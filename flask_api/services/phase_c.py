"""Layer 1 Portfolio Intelligence - Phase C calculation chain.

Completes the capital-planning chain on top of Phase A (assetHealthScore) and
Phase B (stormImpactLevel, riskScore_v2, lossForecast):

    1. insuranceGap     - uncovered forecast loss after the applicable
                          deductible and coverage limit.
    2. capitalROI       - per capital action: avoided loss over the planning
                          horizon divided by action cost.
    3. priorityRanking  - final weighted portfolio ranking.

Formulas (all deterministic; no randomness, clock reads, or network):

  insuranceGap (per property):
      applicableDeductible = namedStormDeductible
                             else windstormDeductible
                             else 0 (+ dataQualityNote)
      coveredAmount = min(coverageLimit, max(0, lossForecast - applicableDeductible))
      insuranceGap  = max(0, lossForecast - coveredAmount - applicableDeductible)
    The deductible is treated as planned self-retention, not part of the gap;
    the gap is forecast loss that exceeds both the deductible and the limit.

  capitalROI (per capital action):
      horizonYears        = min(usefulLifeYears, PLANNING_PERIOD_YEARS)
                            (PLANNING_PERIOD_YEARS when usefulLifeYears missing)
      estimatedAvoidedLoss = lossForecast * estimatedRiskReduction * horizonYears
      capitalROI           = estimatedAvoidedLoss / estimatedCost
    lossForecast is used as the annual expected-loss basis, matching the
    capital_actions meta note ("fraction of annual expected loss avoided").

  priorityRanking (per property, 0-100 weighted score, rank 1 = top):
      priorityScore = 0.35 * riskScore_v2
                    + 0.25 * lossForecastNormalized
                    + 0.15 * (100 - assetHealthScore)
                    + 0.15 * insuranceGapNormalized
                    + 0.10 * capitalUrgency
    Normalized components are the property's share of the portfolio maximum
    (100 * value / max), so a zero stays zero and the best is 100.
    capitalUrgency is the property's best capitalROI normalized the same way.
    Missing components contribute 0, add a dataQualityNote, and downgrade
    confidence — they are never guessed.

Missing inputs (policy not in force for the scope, missing lossForecast,
missing action cost/risk-reduction) produce a null metric value plus
dataQualityNotes, mirroring Phase A/B behavior. The analysis scope
(portfolioId / analysisYear / stormEventId) is honored: policies are filtered
to those in force during the analysis year, and the Phase A/B inputs are
computed under the same scope.
"""

from services.analysis_scope import filter_policies, resolve_analysis_scope
from services.capital_planning import compute_phase_b
from services.data_loader import load_json
from services.layer1_schema import (
    PRIORITY_COMPONENTS,
    make_capital_roi_result,
    make_insurance_gap_result,
    make_priority_ranking_result,
)
from services.portfolio_intelligence import compute_all_asset_health


# Planning horizon for capital ROI, in years. Actions with a shorter useful
# life are credited only for that life.
PLANNING_PERIOD_YEARS = 5

# priorityScore weights; must sum to 1.0.
PRIORITY_WEIGHTS = {
    "riskScore_v2": 0.35,
    "lossForecastNormalized": 0.25,
    "assetHealthInverse": 0.15,
    "insuranceGapNormalized": 0.15,
    "capitalUrgency": 0.10,
}

_CONFIDENCE_RANK = {"Low": 0, "Medium": 1, "High": 2}


def _least_confident(levels):
    present = [lvl for lvl in levels if lvl in _CONFIDENCE_RANK]
    if not present:
        return "Low"
    return min(present, key=lambda lvl: _CONFIDENCE_RANK[lvl])


# ===========================================================================
# 1) insuranceGap
# ===========================================================================

def _select_applicable_deductible(policy):
    """Pick the deductible for a named tornado-bearing windstorm event.

    Selection order: namedStormDeductible -> windstormDeductible -> 0.
    Returns (deductible, deductible_type, notes).
    """
    named = policy.get("namedStormDeductible")
    if named is not None:
        return float(named), "namedStorm", []
    wind = policy.get("windstormDeductible")
    if wind is not None:
        return float(wind), "windstorm", []
    return 0.0, None, [
        "Policy has no namedStorm/windstorm deductible; deductible assumed 0"
    ]


def compute_insurance_gap(property_id, loss_forecast_result, policy):
    """Compute the insuranceGap result for one property.

    Args:
        property_id: the property to describe.
        loss_forecast_result: the property's Phase B lossForecast result.
        policy: the property's insurance policy in force for the scope, or
            None when no policy applies.
    """
    notes = []
    drivers = []

    if policy is None:
        notes.append("No insurance policy in force for the analysis scope")
        return make_insurance_gap_result(
            property_id, insurance_gap=None,
            drivers=["Cannot compute gap without an applicable policy"],
            confidence="Low", data_quality_notes=notes,
        )

    coverage_limit = policy.get("coverageLimit")
    deductible, deductible_type, deductible_notes = _select_applicable_deductible(policy)
    notes.extend(deductible_notes)

    expected_loss = (loss_forecast_result or {}).get("expectedLoss")
    if expected_loss is None:
        notes.append("lossForecast not available; insurance gap not computable")
        return make_insurance_gap_result(
            property_id, insurance_gap=None,
            coverage_limit=coverage_limit,
            applicable_deductible=deductible,
            deductible_type=deductible_type,
            policy_id=policy.get("policyId"),
            drivers=["Cannot compute gap without a loss forecast"],
            confidence="Low", data_quality_notes=notes,
        )

    if coverage_limit is None:
        notes.append("Policy has no coverageLimit; insurance gap not computable")
        return make_insurance_gap_result(
            property_id, insurance_gap=None,
            applicable_deductible=deductible,
            deductible_type=deductible_type,
            policy_id=policy.get("policyId"),
            drivers=["Cannot compute gap without a coverage limit"],
            confidence="Low", data_quality_notes=notes,
        )

    covered = min(float(coverage_limit), max(0.0, expected_loss - deductible))
    gap = max(0.0, expected_loss - covered - deductible)

    if deductible_type:
        drivers.append(
            f"{deductible_type} deductible ${deductible:,.0f} applied"
        )
    if gap > 0:
        drivers.append(
            f"Forecast loss ${expected_loss:,.0f} exceeds coverage by ${gap:,.0f}"
        )
    else:
        drivers.append(
            f"Coverage limit ${float(coverage_limit):,.0f} absorbs the forecast loss "
            f"beyond the deductible"
        )

    confidence = _least_confident([
        (loss_forecast_result or {}).get("confidence", "Low"),
        "Medium" if deductible_notes else "High",
    ])

    return make_insurance_gap_result(
        property_id, insurance_gap=gap,
        coverage_limit=coverage_limit,
        applicable_deductible=deductible,
        covered_amount=covered,
        deductible_type=deductible_type,
        policy_id=policy.get("policyId"),
        drivers=drivers, confidence=confidence, data_quality_notes=notes,
    )


# ===========================================================================
# 2) capitalROI
# ===========================================================================

def compute_capital_roi(action, loss_forecast_result):
    """Compute the capitalROI result for one capital action."""
    property_id = action.get("propertyId", "UNKNOWN")
    action_id = action.get("capitalActionId", "UNKNOWN")
    notes = []
    drivers = []

    cost = action.get("estimatedCost")
    risk_reduction = action.get("estimatedRiskReduction")
    useful_life = action.get("usefulLifeYears")

    if useful_life is None:
        horizon = PLANNING_PERIOD_YEARS
        notes.append(
            f"Missing usefulLifeYears; planning period {PLANNING_PERIOD_YEARS}y assumed"
        )
    else:
        horizon = min(int(useful_life), PLANNING_PERIOD_YEARS)

    expected_loss = (loss_forecast_result or {}).get("expectedLoss")

    missing = []
    if expected_loss is None:
        missing.append("lossForecast")
    if risk_reduction is None:
        missing.append("estimatedRiskReduction")
    if cost is None or (isinstance(cost, (int, float)) and cost <= 0):
        missing.append("estimatedCost")

    if missing:
        notes.append(f"capitalROI not computable; missing: {', '.join(missing)}")
        return make_capital_roi_result(
            property_id, action_id, action.get("actionType"),
            estimated_cost=cost if isinstance(cost, (int, float)) and cost > 0 else None,
            estimated_risk_reduction=risk_reduction,
            horizon_years=horizon,
            drivers=[f"Cannot compute ROI without {', '.join(missing)}"],
            confidence="Low", data_quality_notes=notes,
        )

    avoided_loss = expected_loss * float(risk_reduction) * horizon
    roi = avoided_loss / float(cost)

    drivers.append(
        f"Avoids {float(risk_reduction):.0%} of ${expected_loss:,.0f} annual "
        f"expected loss over {horizon}y"
    )
    drivers.append(f"Cost ${float(cost):,.0f} -> ROI {roi:.2f}x")

    confidence = _least_confident([
        (loss_forecast_result or {}).get("confidence", "Low"),
        "Medium" if useful_life is None else "High",
    ])

    return make_capital_roi_result(
        property_id, action_id, action.get("actionType"),
        estimated_cost=cost,
        estimated_risk_reduction=risk_reduction,
        estimated_avoided_loss=avoided_loss,
        capital_roi=roi,
        horizon_years=horizon,
        drivers=drivers, confidence=confidence, data_quality_notes=notes,
    )


def select_best_capital_action(roi_results):
    """Pick a property's best capital action: highest computable capitalROI.

    Ties break on lower estimatedCost, then capitalActionId, so the choice is
    fully deterministic. Returns None when no action has a computable ROI.
    """
    computable = [r for r in roi_results if r.get("capitalROI") is not None]
    if not computable:
        return None
    return min(
        computable,
        key=lambda r: (
            -r["capitalROI"],
            r.get("estimatedCost") if r.get("estimatedCost") is not None else float("inf"),
            r.get("capitalActionId", ""),
        ),
    )


# ===========================================================================
# 3) priorityRanking
# ===========================================================================

def _share_of_max(value, max_value):
    """Normalize to 0-100 as the share of the portfolio maximum."""
    if value is None or max_value is None or max_value <= 0:
        return None if value is None else 0.0
    return 100.0 * float(value) / float(max_value)


def compute_priority_ranking(property_inputs):
    """Rank the portfolio by weighted priority score.

    Args:
        property_inputs: list of dicts, one per property:
            {propertyId, riskScoreV2, riskConfidence, lossForecast,
             lossConfidence, assetHealthScore, healthConfidence,
             insuranceGap, gapConfidence, bestCapitalROI}

    Returns a list of priorityRanking results sorted by priorityRank
    (score descending, propertyId ascending on ties).
    """
    max_loss = max(
        (p["lossForecast"] for p in property_inputs if p.get("lossForecast") is not None),
        default=None,
    )
    max_gap = max(
        (p["insuranceGap"] for p in property_inputs if p.get("insuranceGap") is not None),
        default=None,
    )
    max_roi = max(
        (p["bestCapitalROI"] for p in property_inputs if p.get("bestCapitalROI") is not None),
        default=None,
    )

    scored = []
    for p in property_inputs:
        notes = []
        confidences = []

        components = {
            "riskScore_v2": p.get("riskScoreV2"),
            "lossForecastNormalized": _share_of_max(p.get("lossForecast"), max_loss),
            "assetHealthInverse": (
                100 - p["assetHealthScore"] if p.get("assetHealthScore") is not None else None
            ),
            "insuranceGapNormalized": _share_of_max(p.get("insuranceGap"), max_gap),
            "capitalUrgency": _share_of_max(p.get("bestCapitalROI"), max_roi),
        }

        for name, value in components.items():
            if value is None:
                notes.append(f"Component {name} unavailable; contributes 0 to priorityScore")
                components[name] = 0.0
                confidences.append("Low")

        for key in ("riskConfidence", "lossConfidence", "healthConfidence", "gapConfidence"):
            if p.get(key):
                confidences.append(p[key])

        score = sum(PRIORITY_WEIGHTS[name] * components[name] for name in PRIORITY_COMPONENTS)
        score = max(0.0, min(100.0, score))

        # Drivers: the two largest weighted contributors, for explainability.
        weighted = sorted(
            ((name, PRIORITY_WEIGHTS[name] * components[name]) for name in PRIORITY_COMPONENTS),
            key=lambda kv: kv[1],
            reverse=True,
        )
        drivers = [
            f"{name} {components[name]:.0f} (contributes {contrib:.1f})"
            for name, contrib in weighted[:2]
        ]

        scored.append(
            {
                "propertyId": p["propertyId"],
                "score": score,
                "components": components,
                "drivers": drivers,
                "confidence": _least_confident(confidences) if confidences else "Low",
                "notes": notes,
            }
        )

    scored.sort(key=lambda s: (-s["score"], s["propertyId"]))

    return [
        make_priority_ranking_result(
            s["propertyId"],
            priority_rank=rank,
            priority_score=s["score"],
            components=s["components"],
            drivers=s["drivers"],
            confidence=s["confidence"],
            data_quality_notes=s["notes"],
        )
        for rank, s in enumerate(scored, start=1)
    ]


# ===========================================================================
# Orchestration: run the full Phase C chain across the portfolio
# ===========================================================================

def compute_phase_c(data_dir=None, scope=None, asset_health_results=None, phase_b=None):
    """Run insuranceGap -> capitalROI -> priorityRanking for every property.

    Args:
        data_dir: optional directory holding the mock data files.
        scope: optional resolved analysis scope; defaults to the demo scope.
        asset_health_results / phase_b: optional precomputed Phase A/B outputs
            (passed by the Portfolio Intelligence API to avoid recomputation);
            computed under the same scope when omitted.

    Returns:
        {
          "insuranceGap":    [one result per property],
          "capitalROI":      [one result per capital action],
          "priorityRanking": [one result per property, rank ascending],
          "bestCapitalActionByProperty": {propertyId: capitalROI result or None},
        }
    """
    if scope is None:
        scope = resolve_analysis_scope()

    if data_dir is None:
        properties_data = load_json("properties.json")
        policies_data = _load_optional("insurance_policies.json")
        actions_data = _load_optional("capital_actions.json")
    else:
        from pathlib import Path

        base = Path(data_dir)
        properties_data = load_json(base / "properties.json")
        policies_data = _load_optional(base / "insurance_policies.json")
        actions_data = _load_optional(base / "capital_actions.json")

    if asset_health_results is None:
        asset_health_results = compute_all_asset_health(data_dir, scope=scope)
    if phase_b is None:
        phase_b = compute_phase_b(data_dir, scope=scope)

    health_by_property = {r["propertyId"]: r for r in asset_health_results}
    risk_by_property = {r["propertyId"]: r for r in phase_b["riskScore_v2"]}
    loss_by_property = {r["propertyId"]: r for r in phase_b["lossForecast"]}

    in_force_policies, _ = filter_policies(policies_data.get("policies", []), scope)
    policy_by_property = {p.get("propertyId"): p for p in in_force_policies}

    actions_by_property = {}
    for action in actions_data.get("capitalActions", []):
        actions_by_property.setdefault(action.get("propertyId"), []).append(action)

    gap_results = []
    roi_results = []
    best_by_property = {}
    ranking_inputs = []

    for prop in properties_data.get("properties", []):
        pid = prop.get("propertyId")
        loss = loss_by_property.get(pid)
        health = health_by_property.get(pid)
        risk = risk_by_property.get(pid)

        gap = compute_insurance_gap(pid, loss, policy_by_property.get(pid))
        gap_results.append(gap)

        property_rois = [
            compute_capital_roi(action, loss)
            for action in sorted(
                actions_by_property.get(pid, []),
                key=lambda a: a.get("capitalActionId", ""),
            )
        ]
        roi_results.extend(property_rois)
        best = select_best_capital_action(property_rois)
        best_by_property[pid] = best
        if not property_rois:
            # No actions defined: priorityRanking notes the missing component.
            pass

        ranking_inputs.append(
            {
                "propertyId": pid,
                "riskScoreV2": (risk or {}).get("score"),
                "riskConfidence": (risk or {}).get("confidence"),
                "lossForecast": (loss or {}).get("expectedLoss"),
                "lossConfidence": (loss or {}).get("confidence"),
                "assetHealthScore": (health or {}).get("score"),
                "healthConfidence": (health or {}).get("confidence"),
                "insuranceGap": gap.get("insuranceGap"),
                "gapConfidence": gap.get("confidence"),
                "bestCapitalROI": (best or {}).get("capitalROI"),
            }
        )

    gap_results.sort(key=lambda r: r["propertyId"])
    roi_results.sort(key=lambda r: (r["propertyId"], r["capitalActionId"]))
    ranking_results = compute_priority_ranking(ranking_inputs)

    return {
        "insuranceGap": gap_results,
        "capitalROI": roi_results,
        "priorityRanking": ranking_results,
        "bestCapitalActionByProperty": best_by_property,
    }


def _load_optional(file_name_or_path):
    try:
        return load_json(file_name_or_path)
    except (FileNotFoundError, ValueError):
        return {}


__all__ = [
    "PLANNING_PERIOD_YEARS",
    "PRIORITY_WEIGHTS",
    "compute_capital_roi",
    "compute_insurance_gap",
    "compute_phase_c",
    "compute_priority_ranking",
    "select_best_capital_action",
]

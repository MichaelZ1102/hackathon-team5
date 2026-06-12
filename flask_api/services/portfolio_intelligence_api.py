"""Read-only aggregation for the Layer 1 Portfolio Intelligence API.

Joins the deterministic Phase A (assetHealthScore) and Phase B
(stormImpactLevel, riskScore_v2, lossForecast) results with property master
data and shapes them into a single read-only response for the frontend and the
(future) AI Copilot.

This module performs NO calculation of its own beyond aggregation/sorting; all
numeric metrics come from portfolio_intelligence (Phase A), capital_planning
(Phase B), and phase_c (insuranceGap, capitalROI, priorityRanking). It does
not touch Layer 2, the JS engine, or riskScore_v1.

The watchList keeps its original (pre-Phase C) deterministic sort for
compatibility; finalPriorityList carries the final Phase C priorityRanking.
"""

from services.analysis_scope import (
    WORK_ORDER_LOOKBACK_MONTHS,
    filter_valuations,
    filter_work_orders,
    resolve_analysis_scope,
    select_storm_event,
    work_order_window,
)
from services.capital_planning import compute_phase_b
from services.data_loader import load_json
from services.phase_c import compute_phase_c
from services.portfolio_intelligence import compute_all_asset_health


CALCULATION_VERSION = "layer1-phaseA+B+C-v3-scoped"
INCLUDED_METRICS = [
    "assetHealthScore",
    "stormImpactLevel",
    "riskScore_v2",
    "lossForecast",
    "insuranceGap",
    "capitalROI",
    "priorityRanking",
]
# All planned Layer 1 metrics are implemented as of Phase C.
MISSING_METRICS = []
DATA_SOURCES_USED = [
    "mock_data/properties.json",
    "mock_data/work_orders.json",
    "mock_data/valuations.json",
    "mock_data/storm_path.json",
    "mock_data/insurance_policies.json",
    "mock_data/capital_actions.json",
]

_CONFIDENCE_RANK = {"Low": 0, "Medium": 1, "High": 2}

# How many properties appear on the temporary watch list.
WATCH_LIST_SIZE = 5

# Storm impact level vocabulary (distance-decayed; "None" = out of range).
STORM_LEVELS = ("Severe", "High", "Medium", "Low", "None")

# "Affected" is a derived rollup, NOT a county boolean: a property counts as
# affected when its distance-decayed storm impact level is Medium or worse.
AFFECTED_LEVELS = ("Severe", "High", "Medium")
AFFECTED_DEFINITION = "stormImpactLevel in (Severe, High, Medium); distance-decayed, not county membership"


def _storm_impact_distribution(property_results):
    """Counts by stormImpactLevel across ALL evaluated properties."""
    counts = {level: 0 for level in STORM_LEVELS}
    for r in property_results:
        level = (r.get("stormImpactLevel") or {}).get("level")
        if level in counts:
            counts[level] += 1
    return counts


def _least_confident(levels):
    """Return the most severe (lowest) confidence among a list of levels."""
    present = [lvl for lvl in levels if lvl in _CONFIDENCE_RANK]
    if not present:
        return "Low"
    return min(present, key=lambda lvl: _CONFIDENCE_RANK[lvl])


def _index_by_property(results):
    return {r["propertyId"]: r for r in results}


def build_portfolio_intelligence(data_dir=None, scope=None):
    """Build the full read-only Portfolio Intelligence payload.

    Args:
        data_dir: optional directory holding the mock data files.
        scope: optional resolved analysis scope (see services.analysis_scope).
            When None, the default scope (FL-DEMO / 2026 / demo storm) is used,
            which selects exactly the data the engines used before scoping.

    Returns a dict with portfolioSummary, propertyIntelligenceResults,
    watchList, and diagnostics (including diagnostics.analysisScope).
    Deterministic for a fixed data set and scope.
    """
    if scope is None:
        scope = resolve_analysis_scope()

    if data_dir is None:
        properties_data = load_json("properties.json")
    else:
        from pathlib import Path

        properties_data = load_json(Path(data_dir) / "properties.json")

    properties = properties_data.get("properties", [])

    asset_health_results = compute_all_asset_health(data_dir, scope=scope)
    asset_health = _index_by_property(asset_health_results)
    phase_b = compute_phase_b(data_dir, scope=scope)
    storm = _index_by_property(phase_b["stormImpactLevel"])
    risk_v2 = _index_by_property(phase_b["riskScore_v2"])
    loss = _index_by_property(phase_b["lossForecast"])

    phase_c = compute_phase_c(
        data_dir, scope=scope, asset_health_results=asset_health_results, phase_b=phase_b
    )
    insurance_gap = _index_by_property(phase_c["insuranceGap"])
    priority = _index_by_property(phase_c["priorityRanking"])
    best_action_by_property = phase_c["bestCapitalActionByProperty"]

    property_results = []
    warnings = []

    for prop in properties:
        pid = prop.get("propertyId")
        ah = asset_health.get(pid)
        si = storm.get(pid)
        rv = risk_v2.get(pid)
        lf = loss.get(pid)
        ig = insurance_gap.get(pid)
        pr = priority.get(pid)

        # Aggregate drivers + data-quality notes across all metrics.
        drivers = []
        notes = []
        confidences = []
        for metric_result in (ah, si, rv, lf, ig, pr):
            if not metric_result:
                continue
            drivers.extend(metric_result.get("drivers", []))
            notes.extend(metric_result.get("dataQualityNotes", []))
            confidences.append(metric_result.get("confidence", "Low"))

        overall_confidence = _least_confident(confidences)

        if lf and lf.get("expectedLoss") is None:
            warnings.append(f"{pid}: lossForecast not computable (missing valuation)")
        if ig and ig.get("insuranceGap") is None:
            warnings.append(f"{pid}: insuranceGap not computable (missing policy or loss forecast)")
        if overall_confidence == "Low":
            warnings.append(f"{pid}: overall confidence is Low")

        property_results.append(
            {
                "propertyId": pid,
                "propertyName": prop.get("name"),
                "county": prop.get("county"),
                "location": {
                    "city": prop.get("city"),
                    "market": prop.get("market"),
                    "lat": prop.get("lat"),
                    "lng": prop.get("lng"),
                },
                # Storm fields surfaced at the top level: EVERY property is
                # evaluated; impact decays with distance ("None" = out of range).
                "distanceToStormPathMiles": (si or {}).get("distanceMiles"),
                "stormImpactScore": (si or {}).get("score"),
                "assetHealthScore": ah,
                "stormImpactLevel": si,
                "riskScore_v2": rv,
                "lossForecast": lf,
                "insuranceGap": ig,
                "priorityRanking": pr,
                "bestCapitalAction": best_action_by_property.get(pid),
                "drivers": drivers,
                "confidence": overall_confidence,
                "dataQualityNotes": notes,
            }
        )

    # Deterministic ordering of the full result set by propertyId.
    property_results.sort(key=lambda r: r["propertyId"])

    watch_list = _build_watch_list(property_results)
    final_priority_list = _build_final_priority_list(property_results)
    summary = _build_summary(property_results, watch_list)
    analysis_scope, scope_warnings = _build_scope_diagnostics(scope, data_dir)
    diagnostics = {
        "calculationVersion": CALCULATION_VERSION,
        "analysisScope": analysis_scope,
        "stormImpactDistribution": _storm_impact_distribution(property_results),
        "affectedDefinition": AFFECTED_DEFINITION,
        "includedMetrics": list(INCLUDED_METRICS),
        "missingMetrics": list(MISSING_METRICS),
        "dataSourcesUsed": list(DATA_SOURCES_USED),
        "warnings": scope_warnings + warnings,
    }

    return {
        "portfolioSummary": summary,
        "propertyIntelligenceResults": property_results,
        "watchList": watch_list,
        "finalPriorityList": final_priority_list,
        "capitalActionResults": phase_c["capitalROI"],
        "diagnostics": diagnostics,
    }


def _build_scope_diagnostics(scope, data_dir=None):
    """Describe how the analysis scope selected the underlying data.

    Returns (analysisScope_dict, scope_warnings). The dict reports the resolved
    scope plus what the scope actually selected (work-order window and counts,
    valuation validity counts, the storm event used); the warnings surface
    scope-level data gaps (unknown ids, records excluded by the scope) so they
    are visible next to the per-property dataQualityNotes.
    """
    if data_dir is None:
        work_orders_data = load_json("work_orders.json")
        try:
            valuations_data = load_json("valuations.json")
        except (FileNotFoundError, ValueError):
            valuations_data = {"valuations": []}
        _, storm_meta, storm_notes = select_storm_event(scope)
    else:
        from pathlib import Path

        base = Path(data_dir)
        work_orders_data = load_json(base / "work_orders.json")
        try:
            valuations_data = load_json(base / "valuations.json")
        except (FileNotFoundError, ValueError):
            valuations_data = {"valuations": []}
        try:
            storm_data = load_json(base / "storm_path.json")
            _, storm_meta, storm_notes = select_storm_event(scope, storm_data=storm_data)
        except (FileNotFoundError, ValueError):
            storm_meta, storm_notes = {}, ["Storm path data unavailable"]

    window_start, window_end = work_order_window(scope)
    wo_in_scope, wo_excluded = filter_work_orders(
        work_orders_data.get("workOrders", []), scope
    )
    valuations_valid, valuations_excluded = filter_valuations(
        valuations_data.get("valuations", []), scope
    )

    warnings = list(scope.get("notes", [])) + list(storm_notes)
    if wo_excluded:
        warnings.append(
            f"{len(wo_excluded)} work order(s) outside the {window_start}..{window_end} "
            "analysis window were excluded from scoring"
        )
    if valuations_excluded:
        excluded_ids = sorted(v.get("propertyId", "?") for v in valuations_excluded)
        warnings.append(
            f"{len(valuations_excluded)} valuation(s) dated after analysisYear "
            f"{scope['analysisYear']} were excluded: {', '.join(excluded_ids)}"
        )

    analysis_scope = {
        "portfolioId": scope["portfolioId"],
        "analysisYear": scope["analysisYear"],
        "stormEventId": storm_meta.get("stormEventId"),
        "requestedStormEventId": scope.get("stormEventId"),
        "workOrderWindow": {
            "start": window_start,
            "end": window_end,
            "lookbackMonths": WORK_ORDER_LOOKBACK_MONTHS,
        },
        "workOrdersInScope": len(wo_in_scope),
        "workOrdersExcluded": len(wo_excluded),
        "valuationsValidForYear": len(valuations_valid),
        "valuationsExcluded": len(valuations_excluded),
        "notes": warnings,
    }
    return analysis_scope, warnings


def _watch_sort_key(result):
    """TEMPORARY deterministic watch-list sort (NOT the final priorityRanking).

    Higher riskScore_v2, then higher lossForecast, then lower assetHealthScore
    (worse health = higher priority). propertyId is the final tie-breaker so the
    order is fully deterministic.
    """
    rv = result.get("riskScore_v2") or {}
    lf = result.get("lossForecast") or {}
    ah = result.get("assetHealthScore") or {}
    risk = rv.get("score", 0)
    expected_loss = lf.get("expectedLoss") or 0
    health = ah.get("score", 100)  # missing health treated as healthy (low priority)
    # Negate the "higher is more urgent" fields; health ascending; id ascending.
    return (-risk, -expected_loss, health, result.get("propertyId", ""))


def _build_watch_list(property_results):
    ordered = sorted(property_results, key=_watch_sort_key)
    watch = []
    for rank, r in enumerate(ordered[:WATCH_LIST_SIZE], start=1):
        rv = r.get("riskScore_v2") or {}
        lf = r.get("lossForecast") or {}
        ah = r.get("assetHealthScore") or {}
        si = r.get("stormImpactLevel") or {}
        watch.append(
            {
                "watchRank": rank,
                "propertyId": r["propertyId"],
                "propertyName": r["propertyName"],
                "county": r["county"],
                "riskScore_v2": rv.get("score"),
                "riskBand": rv.get("band"),
                "stormImpactLevel": si.get("level"),
                "stormImpactScore": si.get("score"),
                "distanceToStormPathMiles": si.get("distanceMiles"),
                "lossForecast": lf.get("expectedLoss"),
                "assetHealthScore": ah.get("score"),
                "drivers": (rv.get("drivers") or [])[:3],
                "confidence": r["confidence"],
                "note": "Compatibility list (pre-Phase C sort); see finalPriorityList for the final priorityRanking.",
            }
        )
    return watch


def _build_final_priority_list(property_results):
    """The final Phase C portfolio ranking, ordered by priorityRank.

    One row per property with the metrics a consumer needs to act on the
    ranking. This list supersedes the temporary watchList sort, which is kept
    unchanged for compatibility.
    """
    ranked = [r for r in property_results if r.get("priorityRanking")]
    ranked.sort(key=lambda r: r["priorityRanking"]["priorityRank"])
    rows = []
    for r in ranked:
        pr = r["priorityRanking"]
        rv = r.get("riskScore_v2") or {}
        lf = r.get("lossForecast") or {}
        ah = r.get("assetHealthScore") or {}
        ig = r.get("insuranceGap") or {}
        si = r.get("stormImpactLevel") or {}
        best = r.get("bestCapitalAction")
        rows.append(
            {
                "priorityRank": pr["priorityRank"],
                "priorityScore": pr["priorityScore"],
                "propertyId": r["propertyId"],
                "propertyName": r["propertyName"],
                "county": r["county"],
                "stormImpactLevel": si.get("level"),
                "stormImpactScore": si.get("score"),
                "distanceToStormPathMiles": si.get("distanceMiles"),
                "riskScore_v2": rv.get("score"),
                "lossForecast": lf.get("expectedLoss"),
                "assetHealthScore": ah.get("score"),
                "insuranceGap": ig.get("insuranceGap"),
                "bestCapitalAction": (
                    {
                        "capitalActionId": best.get("capitalActionId"),
                        "actionType": best.get("actionType"),
                        "estimatedCost": best.get("estimatedCost"),
                        "capitalROI": best.get("capitalROI"),
                    }
                    if best
                    else None
                ),
                "rankingDrivers": pr.get("drivers", []),
                "confidence": pr.get("confidence"),
            }
        )
    return rows


def _build_summary(property_results, watch_list):
    total = len(property_results)

    severe_count = sum(
        1
        for r in property_results
        if (r.get("stormImpactLevel") or {}).get("level") == "Severe"
    )
    affected_count = sum(
        1
        for r in property_results
        if (r.get("stormImpactLevel") or {}).get("level") in AFFECTED_LEVELS
    )
    high_risk_count = sum(
        1
        for r in property_results
        if (r.get("riskScore_v2") or {}).get("band") == "High"
    )

    losses = [
        (r.get("lossForecast") or {}).get("expectedLoss")
        for r in property_results
    ]
    total_loss = round(sum(v for v in losses if v is not None), 2)

    health_scores = [
        (r.get("assetHealthScore") or {}).get("score")
        for r in property_results
    ]
    health_scores = [s for s in health_scores if s is not None]
    avg_health = round(sum(health_scores) / len(health_scores), 1) if health_scores else None

    # topRiskDrivers: drivers from the watch-list properties' riskScore_v2,
    # de-duplicated while preserving order (most-urgent first).
    top_drivers = []
    seen = set()
    for w in watch_list:
        for d in w.get("drivers", []):
            if d not in seen:
                seen.add(d)
                top_drivers.append(d)

    gaps = [
        (r.get("insuranceGap") or {}).get("insuranceGap")
        for r in property_results
    ]
    total_gap = round(sum(v for v in gaps if v is not None), 2)

    top_priority = min(
        (r for r in property_results if r.get("priorityRanking")),
        key=lambda r: r["priorityRanking"]["priorityRank"],
        default=None,
    )

    return {
        "totalProperties": total,
        "severeImpactCount": severe_count,
        # Derived rollup, not a county boolean — see AFFECTED_DEFINITION.
        "affectedPropertyCount": affected_count,
        "highRiskCount": high_risk_count,
        "totalLossForecast": total_loss,
        "averageAssetHealthScore": avg_health,
        "totalInsuranceGap": total_gap,
        "topPriorityPropertyId": top_priority["propertyId"] if top_priority else None,
        "topRiskDrivers": top_drivers[:6],
    }


__all__ = ["build_portfolio_intelligence", "CALCULATION_VERSION"]

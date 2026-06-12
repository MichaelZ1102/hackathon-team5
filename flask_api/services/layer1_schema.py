"""Layer 1 Portfolio Intelligence output contract.

This module defines the shape of a single Layer 1 metric result so that other
layers (the AI Copilot context builder, the frontend, and future Phase B
calculations) can rely on a stable, inspectable structure.

The contract is intentionally plain Python (no third-party schema library) so it
stays importable with stdlib only and easy to validate in tests.

Layer 1 result fields (per property, per metric):
    propertyId        str   - the property this result describes
    metric            str   - the metric name, e.g. "assetHealthScore"
    score             int   - 0-100 integer score
    band              str   - qualitative band derived from score
    drivers           list  - short strings explaining what hurt/helped the score
    confidence        str   - "High" | "Medium" | "Low"
    dataQualityNotes  list  - strings describing missing/sparse data; [] if complete
"""

# ---------------------------------------------------------------------------
# Allowed enumerations
# ---------------------------------------------------------------------------

ASSET_HEALTH_METRIC = "assetHealthScore"

# Score bands for assetHealthScore (from design doc section 6).
# Each entry is (inclusive_min_score, band_label); evaluated high to low.
ASSET_HEALTH_BANDS = (
    (80, "Strong"),
    (60, "Stable"),
    (40, "Concerning"),
    (0, "Poor"),
)

VALID_BANDS = tuple(label for _, label in ASSET_HEALTH_BANDS)

CONFIDENCE_LEVELS = ("High", "Medium", "Low")

# Required keys every Layer 1 result must expose, mapped to their expected type.
REQUIRED_FIELDS = {
    "propertyId": str,
    "metric": str,
    "score": int,
    "band": str,
    "drivers": list,
    "confidence": str,
    "dataQualityNotes": list,
}


def band_for_score(score):
    """Return the qualitative band label for a 0-100 score."""
    for minimum, label in ASSET_HEALTH_BANDS:
        if score >= minimum:
            return label
    # Scores are clamped to >= 0 upstream, so this is a defensive fallback.
    return "Poor"


def make_layer1_result(
    property_id,
    metric,
    score,
    drivers=None,
    confidence="High",
    data_quality_notes=None,
):
    """Build a Layer 1 result dict that conforms to the contract.

    Centralizing construction here guarantees band is always consistent with
    score and that every result carries all required keys with correct types.
    """
    score = int(score)
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}, got {confidence!r}")

    return {
        "propertyId": str(property_id),
        "metric": str(metric),
        "score": score,
        "band": band_for_score(score),
        "drivers": list(drivers or []),
        "confidence": confidence,
        "dataQualityNotes": list(data_quality_notes or []),
    }


def validate_layer1_result(result):
    """Validate a single Layer 1 result against the contract.

    Returns a list of human-readable problems; an empty list means valid.
    Kept as a pure function so tests and callers can assert on it directly.
    """
    problems = []

    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in result:
            problems.append(f"missing required field: {field}")
            continue
        if not isinstance(result[field], expected_type):
            problems.append(
                f"field {field} should be {expected_type.__name__}, "
                f"got {type(result[field]).__name__}"
            )

    # Note: bool is a subclass of int in Python; reject it for the score field.
    if isinstance(result.get("score"), bool):
        problems.append("field score should be int, got bool")

    score = result.get("score")
    if isinstance(score, int) and not isinstance(score, bool):
        if not (0 <= score <= 100):
            problems.append(f"score out of range 0-100: {score}")
        if result.get("band") != band_for_score(score):
            problems.append(
                f"band {result.get('band')!r} does not match score {score} "
                f"(expected {band_for_score(score)!r})"
            )

    if result.get("band") not in VALID_BANDS:
        problems.append(f"band {result.get('band')!r} not in {VALID_BANDS}")

    if result.get("confidence") not in CONFIDENCE_LEVELS:
        problems.append(f"confidence {result.get('confidence')!r} not in {CONFIDENCE_LEVELS}")

    for note in result.get("dataQualityNotes", []) or []:
        if not isinstance(note, str):
            problems.append("dataQualityNotes must contain only strings")
            break

    for driver in result.get("drivers", []) or []:
        if not isinstance(driver, str):
            problems.append("drivers must contain only strings")
            break

    return problems


# ===========================================================================
# Phase B output contracts (additive)
# ---------------------------------------------------------------------------
# Phase B introduces three metrics whose natural output shapes differ from the
# Phase A score(0-100)+band shape, so each gets its own builder. The Phase A
# functions above are intentionally left unchanged.
#
# Shared envelope fields on every Phase B result:
#     propertyId        str   - the property this result describes
#     metric            str   - the metric name
#     drivers           list  - short strings explaining the result
#     confidence        str   - "High" | "Medium" | "Low"
#     dataQualityNotes  list  - strings describing missing/sparse data; [] if complete
# plus metric-specific payload fields documented per builder.
# ===========================================================================

STORM_IMPACT_METRIC = "stormImpactLevel"
RISK_SCORE_V2_METRIC = "riskScore_v2"
LOSS_FORECAST_METRIC = "lossForecast"

# stormImpactLevel categorical levels, ordered low -> high severity.
# "None" means the property is outside meaningful storm impact range; every
# portfolio property is still evaluated and carries a level.
STORM_IMPACT_LEVELS = ("None", "Low", "Medium", "High", "Severe")

# stormImpactLevel derived from stormImpactScore (0-100), evaluated high to low.
# Score 0 (or no computable distance) maps to "None".
STORM_IMPACT_SCORE_BANDS = (
    (80, "Severe"),
    (60, "High"),
    (30, "Medium"),
    (1, "Low"),
    (0, "None"),
)


def storm_level_for_score(score):
    """Return the stormImpactLevel label for a 0-100 stormImpactScore."""
    for minimum, label in STORM_IMPACT_SCORE_BANDS:
        if score >= minimum:
            return label
    return "None"

# riskScore_v2 bands (design doc section 6: 0-39 Low, 40-69 Medium, 70-100 High).
# Stored high-to-low for evaluation, mirroring ASSET_HEALTH_BANDS' style.
RISK_V2_BANDS = (
    (70, "High"),
    (40, "Medium"),
    (0, "Low"),
)
VALID_RISK_V2_BANDS = tuple(label for _, label in RISK_V2_BANDS)

# The five riskScore_v2 components (design doc section 6 weighted formula).
RISK_V2_COMPONENTS = (
    "stormExposure",
    "buildingVulnerability",
    "maintenanceRisk",
    "locationHazard",
    "assetValue",
)


def _validate_envelope(result, metric_name, problems):
    """Shared validation for the Phase B envelope fields."""
    if result.get("propertyId") is None or not isinstance(result.get("propertyId"), str):
        problems.append("propertyId must be a non-null str")
    if result.get("metric") != metric_name:
        problems.append(f"metric should be {metric_name!r}, got {result.get('metric')!r}")
    if not isinstance(result.get("drivers"), list):
        problems.append("drivers must be a list")
    elif any(not isinstance(d, str) for d in result["drivers"]):
        problems.append("drivers must contain only strings")
    if result.get("confidence") not in CONFIDENCE_LEVELS:
        problems.append(f"confidence {result.get('confidence')!r} not in {CONFIDENCE_LEVELS}")
    notes = result.get("dataQualityNotes")
    if not isinstance(notes, list):
        problems.append("dataQualityNotes must be a list")
    elif any(not isinstance(n, str) for n in notes):
        problems.append("dataQualityNotes must contain only strings")


def risk_v2_band_for_score(score):
    """Return the qualitative band label for a 0-100 riskScore_v2."""
    for minimum, label in RISK_V2_BANDS:
        if score >= minimum:
            return label
    return "Low"


def make_storm_impact_result(
    property_id,
    level,
    score=0,
    distance_miles=None,
    drivers=None,
    confidence="High",
    data_quality_notes=None,
):
    """Build a stormImpactLevel result.

    Payload fields beyond the envelope:
        level            str    - one of STORM_IMPACT_LEVELS
        score            int    - 0-100 stormImpactScore (distance-decayed,
                                  hazard-adjusted); 0 = outside impact range
        distanceMiles    float  - distance from property to the projected storm
                                  path (None if not computable)
    """
    if level not in STORM_IMPACT_LEVELS:
        raise ValueError(f"level must be one of {STORM_IMPACT_LEVELS}, got {level!r}")
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}, got {confidence!r}")
    score = max(0, min(100, int(round(score))))
    if level != storm_level_for_score(score):
        raise ValueError(
            f"level {level!r} does not match score {score} "
            f"(expected {storm_level_for_score(score)!r})"
        )
    return {
        "propertyId": str(property_id),
        "metric": STORM_IMPACT_METRIC,
        "level": level,
        "score": score,
        "distanceMiles": (round(float(distance_miles), 1) if distance_miles is not None else None),
        "drivers": list(drivers or []),
        "confidence": confidence,
        "dataQualityNotes": list(data_quality_notes or []),
    }


def validate_storm_impact_result(result):
    problems = []
    _validate_envelope(result, STORM_IMPACT_METRIC, problems)
    if result.get("level") not in STORM_IMPACT_LEVELS:
        problems.append(f"level {result.get('level')!r} not in {STORM_IMPACT_LEVELS}")
    score = result.get("score")
    if isinstance(score, bool) or not isinstance(score, int):
        problems.append("score must be int")
    elif not (0 <= score <= 100):
        problems.append(f"score out of range 0-100: {score}")
    elif result.get("level") != storm_level_for_score(score):
        problems.append(
            f"level {result.get('level')!r} does not match score {score} "
            f"(expected {storm_level_for_score(score)!r})"
        )
    dist = result.get("distanceMiles")
    if dist is not None and not isinstance(dist, (int, float)):
        problems.append("distanceMiles must be a number or None")
    return problems


def make_risk_v2_result(
    property_id,
    score,
    components,
    drivers=None,
    confidence="High",
    data_quality_notes=None,
):
    """Build a riskScore_v2 result.

    Payload fields beyond the envelope:
        score        int   - 0-100 weighted risk score (kept SEPARATE from the
                             existing Layer 2 riskScore / riskScore_v1)
        band         str   - Low / Medium / High
        components   dict   - the five RISK_V2_COMPONENTS sub-scores (0-100 each)
    """
    score = int(round(score))
    score = max(0, min(100, score))
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}, got {confidence!r}")
    return {
        "propertyId": str(property_id),
        "metric": RISK_SCORE_V2_METRIC,
        "score": score,
        "band": risk_v2_band_for_score(score),
        "components": {k: int(round(components.get(k, 0))) for k in RISK_V2_COMPONENTS},
        "drivers": list(drivers or []),
        "confidence": confidence,
        "dataQualityNotes": list(data_quality_notes or []),
    }


def validate_risk_v2_result(result):
    problems = []
    _validate_envelope(result, RISK_SCORE_V2_METRIC, problems)
    score = result.get("score")
    if isinstance(score, bool) or not isinstance(score, int):
        problems.append("score must be int")
    elif not (0 <= score <= 100):
        problems.append(f"score out of range 0-100: {score}")
    elif result.get("band") != risk_v2_band_for_score(score):
        problems.append(
            f"band {result.get('band')!r} does not match score {score} "
            f"(expected {risk_v2_band_for_score(score)!r})"
        )
    comps = result.get("components")
    if not isinstance(comps, dict):
        problems.append("components must be a dict")
    else:
        for key in RISK_V2_COMPONENTS:
            if key not in comps:
                problems.append(f"components missing {key}")
    return problems


def make_loss_forecast_result(
    property_id,
    expected_loss,
    damage_ratio,
    multipliers,
    replacement_value=None,
    drivers=None,
    confidence="High",
    data_quality_notes=None,
):
    """Build a lossForecast result.

    Payload fields beyond the envelope:
        expectedLoss      float/None - forecast dollar loss (None if not computable)
        damageRatio       float      - base damage ratio from storm impact level
        multipliers       dict       - {"buildingVulnerability": x, "maintenanceCondition": y}
        replacementValue  float/None - the valuation input used
    """
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}, got {confidence!r}")
    return {
        "propertyId": str(property_id),
        "metric": LOSS_FORECAST_METRIC,
        "expectedLoss": (round(float(expected_loss), 2) if expected_loss is not None else None),
        "damageRatio": round(float(damage_ratio), 4),
        "multipliers": {k: round(float(v), 3) for k, v in (multipliers or {}).items()},
        "replacementValue": (float(replacement_value) if replacement_value is not None else None),
        "drivers": list(drivers or []),
        "confidence": confidence,
        "dataQualityNotes": list(data_quality_notes or []),
    }


def validate_loss_forecast_result(result):
    problems = []
    _validate_envelope(result, LOSS_FORECAST_METRIC, problems)
    loss = result.get("expectedLoss")
    if loss is not None and not isinstance(loss, (int, float)):
        problems.append("expectedLoss must be a number or None")
    if loss is not None and loss < 0:
        problems.append(f"expectedLoss must be >= 0, got {loss}")
    if not isinstance(result.get("damageRatio"), (int, float)):
        problems.append("damageRatio must be a number")
    if not isinstance(result.get("multipliers"), dict):
        problems.append("multipliers must be a dict")
    return problems

# ===========================================================================
# Phase C output contracts (additive)
# ---------------------------------------------------------------------------
# Phase C completes the capital-planning chain: insuranceGap, capitalROI, and
# priorityRanking. Same envelope as Phase B (propertyId, metric, drivers,
# confidence, dataQualityNotes) plus metric-specific payload fields.
# ===========================================================================

INSURANCE_GAP_METRIC = "insuranceGap"
CAPITAL_ROI_METRIC = "capitalROI"
PRIORITY_RANKING_METRIC = "priorityRanking"

# The five weighted priorityScore components (Phase C ranking formula).
PRIORITY_COMPONENTS = (
    "riskScore_v2",
    "lossForecastNormalized",
    "assetHealthInverse",
    "insuranceGapNormalized",
    "capitalUrgency",
)


def make_insurance_gap_result(
    property_id,
    insurance_gap,
    coverage_limit=None,
    applicable_deductible=None,
    covered_amount=None,
    deductible_type=None,
    policy_id=None,
    drivers=None,
    confidence="High",
    data_quality_notes=None,
):
    """Build an insuranceGap result.

    Payload fields beyond the envelope:
        insuranceGap          float/None - uncovered forecast loss in USD
                                           (None if not computable)
        coverageLimit         float/None - policy coverage limit used
        applicableDeductible  float/None - deductible applied for the storm peril
        coveredAmount         float/None - eligible covered amount
        deductibleType        str/None   - which deductible was applied
                                           ("namedStorm" | "windstorm" | None)
        policyId              str/None   - source policy
    """
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}, got {confidence!r}")
    return {
        "propertyId": str(property_id),
        "metric": INSURANCE_GAP_METRIC,
        "insuranceGap": (round(float(insurance_gap), 2) if insurance_gap is not None else None),
        "coverageLimit": (float(coverage_limit) if coverage_limit is not None else None),
        "applicableDeductible": (
            float(applicable_deductible) if applicable_deductible is not None else None
        ),
        "coveredAmount": (round(float(covered_amount), 2) if covered_amount is not None else None),
        "deductibleType": deductible_type,
        "policyId": policy_id,
        "drivers": list(drivers or []),
        "confidence": confidence,
        "dataQualityNotes": list(data_quality_notes or []),
    }


def validate_insurance_gap_result(result):
    problems = []
    _validate_envelope(result, INSURANCE_GAP_METRIC, problems)
    for field in ("insuranceGap", "coverageLimit", "applicableDeductible", "coveredAmount"):
        value = result.get(field)
        if value is not None and not isinstance(value, (int, float)):
            problems.append(f"{field} must be a number or None")
    gap = result.get("insuranceGap")
    if isinstance(gap, (int, float)) and gap < 0:
        problems.append(f"insuranceGap must be >= 0, got {gap}")
    return problems


def make_capital_roi_result(
    property_id,
    capital_action_id,
    action_type,
    estimated_cost=None,
    estimated_risk_reduction=None,
    estimated_avoided_loss=None,
    capital_roi=None,
    horizon_years=None,
    drivers=None,
    confidence="High",
    data_quality_notes=None,
):
    """Build a capitalROI result (one per capital action, not per property).

    Payload fields beyond the envelope:
        capitalActionId         str        - the action this result describes
        actionType              str/None   - e.g. "Roof Replacement"
        estimatedCost           float/None - action cost in USD
        estimatedRiskReduction  float/None - 0-1 fraction of annual loss avoided
        estimatedAvoidedLoss    float/None - USD avoided over the horizon
        capitalROI              float/None - avoidedLoss / cost (None if not computable)
        horizonYears            int/None   - planning horizon used
    """
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}, got {confidence!r}")
    return {
        "propertyId": str(property_id),
        "metric": CAPITAL_ROI_METRIC,
        "capitalActionId": str(capital_action_id),
        "actionType": action_type,
        "estimatedCost": (float(estimated_cost) if estimated_cost is not None else None),
        "estimatedRiskReduction": (
            float(estimated_risk_reduction) if estimated_risk_reduction is not None else None
        ),
        "estimatedAvoidedLoss": (
            round(float(estimated_avoided_loss), 2) if estimated_avoided_loss is not None else None
        ),
        "capitalROI": (round(float(capital_roi), 2) if capital_roi is not None else None),
        "horizonYears": (int(horizon_years) if horizon_years is not None else None),
        "drivers": list(drivers or []),
        "confidence": confidence,
        "dataQualityNotes": list(data_quality_notes or []),
    }


def validate_capital_roi_result(result):
    problems = []
    _validate_envelope(result, CAPITAL_ROI_METRIC, problems)
    if not isinstance(result.get("capitalActionId"), str):
        problems.append("capitalActionId must be a str")
    for field in ("estimatedCost", "estimatedRiskReduction", "estimatedAvoidedLoss", "capitalROI"):
        value = result.get(field)
        if value is not None and not isinstance(value, (int, float)):
            problems.append(f"{field} must be a number or None")
    roi = result.get("capitalROI")
    if isinstance(roi, (int, float)) and roi < 0:
        problems.append(f"capitalROI must be >= 0, got {roi}")
    return problems


def make_priority_ranking_result(
    property_id,
    priority_rank,
    priority_score,
    components=None,
    drivers=None,
    confidence="High",
    data_quality_notes=None,
):
    """Build a priorityRanking result.

    Payload fields beyond the envelope:
        priorityRank   int   - 1 = highest priority across the portfolio
        priorityScore  float - 0-100 weighted score the rank derives from
        components     dict  - the PRIORITY_COMPONENTS sub-scores (0-100 each)
    """
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}, got {confidence!r}")
    return {
        "propertyId": str(property_id),
        "metric": PRIORITY_RANKING_METRIC,
        "priorityRank": int(priority_rank),
        "priorityScore": round(float(priority_score), 1),
        "components": {
            k: round(float((components or {}).get(k, 0)), 1) for k in PRIORITY_COMPONENTS
        },
        "drivers": list(drivers or []),
        "confidence": confidence,
        "dataQualityNotes": list(data_quality_notes or []),
    }


def validate_priority_ranking_result(result):
    problems = []
    _validate_envelope(result, PRIORITY_RANKING_METRIC, problems)
    rank = result.get("priorityRank")
    if isinstance(rank, bool) or not isinstance(rank, int):
        problems.append("priorityRank must be int")
    elif rank < 1:
        problems.append(f"priorityRank must be >= 1, got {rank}")
    score = result.get("priorityScore")
    if not isinstance(score, (int, float)):
        problems.append("priorityScore must be a number")
    elif not (0 <= score <= 100):
        problems.append(f"priorityScore out of range 0-100: {score}")
    comps = result.get("components")
    if not isinstance(comps, dict):
        problems.append("components must be a dict")
    else:
        for key in PRIORITY_COMPONENTS:
            if key not in comps:
                problems.append(f"components missing {key}")
    return problems

"""Layer 1 Portfolio Intelligence - Phase A calculation.

Implements the deterministic ``assetHealthScore`` metric described in section 6
of the design doc:

    assetHealthScore =
        100
        - agePenalty
        - roofConditionPenalty
        - openCriticalWorkOrderPenalty
        - recurringIssuePenalty
        - recentRepairCostPenalty

Bands: 80-100 Strong, 60-79 Stable, 40-59 Concerning, 0-39 Poor.

This module is deterministic (no randomness, no clock reads, no network) and
uses ONLY the existing ``properties.json`` and ``work_orders.json`` mock data.
It does not touch or depend on the existing ``riskScore`` logic.

All penalty thresholds are explicit named constants so they are inspectable and
tunable without hunting through the code.
"""

from collections import defaultdict

from services.data_loader import load_json
from services.layer1_schema import (
    ASSET_HEALTH_METRIC,
    band_for_score,
    make_layer1_result,
)


# ---------------------------------------------------------------------------
# Deterministic reference year
# ---------------------------------------------------------------------------
# Fixed so building age is reproducible regardless of when the code runs.
# Matches the demo's storm-scenario timeframe (mid-2026).
ANALYSIS_YEAR = 2026


# ---------------------------------------------------------------------------
# Penalty constants (tunable). Higher penalty = worse asset health.
# ---------------------------------------------------------------------------

# 1) agePenalty - building age (from yearBuilt) plus average HVAC age.
BUILDING_AGE_PENALTIES = (
    (40, 12),  # >= 40 years old
    (25, 8),   # >= 25 years old
    (15, 4),   # >= 15 years old
)
HVAC_AGE_PENALTIES = (
    (12, 8),   # >= 12 years
    (10, 5),   # >= 10 years
    (7, 2),    # >= 7 years
)

# 2) roofConditionPenalty - roof age tier plus exterior condition.
ROOF_AGE_PENALTIES = (
    (20, 18),  # >= 20 years
    (15, 12),  # >= 15 years
    (10, 6),   # >= 10 years
)
EXTERIOR_CONDITION_PENALTIES = {
    "Poor": 12,
    "Fair": 6,
    "Good": 0,
    "Excellent": 0,
}

# 3) openCriticalWorkOrderPenalty - unresolved work orders in critical systems.
CRITICAL_WORK_ORDER_CATEGORIES = frozenset(
    {"Roof Leak", "Water Intrusion", "HVAC Exterior Unit", "Exterior Siding"}
)
# A work order counts as "open" when its status is not one of these.
CLOSED_WORK_ORDER_STATUSES = frozenset({"Completed", "Closed", "Resolved"})
OPEN_CRITICAL_WORK_ORDER_PENALTY_EACH = 8
OPEN_CRITICAL_WORK_ORDER_PENALTY_CAP = 24

# 4) recurringIssuePenalty - work orders flagged as repeat issues.
RECURRING_ISSUE_PENALTY_EACH = 5
RECURRING_ISSUE_PENALTY_CAP = 20

# 5) recentRepairCostPenalty - total repair spend across the lookback window.
#    (work_orders.json already covers a 12-month lookback, so all rows are recent)
RECENT_REPAIR_COST_PENALTIES = (
    (8000, 15),
    (5000, 10),
    (2500, 5),
)

# ---------------------------------------------------------------------------
# Confidence / data-quality constants
# ---------------------------------------------------------------------------

# Property fields required to compute the score without guessing.
REQUIRED_PROPERTY_FIELDS = ("yearBuilt", "roofAgeYears", "hvacAvgAgeYears", "exteriorCondition")

# At least this many work orders for full-confidence maintenance signal.
MIN_WORK_ORDERS_FOR_HIGH_CONFIDENCE = 3

_CONFIDENCE_RANK = {"Low": 0, "Medium": 1, "High": 2}


def _tiered_penalty(value, tiers):
    """Return the penalty for the first (threshold, penalty) tier that ``value`` meets.

    ``tiers`` must be ordered from highest threshold to lowest. Returns 0 if no
    tier is met or ``value`` is None.
    """
    if value is None:
        return 0
    for threshold, penalty in tiers:
        if value >= threshold:
            return penalty
    return 0


def _least_confident(*levels):
    """Return the most severe (lowest) confidence level among the arguments."""
    return min(levels, key=lambda level: _CONFIDENCE_RANK[level])


def compute_asset_health_score(property_dict, work_orders_for_property):
    """Compute the assetHealthScore Layer 1 result for one property.

    Args:
        property_dict: a property record from properties.json.
        work_orders_for_property: list of that property's work order records.

    Returns:
        A Layer 1 result dict (see layer1_schema.make_layer1_result).

    Missing fields are never guessed: the corresponding penalty is treated as 0,
    the gap is recorded in dataQualityNotes, and confidence is downgraded.
    """
    work_orders = list(work_orders_for_property or [])
    data_quality_notes = []
    drivers = []

    property_id = property_dict.get("propertyId", "UNKNOWN")

    # --- record missing required property fields (do not guess values) ---
    missing_fields = [
        field for field in REQUIRED_PROPERTY_FIELDS if property_dict.get(field) is None
    ]
    for field in missing_fields:
        data_quality_notes.append(f"Missing property field: {field}")

    year_built = property_dict.get("yearBuilt")
    roof_age = property_dict.get("roofAgeYears")
    hvac_age = property_dict.get("hvacAvgAgeYears")
    exterior_condition = property_dict.get("exteriorCondition")

    # --- 1) agePenalty (building age + HVAC age) ---
    building_age = (ANALYSIS_YEAR - year_built) if year_built is not None else None
    building_age_penalty = _tiered_penalty(building_age, BUILDING_AGE_PENALTIES)
    hvac_age_penalty = _tiered_penalty(hvac_age, HVAC_AGE_PENALTIES)
    age_penalty = building_age_penalty + hvac_age_penalty
    if building_age_penalty:
        drivers.append(f"Building age ~{building_age} years (-{building_age_penalty})")
    if hvac_age_penalty:
        drivers.append(f"Average HVAC age {hvac_age} years (-{hvac_age_penalty})")

    # --- 2) roofConditionPenalty (roof age + exterior condition) ---
    roof_age_penalty = _tiered_penalty(roof_age, ROOF_AGE_PENALTIES)
    if exterior_condition is None:
        condition_penalty = 0
    else:
        condition_penalty = EXTERIOR_CONDITION_PENALTIES.get(exterior_condition, 0)
        if exterior_condition not in EXTERIOR_CONDITION_PENALTIES:
            data_quality_notes.append(
                f"Unrecognized exteriorCondition value: {exterior_condition!r}"
            )
    roof_condition_penalty = roof_age_penalty + condition_penalty
    if roof_age_penalty:
        drivers.append(f"Roof age {roof_age} years (-{roof_age_penalty})")
    if condition_penalty:
        drivers.append(f"Exterior condition {exterior_condition} (-{condition_penalty})")

    # --- 3) openCriticalWorkOrderPenalty ---
    open_critical_count = sum(
        1
        for wo in work_orders
        if wo.get("category") in CRITICAL_WORK_ORDER_CATEGORIES
        and wo.get("status") not in CLOSED_WORK_ORDER_STATUSES
    )
    open_critical_penalty = min(
        open_critical_count * OPEN_CRITICAL_WORK_ORDER_PENALTY_EACH,
        OPEN_CRITICAL_WORK_ORDER_PENALTY_CAP,
    )
    if open_critical_penalty:
        drivers.append(
            f"{open_critical_count} open critical work order(s) (-{open_critical_penalty})"
        )

    # --- 4) recurringIssuePenalty ---
    recurring_count = sum(1 for wo in work_orders if wo.get("isRepeatIssue"))
    recurring_penalty = min(
        recurring_count * RECURRING_ISSUE_PENALTY_EACH,
        RECURRING_ISSUE_PENALTY_CAP,
    )
    if recurring_penalty:
        drivers.append(
            f"{recurring_count} recurring maintenance issue(s) (-{recurring_penalty})"
        )

    # --- 5) recentRepairCostPenalty ---
    total_repair_cost = sum(wo.get("cost", 0) or 0 for wo in work_orders)
    recent_repair_cost_penalty = _tiered_penalty(
        total_repair_cost, RECENT_REPAIR_COST_PENALTIES
    )
    if recent_repair_cost_penalty:
        drivers.append(
            f"Recent repair spend ${total_repair_cost:,} (-{recent_repair_cost_penalty})"
        )

    # --- combine ---
    total_penalty = (
        age_penalty
        + roof_condition_penalty
        + open_critical_penalty
        + recurring_penalty
        + recent_repair_cost_penalty
    )
    score = max(0, min(100, 100 - total_penalty))

    # A positive note when nothing dragged the score down.
    if not drivers:
        drivers.append("No significant age, condition, or maintenance penalties")

    # --- confidence ---
    confidence = "High"
    if len(missing_fields) >= 2:
        confidence = _least_confident(confidence, "Low")
    elif len(missing_fields) == 1:
        confidence = _least_confident(confidence, "Medium")

    if not work_orders:
        # No maintenance history at all: low confidence in a maintenance-derived score.
        confidence = _least_confident(confidence, "Low")
        data_quality_notes.append("No work order history available for this property")
    elif len(work_orders) < MIN_WORK_ORDERS_FOR_HIGH_CONFIDENCE:
        confidence = _least_confident(confidence, "Medium")
        data_quality_notes.append(
            f"Sparse work order history ({len(work_orders)} record(s))"
        )

    return make_layer1_result(
        property_id=property_id,
        metric=ASSET_HEALTH_METRIC,
        score=score,
        drivers=drivers,
        confidence=confidence,
        data_quality_notes=data_quality_notes,
    )


def _group_work_orders_by_property(work_orders):
    grouped = defaultdict(list)
    for work_order in work_orders:
        grouped[work_order.get("propertyId")].append(work_order)
    return grouped


def compute_all_asset_health(data_dir=None):
    """Compute assetHealthScore for every property in the mock data.

    Args:
        data_dir: optional directory containing properties.json and
            work_orders.json. When None, the default mock_data dir is used
            (via data_loader.load_json).

    Returns:
        A list of Layer 1 result dicts, ordered worst-health-first then by
        propertyId for deterministic output.
    """
    if data_dir is None:
        properties_data = load_json("properties.json")
        work_orders_data = load_json("work_orders.json")
    else:
        from pathlib import Path

        base = Path(data_dir)
        properties_data = load_json(base / "properties.json")
        work_orders_data = load_json(base / "work_orders.json")

    work_orders_by_property = _group_work_orders_by_property(
        work_orders_data.get("workOrders", [])
    )

    results = [
        compute_asset_health_score(
            property_item,
            work_orders_by_property.get(property_item.get("propertyId"), []),
        )
        for property_item in properties_data.get("properties", [])
    ]

    results.sort(key=lambda item: (item["score"], item["propertyId"]))
    return results


__all__ = [
    "ANALYSIS_YEAR",
    "compute_asset_health_score",
    "compute_all_asset_health",
    "band_for_score",
]

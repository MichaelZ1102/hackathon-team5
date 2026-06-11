"""Layer 1 Portfolio Intelligence - Phase B calculation chain.

Implements the first capital-planning calculation chain from the design doc
(section 6), in dependency order:

    1. stormImpactLevel  - distance from property to projected storm path,
                           adjusted by wind/rainfall/flood exposure.
    2. riskScore_v2      - weighted 5-factor risk score (storm exposure,
                           building vulnerability, maintenance risk, location
                           hazard, asset value). Kept SEPARATE from the existing
                           Layer 2 riskScore (a.k.a. riskScore_v1).
    3. lossForecast      - replacementValue * damageRatio * vulnerability
                           multiplier * maintenance-condition multiplier.

Design constraints honoured:
  * Python side only. The JS engine and the existing Layer 2 riskScore_v1 are
    not touched or imported.
  * Deterministic: no randomness, no clock reads, no network.
  * Graceful degradation: missing valuation / storm-path / property data never
    crashes; the affected output degrades, records a dataQualityNote, and
    downgrades confidence.
  * Reuses Phase A assetHealthScore as the maintenance-condition signal so the
    two phases stay consistent.

Data inputs (all already present in mock_data/):
    properties.json, work_orders.json, valuations.json, storm_path.json
"""

import math

from services.analysis_scope import (
    filter_valuations,
    filter_work_orders,
    resolve_analysis_scope,
    select_storm_event,
)
from services.data_loader import load_json
from services.layer1_schema import (
    RISK_V2_COMPONENTS,
    STORM_IMPACT_LEVELS,
    make_loss_forecast_result,
    make_risk_v2_result,
    make_storm_impact_result,
)
from services.portfolio_intelligence import (
    _group_work_orders_by_property,
    compute_asset_health_score,
)


# ===========================================================================
# 1) stormImpactLevel
# ===========================================================================
# Distance thresholds (statute miles) to the projected storm path, combined
# with hazard adjustments, then bucketed into Low/Medium/High/Severe.
# Mirrors the design doc's distance-driven logic (section 6) but uses real
# great-circle distance to the path polyline rather than county membership.

# Distance thresholds (statute miles) used by the combined level logic below.
STORM_DIST_SEVERE = 25    # within this AND high wind -> Severe
STORM_DIST_HIGH = 50      # within this OR heavy rain -> High
STORM_DIST_MEDIUM = 100   # within this -> Medium; beyond -> Low

# Hazard thresholds that combine with distance (design doc section 6 logic).
STORM_HIGH_WIND_MPH = 100
STORM_HEAVY_RAIN_INCHES = 8.0
HIGH_FLOOD_ZONES = frozenset({"High"})


def _haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in statute miles between two lat/lon points."""
    radius_miles = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius_miles * math.asin(math.sqrt(a))


def _point_to_segment_miles(lat, lon, a, b):
    """Distance from point to a path segment a->b, approximated on a local
    equirectangular projection (fine for the ~hundreds-of-miles demo scale)."""
    # Project lon/lat to local planar miles around the point's latitude.
    miles_per_deg_lat = 69.0
    miles_per_deg_lon = 69.0 * math.cos(math.radians(lat))

    def to_xy(plat, plon):
        return (plon * miles_per_deg_lon, plat * miles_per_deg_lat)

    px, py = to_xy(lat, lon)
    ax, ay = to_xy(a[0], a[1])
    bx, by = to_xy(b[0], b[1])

    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        # Degenerate segment: fall back to endpoint great-circle distance.
        return _haversine_miles(lat, lon, a[0], a[1])
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    proj_x, proj_y = ax + t * dx, ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _distance_to_path_miles(lat, lon, path_points):
    """Minimum distance from a property to the projected storm-path polyline."""
    if not path_points:
        return None
    if len(path_points) == 1:
        p = path_points[0]
        return _haversine_miles(lat, lon, p[0], p[1])
    best = None
    for i in range(len(path_points) - 1):
        d = _point_to_segment_miles(lat, lon, path_points[i], path_points[i + 1])
        best = d if best is None else min(best, d)
    return best


def _bump_level(level, notches=1):
    """Move a storm impact level up by ``notches`` (capped at Severe)."""
    idx = STORM_IMPACT_LEVELS.index(level)
    return STORM_IMPACT_LEVELS[min(idx + notches, len(STORM_IMPACT_LEVELS) - 1)]


def _classify_storm_level(distance, wind, rain, flood):
    """Combined distance + hazard classification (design doc section 6).

    Returns (level, list_of_driver_strings). Distance sets the base tier;
    wind/rain/flood can raise it, but a far-away property stays Low.
    """
    drivers = []
    high_wind = wind is not None and wind >= STORM_HIGH_WIND_MPH
    heavy_rain = rain is not None and rain >= STORM_HEAVY_RAIN_INCHES
    high_flood = flood in HIGH_FLOOD_ZONES

    if distance <= STORM_DIST_SEVERE and high_wind:
        level = "Severe"
        drivers.append(f"Within {distance:.0f} mi of track and high wind {wind} mph")
    elif distance <= STORM_DIST_HIGH or heavy_rain:
        level = "High"
        if distance <= STORM_DIST_HIGH:
            drivers.append(f"Within {distance:.0f} mi of projected storm path")
        if heavy_rain:
            drivers.append(f"Heavy rainfall forecast {rain} in")
    elif distance <= STORM_DIST_MEDIUM:
        level = "Medium"
        drivers.append(f"{distance:.0f} mi from projected storm path")
    else:
        level = "Low"
        drivers.append(f"{distance:.0f} mi from projected storm path (outside impact band)")

    # High flood-zone exposure nudges a borderline property up one tier,
    # but never escalates a far-away (Low) property.
    if high_flood and level in ("Medium", "High"):
        bumped = _bump_level(level)
        drivers.append("High flood-zone exposure (level raised)")
        level = bumped

    return level, drivers


def compute_storm_impact_level(property_dict, path_points, storm_meta):
    """Compute the stormImpactLevel Layer 1 result for one property."""
    property_id = property_dict.get("propertyId", "UNKNOWN")
    drivers = []
    notes = []
    confidence = "High"

    lat = property_dict.get("lat")
    lon = property_dict.get("lng")

    if lat is None or lon is None:
        notes.append("Missing property lat/lng; cannot compute storm distance")
        return make_storm_impact_result(
            property_id, "Low", distance_miles=None, drivers=["No coordinates"],
            confidence="Low", data_quality_notes=notes,
        )

    if not path_points:
        notes.append("Missing storm path data; storm impact not computed")
        return make_storm_impact_result(
            property_id, "Low", distance_miles=None,
            drivers=["No storm path available"], confidence="Low",
            data_quality_notes=notes,
        )

    distance = _distance_to_path_miles(lat, lon, path_points)

    wind = storm_meta.get("windSpeedMph")
    rain = storm_meta.get("rainfallForecastInches")
    flood = property_dict.get("floodZoneExposure")

    level, drivers = _classify_storm_level(distance, wind, rain, flood)

    if wind is None:
        notes.append("Missing storm windSpeedMph; Severe escalation unavailable")
        confidence = "Medium"
    if rain is None:
        notes.append("Missing storm rainfallForecastInches; rain escalation skipped")
        confidence = "Medium"
    if flood is None:
        notes.append("Missing property floodZoneExposure; flood escalation skipped")

    return make_storm_impact_result(
        property_id, level, distance_miles=distance, drivers=drivers,
        confidence=confidence, data_quality_notes=notes,
    )


# ===========================================================================
# 2) riskScore_v2  (weighted 5-factor, design doc section 6)
# ===========================================================================
# riskScore_v2 =
#     0.35 * stormExposure
#   + 0.25 * buildingVulnerability
#   + 0.20 * maintenanceRisk
#   + 0.10 * locationHazard
#   + 0.10 * assetValue
RISK_V2_WEIGHTS = {
    "stormExposure": 0.35,
    "buildingVulnerability": 0.25,
    "maintenanceRisk": 0.20,
    "locationHazard": 0.10,
    "assetValue": 0.10,
}

# stormExposure: storm impact level -> 0-100 component score.
STORM_EXPOSURE_BY_LEVEL = {"Low": 15, "Medium": 50, "High": 75, "Severe": 95}

# locationHazard: flood-zone exposure -> 0-100.
FLOOD_ZONE_HAZARD = {"High": 90, "Moderate": 55, "Low": 20}

# assetValue: replacementValue mapped to a 0-100 "importance" score by tiers.
ASSET_VALUE_TIERS = (
    (20_000_000, 100),
    (12_000_000, 75),
    (8_000_000, 50),
    (0, 25),
)


def _building_vulnerability_score(property_dict, analysis_year=2026):
    """0-100 from roof age, HVAC age, exterior condition, building age."""
    score = 0
    roof = property_dict.get("roofAgeYears")
    if roof is not None:
        score += min(40, max(0, (roof - 5)) * 2)  # older roof -> up to 40
    hvac = property_dict.get("hvacAvgAgeYears")
    if hvac is not None:
        score += min(25, max(0, (hvac - 5)) * 2.5)
    ext = property_dict.get("exteriorCondition")
    score += {"Poor": 25, "Fair": 15, "Good": 5, "Excellent": 0}.get(ext, 10)
    year = property_dict.get("yearBuilt")
    if year is not None:
        age = analysis_year - year
        score += min(10, max(0, (age - 20)) * 0.5)
    return max(0, min(100, score))


def _maintenance_risk_from_health(asset_health_result):
    """Invert assetHealthScore (Phase A) into a 0-100 maintenance RISK score."""
    return max(0, min(100, 100 - asset_health_result["score"]))


def _asset_value_score(replacement_value):
    if replacement_value is None:
        return None
    for threshold, score in ASSET_VALUE_TIERS:
        if replacement_value >= threshold:
            return score
    return 25


def compute_risk_score_v2(property_dict, storm_impact_result, asset_health_result,
                          valuation, analysis_year=2026):
    """Compute riskScore_v2 for one property from upstream Phase A/B results."""
    property_id = property_dict.get("propertyId", "UNKNOWN")
    drivers = []
    notes = []
    confidences = [storm_impact_result["confidence"], asset_health_result["confidence"]]

    storm_exposure = STORM_EXPOSURE_BY_LEVEL.get(storm_impact_result["level"], 15)
    building_vuln = _building_vulnerability_score(property_dict, analysis_year)
    maintenance_risk = _maintenance_risk_from_health(asset_health_result)

    flood = property_dict.get("floodZoneExposure")
    if flood is None:
        location_hazard = 40
        notes.append("Missing floodZoneExposure; locationHazard estimated at 40")
        confidences.append("Medium")
    else:
        location_hazard = FLOOD_ZONE_HAZARD.get(flood, 40)

    replacement_value = valuation.get("replacementValue") if valuation else None
    asset_value = _asset_value_score(replacement_value)
    if asset_value is None:
        asset_value = 25
        notes.append("Missing replacementValue; assetValue defaulted to 25")
        confidences.append("Medium")

    components = {
        "stormExposure": storm_exposure,
        "buildingVulnerability": building_vuln,
        "maintenanceRisk": maintenance_risk,
        "locationHazard": location_hazard,
        "assetValue": asset_value,
    }

    score = sum(RISK_V2_WEIGHTS[c] * components[c] for c in RISK_V2_COMPONENTS)

    # Drivers: name the two largest weighted contributors.
    weighted = sorted(
        ((c, RISK_V2_WEIGHTS[c] * components[c]) for c in RISK_V2_COMPONENTS),
        key=lambda kv: kv[1], reverse=True,
    )
    for name, contrib in weighted[:2]:
        drivers.append(f"{name} {components[name]} (contributes {contrib:.0f})")
    drivers.append(f"Storm impact level: {storm_impact_result['level']}")

    confidence = min(confidences, key=lambda c: {"Low": 0, "Medium": 1, "High": 2}[c])

    return make_risk_v2_result(
        property_id, score, components, drivers=drivers,
        confidence=confidence, data_quality_notes=notes,
    )


# ===========================================================================
# 3) lossForecast
# ===========================================================================
# lossForecast = replacementValue * damageRatio * vulnerabilityMultiplier
#                * maintenanceConditionMultiplier
# Damage ratios by storm impact level (design doc section 6 example values).
DAMAGE_RATIO_BY_LEVEL = {"Low": 0.005, "Medium": 0.025, "High": 0.075, "Severe": 0.15}


def _vulnerability_multiplier(property_dict, analysis_year=2026):
    """1.0 baseline, scaled up by building vulnerability (0-100 -> ~1.0-1.5)."""
    vuln = _building_vulnerability_score(property_dict, analysis_year)
    return 1.0 + (vuln / 100.0) * 0.5


def _maintenance_condition_multiplier(asset_health_result):
    """Worse asset health -> higher multiplier (~1.0-1.4)."""
    health = asset_health_result["score"]  # 0-100, higher = healthier
    return 1.0 + ((100 - health) / 100.0) * 0.4


def compute_loss_forecast(property_dict, storm_impact_result, asset_health_result,
                          valuation, analysis_year=2026):
    """Compute lossForecast for one property."""
    property_id = property_dict.get("propertyId", "UNKNOWN")
    drivers = []
    notes = []
    confidences = [storm_impact_result["confidence"], asset_health_result["confidence"]]

    level = storm_impact_result["level"]
    damage_ratio = DAMAGE_RATIO_BY_LEVEL.get(level, 0.005)
    vuln_mult = _vulnerability_multiplier(property_dict, analysis_year)
    maint_mult = _maintenance_condition_multiplier(asset_health_result)
    multipliers = {"buildingVulnerability": vuln_mult, "maintenanceCondition": maint_mult}

    replacement_value = valuation.get("replacementValue") if valuation else None

    if replacement_value is None:
        notes.append("Missing replacementValue; expected loss not computable")
        return make_loss_forecast_result(
            property_id, expected_loss=None, damage_ratio=damage_ratio,
            multipliers=multipliers, replacement_value=None,
            drivers=["Cannot compute loss without replacement value"],
            confidence="Low", data_quality_notes=notes,
        )

    expected_loss = replacement_value * damage_ratio * vuln_mult * maint_mult

    drivers.append(f"Storm impact {level} -> damage ratio {damage_ratio:.1%}")
    drivers.append(f"Replacement value ${replacement_value:,.0f}")
    drivers.append(
        f"Vulnerability x{vuln_mult:.2f}, maintenance x{maint_mult:.2f}"
    )

    if storm_impact_result["confidence"] == "Low":
        notes.append("Storm impact confidence is Low; loss estimate uncertain")

    confidence = min(confidences, key=lambda c: {"Low": 0, "Medium": 1, "High": 2}[c])

    return make_loss_forecast_result(
        property_id, expected_loss=expected_loss, damage_ratio=damage_ratio,
        multipliers=multipliers, replacement_value=replacement_value,
        drivers=drivers, confidence=confidence, data_quality_notes=notes,
    )


# ===========================================================================
# Orchestration: run the full Phase B chain across the portfolio
# ===========================================================================
def compute_phase_b(data_dir=None, scope=None):
    """Run stormImpactLevel -> riskScore_v2 -> lossForecast for every property.

    Args:
        data_dir: optional directory holding the mock data files.
        scope: optional resolved analysis scope (see services.analysis_scope).
            When None, the default scope is used. The scope selects the storm
            event (by stormEventId, falling back to the demo storm), the
            work-order lookback window, and which valuations are valid for
            the analysis year.

    Returns a dict keyed by metric name, each a list of Layer 1 results:
        {"stormImpactLevel": [...], "riskScore_v2": [...], "lossForecast": [...]}
    Deterministic ordering by propertyId.
    """
    if scope is None:
        scope = resolve_analysis_scope()

    if data_dir is None:
        properties_data = load_json("properties.json")
        work_orders_data = load_json("work_orders.json")
        try:
            valuations_data = load_json("valuations.json")
        except (FileNotFoundError, ValueError):
            valuations_data = {"valuations": []}
        path_points, storm_meta, _ = select_storm_event(scope)
    else:
        from pathlib import Path

        base = Path(data_dir)
        properties_data = load_json(base / "properties.json")
        work_orders_data = load_json(base / "work_orders.json")
        try:
            valuations_data = load_json(base / "valuations.json")
        except (FileNotFoundError, ValueError):
            valuations_data = {"valuations": []}
        try:
            storm = load_json(base / "storm_path.json")
            path_points, storm_meta, _ = select_storm_event(scope, storm_data=storm)
        except (FileNotFoundError, ValueError):
            path_points, storm_meta = [], {}

    analysis_year = scope["analysisYear"]
    in_scope_work_orders, _ = filter_work_orders(
        work_orders_data.get("workOrders", []), scope
    )
    work_orders_by_property = _group_work_orders_by_property(in_scope_work_orders)
    valid_valuations, _ = filter_valuations(
        valuations_data.get("valuations", []), scope
    )
    valuation_by_property = {v.get("propertyId"): v for v in valid_valuations}

    storm_results, risk_results, loss_results = [], [], []

    for prop in properties_data.get("properties", []):
        pid = prop.get("propertyId")
        wos = work_orders_by_property.get(pid, [])
        valuation = valuation_by_property.get(pid)

        # Phase A dependency: assetHealthScore feeds maintenance signals below.
        health = compute_asset_health_score(prop, wos, analysis_year=analysis_year)

        storm = compute_storm_impact_level(prop, path_points, storm_meta)
        risk = compute_risk_score_v2(prop, storm, health, valuation, analysis_year)
        loss = compute_loss_forecast(prop, storm, health, valuation, analysis_year)

        storm_results.append(storm)
        risk_results.append(risk)
        loss_results.append(loss)

    storm_results.sort(key=lambda r: r["propertyId"])
    risk_results.sort(key=lambda r: r["propertyId"])
    loss_results.sort(key=lambda r: r["propertyId"])

    return {
        "stormImpactLevel": storm_results,
        "riskScore_v2": risk_results,
        "lossForecast": loss_results,
    }


__all__ = [
    "compute_storm_impact_level",
    "compute_risk_score_v2",
    "compute_loss_forecast",
    "compute_phase_b",
    "RISK_V2_WEIGHTS",
    "DAMAGE_RATIO_BY_LEVEL",
]

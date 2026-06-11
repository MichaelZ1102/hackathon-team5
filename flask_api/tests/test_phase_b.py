"""Focused + integration tests for the Layer 1 Phase B calculation chain.

Covers stormImpactLevel, riskScore_v2, and lossForecast individually, plus an
integration test proving Phase A and Phase B outputs coexist and that the
existing Layer 2 output is unaffected.
"""

import math

import pytest

from services.capital_planning import (
    DAMAGE_RATIO_BY_LEVEL,
    RISK_V2_WEIGHTS,
    _classify_storm_level,
    _distance_to_path_miles,
    compute_loss_forecast,
    compute_phase_b,
    compute_risk_score_v2,
    compute_storm_impact_level,
)
from services.layer1_schema import (
    RISK_V2_COMPONENTS,
    STORM_IMPACT_LEVELS,
    validate_loss_forecast_result,
    validate_risk_v2_result,
    validate_storm_impact_result,
)
from services.portfolio_intelligence import compute_asset_health_score


# ---------------------------------------------------------------------------
# Fixtures: a representative property near vs far from a simple storm path
# ---------------------------------------------------------------------------

# Storm path roughly along latitude 28.5, crossing central FL west->east.
PATH = [(28.5, -82.5), (28.5, -81.0), (28.5, -80.8)]
STORM_META = {"windSpeedMph": 105, "rainfallForecastInches": 7.5}


def make_property(**overrides):
    base = {
        "propertyId": "TEST-001",
        "lat": 28.5,
        "lng": -81.5,  # right under the path
        "yearBuilt": 1990,
        "roofAgeYears": 20,
        "hvacAvgAgeYears": 12,
        "exteriorCondition": "Fair",
        "floodZoneExposure": "Moderate",
    }
    base.update(overrides)
    return base


# ===========================================================================
# stormImpactLevel
# ===========================================================================

def test_distance_to_path_zero_on_path():
    # Point exactly on the path centreline -> ~0 miles.
    d = _distance_to_path_miles(28.5, -81.5, PATH)
    assert d < 1.0


def test_distance_increases_away_from_path():
    near = _distance_to_path_miles(28.5, -81.5, PATH)
    far = _distance_to_path_miles(26.0, -81.5, PATH)
    assert far > near
    # ~2.5 deg latitude ~= ~170 miles
    assert far > 150


def test_classify_severe_close_and_high_wind():
    level, drivers = _classify_storm_level(distance=10, wind=110, rain=2.0, flood="Low")
    assert level == "Severe"
    assert drivers


def test_classify_high_within_50mi():
    level, _ = _classify_storm_level(distance=40, wind=80, rain=2.0, flood="Low")
    assert level == "High"


def test_classify_high_on_heavy_rain_even_if_far():
    level, _ = _classify_storm_level(distance=70, wind=80, rain=9.0, flood="Low")
    assert level == "High"


def test_classify_medium_band():
    level, _ = _classify_storm_level(distance=80, wind=80, rain=2.0, flood="Low")
    assert level == "Medium"


def test_classify_low_when_far_and_dry():
    # Far from path, no heavy rain -> Low (wind alone can't escalate a far asset).
    level, _ = _classify_storm_level(distance=150, wind=110, rain=2.0, flood="Low")
    assert level == "Low"


def test_classify_heavy_rain_forces_high_even_if_far():
    # Per design doc: distance<=50 OR rainfall>=8 -> High (rain is storm-wide).
    level, _ = _classify_storm_level(distance=150, wind=80, rain=9.0, flood="Low")
    assert level == "High"


def test_high_flood_bumps_medium_to_high_but_not_low():
    medium_level, _ = _classify_storm_level(distance=80, wind=80, rain=2.0, flood="High")
    assert medium_level == "High"  # Medium bumped to High
    low_level, _ = _classify_storm_level(distance=150, wind=80, rain=2.0, flood="High")
    assert low_level == "Low"  # far property NOT escalated


def test_storm_impact_result_schema_and_near_property():
    prop = make_property()
    result = compute_storm_impact_level(prop, PATH, STORM_META)
    assert validate_storm_impact_result(result) == []
    assert result["level"] == "Severe"  # on path + 105 mph wind
    assert result["distanceMiles"] is not None
    assert result["confidence"] == "High"


def test_storm_impact_missing_coords_degrades():
    prop = make_property(lat=None, lng=None)
    result = compute_storm_impact_level(prop, PATH, STORM_META)
    assert validate_storm_impact_result(result) == []
    assert result["level"] == "Low"
    assert result["confidence"] == "Low"
    assert result["distanceMiles"] is None
    assert any("lat/lng" in n for n in result["dataQualityNotes"])


def test_storm_impact_missing_path_degrades():
    prop = make_property()
    result = compute_storm_impact_level(prop, [], {})
    assert result["level"] == "Low"
    assert result["confidence"] == "Low"
    assert any("storm path" in n.lower() for n in result["dataQualityNotes"])


def test_storm_impact_missing_wind_lowers_confidence():
    prop = make_property()
    result = compute_storm_impact_level(prop, PATH, {"rainfallForecastInches": 7.5})
    assert result["confidence"] == "Medium"
    assert any("windSpeedMph" in n for n in result["dataQualityNotes"])


# ===========================================================================
# riskScore_v2
# ===========================================================================

def test_risk_v2_weights_sum_to_one():
    assert abs(sum(RISK_V2_WEIGHTS.values()) - 1.0) < 1e-9


def test_risk_v2_deterministic_known_value():
    """Lock the weighted formula with a hand-computed expectation."""
    prop = make_property()
    storm = compute_storm_impact_level(prop, PATH, STORM_META)  # Severe -> stormExposure 95
    wos = []
    health = compute_asset_health_score(prop, wos)
    valuation = {"replacementValue": 10_000_000}  # assetValue tier -> 50

    result = compute_risk_score_v2(prop, storm, health, valuation)
    assert validate_risk_v2_result(result) == []

    comps = result["components"]
    assert comps["stormExposure"] == 95
    assert comps["assetValue"] == 50
    # locationHazard for Moderate flood = 55
    assert comps["locationHazard"] == 55
    # Recompute the weighted score from the reported components and compare.
    expected = round(sum(RISK_V2_WEIGHTS[c] * comps[c] for c in RISK_V2_COMPONENTS))
    assert result["score"] == expected
    assert result["band"] in ("Low", "Medium", "High")


def test_risk_v2_missing_valuation_degrades_confidence():
    prop = make_property()
    storm = compute_storm_impact_level(prop, PATH, STORM_META)
    health = compute_asset_health_score(prop, [])
    result = compute_risk_score_v2(prop, storm, health, valuation=None)
    assert validate_risk_v2_result(result) == []
    assert result["components"]["assetValue"] == 25  # defaulted
    assert result["confidence"] in ("Low", "Medium")
    assert any("replacementValue" in n for n in result["dataQualityNotes"])


def test_risk_v2_higher_when_storm_severe():
    prop = make_property()
    valuation = {"replacementValue": 10_000_000}
    health = compute_asset_health_score(prop, [])

    severe = compute_storm_impact_level(prop, PATH, STORM_META)
    far_prop = make_property(lat=24.0, lng=-81.5)  # far from path -> Low
    low = compute_storm_impact_level(far_prop, PATH, STORM_META)

    severe_score = compute_risk_score_v2(prop, severe, health, valuation)["score"]
    low_score = compute_risk_score_v2(prop, low, health, valuation)["score"]
    assert severe_score > low_score


# ===========================================================================
# lossForecast
# ===========================================================================

def test_loss_forecast_deterministic_formula():
    prop = make_property()
    storm = compute_storm_impact_level(prop, PATH, STORM_META)  # Severe
    health = compute_asset_health_score(prop, [])
    valuation = {"replacementValue": 10_000_000}

    result = compute_loss_forecast(prop, storm, health, valuation)
    assert validate_loss_forecast_result(result) == []

    # expectedLoss == replacementValue * damageRatio * vuln * maint, computed
    # from the raw multiplier helpers (the reported multipliers are display-
    # rounded to 3 dp, so reconstruct from source for an exact comparison).
    from services.capital_planning import (
        _maintenance_condition_multiplier,
        _vulnerability_multiplier,
    )

    rv = 10_000_000
    ratio = result["damageRatio"]
    vuln = _vulnerability_multiplier(prop)
    maint = _maintenance_condition_multiplier(health)
    expected = round(rv * ratio * vuln * maint, 2)
    assert result["expectedLoss"] == pytest.approx(expected, rel=1e-6)
    assert ratio == DAMAGE_RATIO_BY_LEVEL["Severe"]


def test_loss_forecast_scales_with_storm_level():
    prop = make_property()
    health = compute_asset_health_score(prop, [])
    valuation = {"replacementValue": 10_000_000}

    severe = compute_storm_impact_level(prop, PATH, STORM_META)
    far_prop = make_property(lat=24.0, lng=-81.5)
    low = compute_storm_impact_level(far_prop, PATH, STORM_META)

    severe_loss = compute_loss_forecast(prop, severe, health, valuation)["expectedLoss"]
    low_loss = compute_loss_forecast(prop, low, health, valuation)["expectedLoss"]
    assert severe_loss > low_loss


def test_loss_forecast_missing_valuation_degrades():
    prop = make_property()
    storm = compute_storm_impact_level(prop, PATH, STORM_META)
    health = compute_asset_health_score(prop, [])
    result = compute_loss_forecast(prop, storm, health, valuation=None)
    assert validate_loss_forecast_result(result) == []
    assert result["expectedLoss"] is None
    assert result["replacementValue"] is None
    assert result["confidence"] == "Low"
    assert any("replacementValue" in n for n in result["dataQualityNotes"])


def test_loss_forecast_never_negative():
    prop = make_property(roofAgeYears=0, hvacAvgAgeYears=0, exteriorCondition="Excellent",
                         yearBuilt=2025)
    storm = compute_storm_impact_level(prop, PATH, STORM_META)
    health = compute_asset_health_score(prop, [])
    result = compute_loss_forecast(prop, storm, health, {"replacementValue": 5_000_000})
    assert result["expectedLoss"] >= 0


# ===========================================================================
# Integration: Phase A + Phase B coexist; full portfolio
# ===========================================================================

def test_phase_b_runs_over_real_portfolio():
    out = compute_phase_b()
    assert set(out.keys()) == {"stormImpactLevel", "riskScore_v2", "lossForecast"}
    assert len(out["stormImpactLevel"]) == 14
    assert len(out["riskScore_v2"]) == 14
    assert len(out["lossForecast"]) == 14
    for r in out["stormImpactLevel"]:
        assert validate_storm_impact_result(r) == []
    for r in out["riskScore_v2"]:
        assert validate_risk_v2_result(r) == []
    for r in out["lossForecast"]:
        assert validate_loss_forecast_result(r) == []


def test_phase_b_is_deterministic():
    a = compute_phase_b()
    b = compute_phase_b()
    assert a == b


def test_phase_b_shows_real_variation():
    """Demo realism: the portfolio should not collapse to a single bucket."""
    out = compute_phase_b()
    levels = {r["level"] for r in out["stormImpactLevel"]}
    bands = {r["band"] for r in out["riskScore_v2"]}
    assert len(levels) >= 2, f"storm levels lack variation: {levels}"
    assert len(bands) >= 2, f"risk bands lack variation: {bands}"


def test_phase_a_and_b_coexist():
    """Phase A assetHealthScore and Phase B riskScore_v2 are distinct metrics."""
    from services.portfolio_intelligence import compute_all_asset_health

    phase_a = {r["propertyId"]: r for r in compute_all_asset_health()}
    phase_b = compute_phase_b()
    risk_v2 = {r["propertyId"]: r for r in phase_b["riskScore_v2"]}

    # Same property set, different metric labels, both present.
    assert set(phase_a) == set(risk_v2)
    for pid in phase_a:
        assert phase_a[pid]["metric"] == "assetHealthScore"
        assert risk_v2[pid]["metric"] == "riskScore_v2"


def test_phase_b_does_not_touch_layer2_riskscore():
    """The existing Layer 2 engine output still has riskScore (v1), and Phase B
    never emits a field named 'riskScore' (only 'riskScore_v2')."""
    from services.risk_engine import analyze_risk, DEFAULT_ANALYSIS_TIME

    layer2 = analyze_risk(DEFAULT_ANALYSIS_TIME)
    assert layer2["properties"], "Layer 2 should still produce properties"
    assert "riskScore" in layer2["properties"][0]  # v1 intact

    phase_b = compute_phase_b()
    for r in phase_b["riskScore_v2"]:
        assert r["metric"] == "riskScore_v2"
        assert "riskScore" not in r  # no clobbering of the v1 field name

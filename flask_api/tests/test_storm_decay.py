"""Tests for the distance-decay storm impact model.

Principle under test: the portfolio intelligence engine evaluates ALL
properties — storm impact is strongest near the storm path and decays with
distance, reaching level "None" (score 0) outside meaningful impact range.
Non-storm risk factors still apply to None-impact properties.
"""

import os
import sys

FLASK_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FLASK_API_DIR not in sys.path:
    sys.path.insert(0, FLASK_API_DIR)

import pytest

from app import app as flask_app
from services.capital_planning import (
    STORM_MAX_IMPACT_DISTANCE_MILES,
    compute_loss_forecast,
    compute_phase_b,
    compute_risk_score_v2,
    compute_storm_impact_level,
)
from services.layer1_schema import STORM_IMPACT_LEVELS
from services.phase_c import compute_phase_c
from services.portfolio_intelligence import compute_asset_health_score


@pytest.fixture(autouse=True)
def offline_env(monkeypatch):
    monkeypatch.setenv("AI_COPILOT_SKIP_DOTENV", "1")
    for name in (
        "AI_PLATFORM_AGENT_ENDPOINT", "AI_PLATFORM_AGENT_ID", "AI_PLATFORM_API_KEY",
        "AI_PLATFORM_USERNAME", "AI_PLATFORM_PASSWORD", "AI_PLATFORM_CONVERSATION_ID",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def client():
    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


# A simple west-east path along latitude 28.5 and a property factory.
PATH = [(28.5, -82.5), (28.5, -81.0)]
STORM_META = {"windSpeedMph": 103, "rainfallForecastInches": 7.1}


def make_property(lat, lng, pid="TEST-001"):
    return {
        "propertyId": pid,
        "lat": lat,
        "lng": lng,
        "yearBuilt": 1995,
        "roofAgeYears": 18,
        "hvacAvgAgeYears": 10,
        "exteriorCondition": "Fair",
        "floodZoneExposure": "Moderate",
    }


def storm_at_distance_deg(lat_offset):
    """Storm result for a property offset south of the path by lat degrees."""
    prop = make_property(28.5 - lat_offset, -81.5)
    return prop, compute_storm_impact_level(prop, PATH, STORM_META)


# ---------------------------------------------------------------------------
# Decay behavior on the unit level
# ---------------------------------------------------------------------------

def test_near_property_severe_far_property_none():
    _, near = storm_at_distance_deg(0.0)      # on the path
    _, far = storm_at_distance_deg(4.0)       # ~275 mi south
    assert near["level"] == "Severe"
    assert near["score"] >= 95
    assert far["level"] == "None"
    assert far["score"] == 0
    assert far["distanceMiles"] > STORM_MAX_IMPACT_DISTANCE_MILES


def test_score_strictly_decreases_with_distance():
    offsets = [0.0, 0.3, 0.8, 1.5, 2.5]      # ~0 to ~170 miles
    results = [storm_at_distance_deg(o)[1] for o in offsets]
    scores = [r["score"] for r in results]
    distances = [r["distanceMiles"] for r in results]
    assert distances == sorted(distances)
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores), "decay should differentiate distances"


def test_none_impact_loss_forecast_is_zero_when_valuation_exists():
    prop, storm = storm_at_distance_deg(4.0)
    assert storm["level"] == "None"
    health = compute_asset_health_score(prop, [])
    loss = compute_loss_forecast(prop, storm, health, {"replacementValue": 5_000_000})
    assert loss["expectedLoss"] == 0.0          # zero, NOT null: it was evaluated
    assert loss["damageRatio"] == 0.0


def test_none_impact_loss_forecast_null_when_valuation_missing():
    prop, storm = storm_at_distance_deg(4.0)
    health = compute_asset_health_score(prop, [])
    loss = compute_loss_forecast(prop, storm, health, valuation=None)
    assert loss["expectedLoss"] is None
    assert any("replacementValue" in n for n in loss["dataQualityNotes"])


def test_risk_score_v2_still_computed_for_none_impact():
    """Non-storm factors keep a distant asset risky: riskScore_v2 never vanishes."""
    prop, storm = storm_at_distance_deg(4.0)
    assert storm["level"] == "None"
    health = compute_asset_health_score(prop, [])
    risk = compute_risk_score_v2(prop, storm, health, {"replacementValue": 9_000_000})
    assert risk["score"] > 0
    assert risk["components"]["stormExposure"] == 0
    assert risk["components"]["buildingVulnerability"] > 0


# ---------------------------------------------------------------------------
# Portfolio-wide behavior on the real demo dataset
# ---------------------------------------------------------------------------

def test_every_property_receives_storm_impact_level():
    storm_results = compute_phase_b()["stormImpactLevel"]
    assert len(storm_results) == 290
    for r in storm_results:
        assert r["level"] in STORM_IMPACT_LEVELS
        assert isinstance(r["score"], int)


def test_priority_ranking_includes_all_properties_even_none_impact():
    out = compute_phase_c()
    assert len(out["priorityRanking"]) == 290  # nobody filtered out
    ranked_ids = {r["propertyId"] for r in out["priorityRanking"]}
    storm = {r["propertyId"]: r for r in compute_phase_b()["stormImpactLevel"]}
    none_ids = {pid for pid, r in storm.items() if r["level"] == "None"}
    assert none_ids <= ranked_ids  # None-impact properties are still ranked


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------

def test_api_exposes_distance_and_score_per_property(client):
    data = client.get("/api/portfolio/intelligence").get_json()
    for r in data["propertyIntelligenceResults"]:
        assert "distanceToStormPathMiles" in r
        assert "stormImpactScore" in r
        assert r["stormImpactLevel"]["level"] in STORM_IMPACT_LEVELS
    # Spot-check decay across the portfolio: nearest property scores at least
    # as high as the farthest one.
    with_distance = [
        r for r in data["propertyIntelligenceResults"]
        if r["distanceToStormPathMiles"] is not None
    ]
    nearest = min(with_distance, key=lambda r: r["distanceToStormPathMiles"])
    farthest = max(with_distance, key=lambda r: r["distanceToStormPathMiles"])
    assert nearest["stormImpactScore"] >= farthest["stormImpactScore"]


def test_api_diagnostics_include_storm_impact_distribution(client):
    data = client.get("/api/portfolio/intelligence").get_json()
    dist = data["diagnostics"]["stormImpactDistribution"]
    assert set(dist.keys()) == {"Severe", "High", "Medium", "Low", "None"}
    assert sum(dist.values()) == 290
    summary = data["portfolioSummary"]
    assert summary["affectedPropertyCount"] == (
        dist["Severe"] + dist["High"] + dist["Medium"]
    )
    assert "affectedDefinition" in data["diagnostics"]


def test_api_watch_and_priority_lists_carry_storm_fields(client):
    data = client.get("/api/portfolio/intelligence").get_json()
    for row in data["watchList"]:
        assert "stormImpactScore" in row
        assert "distanceToStormPathMiles" in row
    for row in data["finalPriorityList"][:5]:
        assert "stormImpactScore" in row
        assert "distanceToStormPathMiles" in row


# ---------------------------------------------------------------------------
# AI Copilot compact state
# ---------------------------------------------------------------------------

def test_compact_state_carries_storm_decay_fields():
    from services.ai_copilot_adapter import build_compact_ai_copilot_state

    state = build_compact_ai_copilot_state(
        task_type="storm_impact",
        user_question="Which assets face the most storm impact?",
        data_context={},
    )
    top = state["watchList"][0]
    assert "stormImpactScore" in top
    assert "distanceToStormPathMiles" in top
    summary = state["portfolioSummary"]
    assert "stormImpactDistribution" in summary
    assert "affectedPropertyCount" in summary
    assert sum(summary["stormImpactDistribution"].values()) == 290

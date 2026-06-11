"""Tests for the read-only Portfolio Intelligence API endpoint.

Covers GET /api/portfolio/intelligence: 200 status, full Phase A+B metric
presence, deterministic watch list, totalLossForecast integrity, and diagnostics
shape. Layer 2 endpoints are smoke-checked here too to prove they are unaffected.
"""

import os
import sys

FLASK_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FLASK_API_DIR not in sys.path:
    sys.path.insert(0, FLASK_API_DIR)

import pytest

from app import app as flask_app
from services.portfolio_intelligence_api import (
    MISSING_METRICS,
    build_portfolio_intelligence,
)


@pytest.fixture()
def client():
    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


# ---------------------------------------------------------------------------
# Endpoint contract
# ---------------------------------------------------------------------------

def test_endpoint_returns_200(client):
    resp = client.get("/api/portfolio/intelligence")
    assert resp.status_code == 200
    assert resp.is_json


def test_top_level_shape(client):
    data = client.get("/api/portfolio/intelligence").get_json()
    for key in ("portfolioSummary", "propertyIntelligenceResults", "watchList", "diagnostics"):
        assert key in data, f"missing top-level key {key}"


def test_each_property_has_all_phase_a_b_metrics(client):
    data = client.get("/api/portfolio/intelligence").get_json()
    results = data["propertyIntelligenceResults"]
    assert len(results) == 14
    for r in results:
        for key in (
            "propertyId", "propertyName", "county", "location",
            "assetHealthScore", "stormImpactLevel", "riskScore_v2", "lossForecast",
            "drivers", "confidence", "dataQualityNotes",
        ):
            assert key in r, f"{r.get('propertyId')} missing {key}"
        # Each embedded metric result carries its own metric label.
        assert r["assetHealthScore"]["metric"] == "assetHealthScore"
        assert r["stormImpactLevel"]["metric"] == "stormImpactLevel"
        assert r["riskScore_v2"]["metric"] == "riskScore_v2"
        assert r["lossForecast"]["metric"] == "lossForecast"


def test_portfolio_summary_fields(client):
    summary = client.get("/api/portfolio/intelligence").get_json()["portfolioSummary"]
    for key in (
        "totalProperties", "severeImpactCount", "highRiskCount",
        "totalLossForecast", "averageAssetHealthScore", "topRiskDrivers",
    ):
        assert key in summary
    assert summary["totalProperties"] == 14
    assert isinstance(summary["topRiskDrivers"], list)


def test_total_loss_forecast_matches_sum_of_properties(client):
    data = client.get("/api/portfolio/intelligence").get_json()
    expected = sum(
        r["lossForecast"]["expectedLoss"]
        for r in data["propertyIntelligenceResults"]
        if r["lossForecast"]["expectedLoss"] is not None
    )
    assert data["portfolioSummary"]["totalLossForecast"] == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# Watch list
# ---------------------------------------------------------------------------

def test_watch_list_is_deterministic(client):
    a = client.get("/api/portfolio/intelligence").get_json()["watchList"]
    b = client.get("/api/portfolio/intelligence").get_json()["watchList"]
    assert a == b


def test_watch_list_sorted_by_risk_then_loss(client):
    watch = client.get("/api/portfolio/intelligence").get_json()["watchList"]
    assert 1 <= len(watch) <= 5
    # watchRank is 1..n in order
    assert [w["watchRank"] for w in watch] == list(range(1, len(watch) + 1))
    # Non-increasing riskScore_v2 down the list (primary sort key).
    risks = [w["riskScore_v2"] for w in watch]
    assert risks == sorted(risks, reverse=True)


def test_watch_list_carries_temporary_note(client):
    watch = client.get("/api/portfolio/intelligence").get_json()["watchList"]
    assert all("priorityRanking" in w["note"] for w in watch)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def test_diagnostics_shape_and_phase_c_metrics_included(client):
    diag = client.get("/api/portfolio/intelligence").get_json()["diagnostics"]
    for key in (
        "calculationVersion", "includedMetrics", "missingMetrics",
        "dataSourcesUsed", "warnings",
    ):
        assert key in diag
    # As of Phase C every planned Layer 1 metric is implemented and included.
    assert set(diag["includedMetrics"]) == {
        "assetHealthScore", "stormImpactLevel", "riskScore_v2", "lossForecast",
        "insuranceGap", "capitalROI", "priorityRanking",
    }
    assert diag["missingMetrics"] == []
    assert MISSING_METRICS == []


def test_phase_c_metrics_present_in_every_property(client):
    """Every property now carries insuranceGap and priorityRanking results."""
    data = client.get("/api/portfolio/intelligence").get_json()
    for r in data["propertyIntelligenceResults"]:
        assert r["insuranceGap"]["metric"] == "insuranceGap"
        assert r["priorityRanking"]["metric"] == "priorityRanking"
        assert "bestCapitalAction" in r


# ---------------------------------------------------------------------------
# Read-only / Layer 2 isolation
# ---------------------------------------------------------------------------

def test_endpoint_is_read_only_no_side_effects(client):
    """Calling the endpoint twice yields identical output (no state mutation)."""
    a = client.get("/api/portfolio/intelligence").get_json()
    b = client.get("/api/portfolio/intelligence").get_json()
    assert a == b


def test_layer2_endpoints_still_work(client):
    """Layer 2 read endpoints remain functional and still expose riskScore (v1)."""
    timeline = client.get("/api/risk/timeline")
    assert timeline.status_code == 200

    props = client.get("/api/risk/properties")
    assert props.status_code == 200
    body = props.get_json()
    assert body["properties"], "Layer 2 should still return properties"
    assert "riskScore" in body["properties"][0]  # v1 field intact


def test_builder_callable_directly():
    """The aggregation builder works without the HTTP layer (used by AI Copilot later)."""
    payload = build_portfolio_intelligence()
    assert payload["portfolioSummary"]["totalProperties"] == 14
    assert len(payload["propertyIntelligenceResults"]) == 14

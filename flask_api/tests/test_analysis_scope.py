"""Tests for explicit analysis-scope support.

Covers the scope model {portfolioId, analysisYear, stormEventId} across:
  * GET /api/portfolio/intelligence query parameters
  * scope-driven selection of work orders, valuations, and the storm event
  * POST /api/ai-copilot/analyze ``scenario`` support
  * the compact AI Copilot state carrying the scope and no full raw history
"""

import json
import os
import sys

FLASK_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FLASK_API_DIR not in sys.path:
    sys.path.insert(0, FLASK_API_DIR)

import pytest

from app import app as flask_app
from services.analysis_scope import (
    DEFAULT_ANALYSIS_YEAR,
    DEFAULT_PORTFOLIO_ID,
    WORK_ORDER_LOOKBACK_MONTHS,
    resolve_analysis_scope,
    work_order_window,
)
from services.ai_copilot_adapter import build_compact_ai_copilot_state


DEMO_STORM_ID = "FL-HUR-2026-FCST-01"

_AI_PLATFORM_ENV_VARS = (
    "AI_PLATFORM_AGENT_ENDPOINT",
    "AI_PLATFORM_AGENT_ID",
    "AI_PLATFORM_API_KEY",
    "AI_PLATFORM_USERNAME",
    "AI_PLATFORM_PASSWORD",
    "AI_PLATFORM_CONVERSATION_ID",
    "AI_PLATFORM_BASE_URL",
    "AI_PLATFORM_AUTH_URL",
    "AI_PLATFORM_CONVERSATION_ENDPOINT",
)


@pytest.fixture(autouse=True)
def isolate_ai_platform_env(monkeypatch):
    """Keep Copilot tests offline and deterministic (mirrors the adapter tests)."""
    monkeypatch.setenv("AI_COPILOT_SKIP_DOTENV", "1")
    for name in _AI_PLATFORM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def client():
    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


# ---------------------------------------------------------------------------
# Scope resolution unit behavior
# ---------------------------------------------------------------------------

def test_resolve_scope_defaults():
    scope = resolve_analysis_scope()
    assert scope["portfolioId"] == DEFAULT_PORTFOLIO_ID
    assert scope["analysisYear"] == DEFAULT_ANALYSIS_YEAR
    assert scope["stormEventId"] is None
    assert scope["notes"] == []


def test_resolve_scope_rejects_non_integer_year():
    with pytest.raises(ValueError):
        resolve_analysis_scope(analysis_year="not-a-year")


def test_work_order_window_is_rolling_lookback():
    start, end = work_order_window(resolve_analysis_scope(analysis_year=2025))
    assert end == "2025-12-31"
    assert start == f"{2025 - WORK_ORDER_LOOKBACK_MONTHS // 12}-12-31"


# ---------------------------------------------------------------------------
# GET /api/portfolio/intelligence: default scope
# ---------------------------------------------------------------------------

def test_default_scope_works_and_is_reported(client):
    data = client.get("/api/portfolio/intelligence").get_json()
    scope = data["diagnostics"]["analysisScope"]
    assert scope["portfolioId"] == DEFAULT_PORTFOLIO_ID
    assert scope["analysisYear"] == DEFAULT_ANALYSIS_YEAR
    assert scope["stormEventId"] == DEMO_STORM_ID
    # The 290-property dataset includes stale 2023/2024 work orders that the
    # default 24-month window must exclude; all valuations are dated <= 2026.
    assert scope["workOrdersInScope"] > 0
    assert scope["workOrdersExcluded"] > 0
    assert scope["valuationsValidForYear"] == 290
    assert scope["valuationsExcluded"] == 0
    assert data["portfolioSummary"]["totalProperties"] == 290


def test_explicit_default_scope_matches_parameterless_call(client):
    default = client.get("/api/portfolio/intelligence").get_json()
    explicit = client.get(
        "/api/portfolio/intelligence"
        f"?portfolioId={DEFAULT_PORTFOLIO_ID}&analysisYear={DEFAULT_ANALYSIS_YEAR}"
        f"&stormEventId={DEMO_STORM_ID}"
    ).get_json()
    # Identical calculations; only the echoed requestedStormEventId may differ.
    assert explicit["portfolioSummary"] == default["portfolioSummary"]
    assert explicit["propertyIntelligenceResults"] == default["propertyIntelligenceResults"]
    assert explicit["watchList"] == default["watchList"]


# ---------------------------------------------------------------------------
# GET /api/portfolio/intelligence: explicit analysisYear
# ---------------------------------------------------------------------------

def test_explicit_analysis_year_changes_selected_data(client):
    default = client.get("/api/portfolio/intelligence").get_json()
    scoped = client.get("/api/portfolio/intelligence?analysisYear=2025").get_json()

    scope = scoped["diagnostics"]["analysisScope"]
    assert scope["analysisYear"] == 2025
    assert scope["workOrderWindow"]["end"] == "2025-12-31"
    # Demo work orders run 2025-11..2026-05, so the 2026 ones must drop out...
    assert scope["workOrdersExcluded"] > 0
    # ...and valuations dated in 2026 are not valid for analysisYear 2025.
    assert scope["valuationsExcluded"] > 0
    assert any("valuation" in w.lower() for w in scoped["diagnostics"]["warnings"])

    # The scope actually changes the calculations, not just the diagnostics.
    assert (
        scoped["portfolioSummary"]["totalLossForecast"]
        != default["portfolioSummary"]["totalLossForecast"]
    )


def test_invalid_analysis_year_returns_400(client):
    resp = client.get("/api/portfolio/intelligence?analysisYear=not-a-year")
    assert resp.status_code == 400
    assert "analysisYear" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# GET /api/portfolio/intelligence: explicit stormEventId
# ---------------------------------------------------------------------------

def test_explicit_storm_event_id_is_selected(client):
    data = client.get(
        f"/api/portfolio/intelligence?stormEventId={DEMO_STORM_ID}"
    ).get_json()
    scope = data["diagnostics"]["analysisScope"]
    assert scope["stormEventId"] == DEMO_STORM_ID
    assert scope["requestedStormEventId"] == DEMO_STORM_ID
    assert not any("falling back" in n for n in scope["notes"])


def test_unknown_storm_event_falls_back_to_demo_storm_with_warning(client):
    resp = client.get("/api/portfolio/intelligence?stormEventId=TORNADO-2026-001")
    assert resp.status_code == 200
    data = resp.get_json()
    scope = data["diagnostics"]["analysisScope"]
    assert scope["stormEventId"] == DEMO_STORM_ID
    assert scope["requestedStormEventId"] == "TORNADO-2026-001"
    assert any("TORNADO-2026-001" in w for w in data["diagnostics"]["warnings"])


def test_unknown_portfolio_id_is_reported_not_fatal(client):
    resp = client.get("/api/portfolio/intelligence?portfolioId=TX-DEMO")
    assert resp.status_code == 200
    warnings = resp.get_json()["diagnostics"]["warnings"]
    assert any("TX-DEMO" in w for w in warnings)


# ---------------------------------------------------------------------------
# POST /api/ai-copilot/analyze: scenario scope
# ---------------------------------------------------------------------------

def _analyze(client, scenario=None, **extra):
    body = {
        "taskType": "portfolio_review",
        "userQuestion": "Which assets need attention?",
        "dataContext": {"requestedMetrics": ["insuranceGap", "capitalROI", "priorityRanking"]},
    }
    if scenario is not None:
        body["scenario"] = scenario
    body.update(extra)
    return client.post("/api/ai-copilot/analyze", json=body)


def test_copilot_receives_scope_in_diagnostics(client):
    resp = _analyze(
        client,
        scenario={
            "portfolioId": DEFAULT_PORTFOLIO_ID,
            "analysisYear": 2026,
            "stormEventId": DEMO_STORM_ID,
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["mode"] == "mock"
    scope = body["diagnostics"]["analysisScope"]
    assert scope["portfolioId"] == DEFAULT_PORTFOLIO_ID
    assert scope["analysisYear"] == 2026
    assert scope["stormEventId"] == DEMO_STORM_ID
    # The frontend-requested metrics are implemented as of Phase C.
    for metric in ("insuranceGap", "capitalROI", "priorityRanking"):
        assert metric in body["diagnostics"]["availableMetrics"]
        assert metric not in body["diagnostics"]["missingMetrics"]


def test_copilot_scenario_year_drives_the_scoped_state(client):
    resp = _analyze(client, scenario={"analysisYear": 2025})
    assert resp.status_code == 200
    scope = resp.get_json()["diagnostics"]["analysisScope"]
    assert scope["analysisYear"] == 2025
    assert scope["workOrderWindow"]["end"] == "2025-12-31"


def test_copilot_without_scenario_uses_default_scope(client):
    resp = _analyze(client)
    assert resp.status_code == 200
    scope = resp.get_json()["diagnostics"]["analysisScope"]
    assert scope["portfolioId"] == DEFAULT_PORTFOLIO_ID
    assert scope["analysisYear"] == DEFAULT_ANALYSIS_YEAR
    assert scope["stormEventId"] == DEMO_STORM_ID


def test_copilot_invalid_scenario_year_returns_400(client):
    resp = _analyze(client, scenario={"analysisYear": "not-a-year"})
    assert resp.status_code == 400
    assert "analysisYear" in resp.get_json()["error"]


def test_copilot_unknown_storm_scenario_falls_back(client):
    resp = _analyze(client, scenario={"stormEventId": "TORNADO-2026-001"})
    assert resp.status_code == 200
    scope = resp.get_json()["diagnostics"]["analysisScope"]
    assert scope["stormEventId"] == DEMO_STORM_ID


# ---------------------------------------------------------------------------
# Compact Copilot state: scope present, no full raw history
# ---------------------------------------------------------------------------

def test_compact_state_carries_scope(client):
    state = build_compact_ai_copilot_state(
        task_type="portfolio_review",
        user_question="What changed?",
        data_context={},
        scope=resolve_analysis_scope(analysis_year=2025, storm_event_id=DEMO_STORM_ID),
    )
    assert state["analysisScope"]["analysisYear"] == 2025
    assert state["analysisScope"]["stormEventId"] == DEMO_STORM_ID
    assert state["analysisScope"]["workOrderWindow"]["end"] == "2025-12-31"


def test_compact_state_contains_no_full_historical_data():
    state = build_compact_ai_copilot_state(
        task_type="portfolio_review",
        user_question="What should leadership know?",
        data_context={},
        scope=resolve_analysis_scope(),
    )
    # No raw per-property/per-record history goes to the AI state.
    assert state["layer1Results"] == []
    assert state["operationalActions"] == []
    assert len(state["watchList"]) <= 4
    assert "propertyIntelligenceResults" not in state

    # No raw work-order or valuation records leak through anywhere.
    serialized = json.dumps(state)
    assert '"workOrderId"' not in serialized
    assert '"replacementValue"' not in serialized
    assert '"projectedPath"' not in serialized

"""Guards for the two-endpoint frontend architecture.

The demo exposes a deliberate split:

  * GET  /api/portfolio/intelligence — deterministic metrics only. Fast,
    local-data-driven, never calls AI or the network. The frontend renders
    its metrics UI from this endpoint alone.
  * POST /api/ai-copilot/analyze — AI advisory built ON TOP of the same
    scoped deterministic metrics. May be slow (AI Platform call); its
    failure must never block the metrics UI.

These tests pin that contract so a future change cannot quietly couple the
metrics endpoint to AI or push raw-data responsibilities onto the frontend.
"""

import os
import sys

FLASK_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FLASK_API_DIR not in sys.path:
    sys.path.insert(0, FLASK_API_DIR)

import pytest

import services.ai_copilot_adapter as adapter
from app import app as flask_app


@pytest.fixture(autouse=True)
def offline_env(monkeypatch):
    monkeypatch.setenv("AI_COPILOT_SKIP_DOTENV", "1")
    for name in (
        "AI_PLATFORM_AGENT_ENDPOINT", "AI_PLATFORM_AGENT_ID", "AI_PLATFORM_API_KEY",
        "AI_PLATFORM_USERNAME", "AI_PLATFORM_PASSWORD", "AI_PLATFORM_CONVERSATION_ID",
        "AI_PLATFORM_BASE_URL", "AI_PLATFORM_AUTH_URL", "AI_PLATFORM_CONVERSATION_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def client():
    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


# ---------------------------------------------------------------------------
# A. Metrics endpoint: deterministic, no AI, no network
# ---------------------------------------------------------------------------

def test_metrics_endpoint_never_invokes_ai(client, monkeypatch):
    """GET /api/portfolio/intelligence must work with ALL AI entry points dead."""

    def explode(*args, **kwargs):
        raise AssertionError("metrics endpoint must not call AI or the network")

    # Kill the adapter's network primitive and its analysis entry points.
    monkeypatch.setattr(adapter, "urlopen", explode)
    monkeypatch.setattr(adapter, "run_ai_copilot_analysis", explode)
    monkeypatch.setattr(adapter, "_call_ai_platform_agent", explode)

    resp = client.get(
        "/api/portfolio/intelligence"
        "?portfolioId=FL-DEMO&analysisYear=2026&stormEventId=TOR-FL-2026-0612"
    )
    assert resp.status_code == 200


def test_metrics_response_contains_no_ai_advisory_fields(client):
    """The metrics payload is metrics-only: no AI result keys anywhere."""
    data = client.get("/api/portfolio/intelligence").get_json()

    assert set(data.keys()) == {
        "portfolioSummary",
        "propertyIntelligenceResults",
        "watchList",
        "finalPriorityList",
        "capitalActionResults",
        "diagnostics",
    }
    body = str(data)
    for ai_field in ("executiveSummary", "operationalActionPlan", "keyFindings",
                     "stormImpactAssessment", "aiPlatform"):
        assert ai_field not in body, f"AI advisory field {ai_field} leaked into metrics"


def test_metrics_endpoint_is_deterministic_across_calls(client):
    a = client.get("/api/portfolio/intelligence?analysisYear=2026").get_json()
    b = client.get("/api/portfolio/intelligence?analysisYear=2026").get_json()
    assert a == b


# ---------------------------------------------------------------------------
# B. AI advisory endpoint: scenario-only input, full diagnostics
# ---------------------------------------------------------------------------

def test_ai_endpoint_works_from_scenario_only(client):
    """The frontend sends scope + intent; the backend supplies all data."""
    resp = client.post(
        "/api/ai-copilot/analyze",
        json={
            "taskType": "portfolio_review",
            "userQuestion": "What should leadership know?",
            "scenario": {
                "portfolioId": "FL-DEMO",
                "analysisYear": 2026,
                "stormEventId": "TOR-FL-2026-0612",
            },
            "dataContext": {"requestedMetrics": ["insuranceGap", "capitalROI", "priorityRanking"]},
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["mode"] == "mock"  # no credentials in tests -> mock fallback
    assert body["result"]["executiveSummary"]


def test_ai_endpoint_diagnostics_contract(client):
    """Diagnostics must expose mode/stateShape/scope/metric availability."""
    resp = client.post(
        "/api/ai-copilot/analyze",
        json={"taskType": "portfolio_review", "userQuestion": "Summarize.",
              "scenario": {"analysisYear": 2026}},
    )
    body = resp.get_json()
    assert body["mode"] in ("mock", "ai_platform")
    diag = body["diagnostics"]
    for key in ("stateShape", "analysisScope", "availableMetrics",
                "missingMetrics", "usedCalculatedMetrics"):
        assert key in diag, f"diagnostics missing {key}"
    assert diag["stateShape"] == "compact"
    assert diag["analysisScope"]["analysisYear"] == 2026
    assert diag["availableMetrics"]


def test_ai_state_is_built_from_scoped_portfolio_intelligence(monkeypatch):
    """The adapter must consume the SAME scoped PI aggregation, not raw data."""
    calls = []
    real_builder = adapter.build_portfolio_intelligence

    def spy(*args, **kwargs):
        calls.append(kwargs.get("scope"))
        return real_builder(*args, **kwargs)

    monkeypatch.setattr(adapter, "build_portfolio_intelligence", spy)

    adapter.run_ai_copilot_analysis(
        "portfolio_review", "test", {}, compact=True,
        scenario={"analysisYear": 2025},
    )
    assert len(calls) == 1
    assert calls[0]["analysisYear"] == 2025


# ---------------------------------------------------------------------------
# C. Layer 2 operational routes unaffected by the split
# ---------------------------------------------------------------------------

def test_layer2_smoke(client):
    assert client.get("/api/risk/timeline").status_code == 200
    props = client.get("/api/risk/properties")
    assert props.status_code == 200
    assert "riskScore" in props.get_json()["properties"][0]

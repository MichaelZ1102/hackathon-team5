"""Tests for the Layer 3 AI Copilot integration."""

import os
import sys


FLASK_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FLASK_API_DIR not in sys.path:
    sys.path.insert(0, FLASK_API_DIR)

import pytest

import services.ai_copilot_adapter as adapter
from services.ai_copilot_adapter import (
    AIPlatformUnavailable,
    RESPONSE_REQUIRED_FIELDS,
    build_ai_copilot_state,
    load_env_file,
    run_ai_copilot_analysis,
    validate_ai_copilot_response,
)


# AI Platform credential vars that, if present in the real shell env or loaded
# from a local .env, would make the adapter attempt live network calls.
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
    """Keep every test offline and deterministic by default.

    Sets AI_COPILOT_SKIP_DOTENV=1 so load_env_file() never reads the developer's
    real .env, and clears any inherited AI Platform credentials. Tests that need
    platform config (e.g. the Basic-auth protocol test) set their own fake vars
    after this fixture runs; the .env-parsing test re-enables loading explicitly.
    """
    monkeypatch.setenv("AI_COPILOT_SKIP_DOTENV", "1")
    for name in _AI_PLATFORM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_state_builder_includes_available_calculated_metrics():
    state = build_ai_copilot_state(
        task_type="portfolio_review",
        user_question="Which assets need attention?",
        data_context={},
    )

    assert state["taskType"] == "portfolio_review"
    assert state["userQuestion"] == "Which assets need attention?"
    assert "assetHealthScore" in state["availableMetrics"]
    assert "stormImpactLevel" in state["availableMetrics"]
    assert "riskScore_v2" in state["availableMetrics"]
    assert "lossForecast" in state["availableMetrics"]
    assert state["layer1Results"]
    assert state["portfolioSummary"]["propertyCount"] == 14


def test_missing_metrics_are_listed_as_data_gaps_when_context_requests_them():
    state = build_ai_copilot_state(
        task_type="capital_planning",
        user_question="What should we fund?",
        data_context={"requestedMetrics": ["capitalROI", "assetHealthScore"]},
    )

    assert "assetHealthScore" in state["availableMetrics"]
    assert "capitalROI" in state["missingMetrics"]
    assert any("capitalROI" in note for note in state["dataQualityNotes"])


def test_compact_state_consumes_portfolio_intelligence_output():
    from services.ai_copilot_adapter import build_compact_ai_copilot_state

    state = build_compact_ai_copilot_state(
        task_type="capital_planning",
        user_question="Which assets need capital attention?",
        data_context={"requestedMetrics": ["capitalROI"]},
    )

    # Compact contract: top-4 watch list, no raw layer1Results/operationalActions.
    assert len(state["watchList"]) <= 4
    assert state["layer1Results"] == []
    assert state["operationalActions"] == []

    # Real Phase A+B+C metrics flow through from the PI diagnostics.
    assert set(state["availableMetrics"]) == {
        "assetHealthScore", "stormImpactLevel", "riskScore_v2", "lossForecast",
        "insuranceGap", "capitalROI", "priorityRanking",
    }
    # The caller-requested capitalROI is implemented now, so nothing is missing.
    assert state["missingMetrics"] == []

    # Watch-list entries carry real deterministic metric values.
    top = state["watchList"][0]
    assert top["propertyId"]
    assert top["riskScore_v2"] is not None
    assert top["stormImpactLevel"] in ("Low", "Medium", "High", "Severe")
    assert state["portfolioSummary"]["totalProperties"] == 14


def test_compact_run_uses_real_metrics_in_mock_mode():
    response = run_ai_copilot_analysis(
        task_type="portfolio_review",
        user_question="What should leadership know?",
        data_context={},
        compact=True,
    )
    assert response["mode"] == "mock"
    assert response["diagnostics"]["stateShape"] == "compact"
    assert validate_ai_copilot_response(response["result"]) == []
    # The mock executive summary should reflect the real 14-property portfolio,
    # not the degraded "0 properties" output from before the PI summary aliases.
    assert "0 properties were reviewed" not in response["result"]["executiveSummary"]
    assert response["result"]["priorityAssets"], "priorityAssets should be populated"


def test_mock_fallback_works_without_ai_platform_credentials(monkeypatch):
    monkeypatch.delenv("AI_PLATFORM_AGENT_ENDPOINT", raising=False)
    monkeypatch.delenv("AI_PLATFORM_AGENT_ID", raising=False)

    response = run_ai_copilot_analysis(
        task_type="storm_impact",
        user_question="Summarize storm exposure.",
        data_context={},
    )

    assert response["mode"] == "mock"
    assert response["taskType"] == "storm_impact"
    assert validate_ai_copilot_response(response["result"]) == []
    assert response["diagnostics"]["availableMetrics"]
    assert response["diagnostics"]["usedCalculatedMetrics"]
    assert response["diagnostics"]["missingMetrics"] == []


def test_response_contract_validation_reports_missing_fields():
    invalid = {"executiveSummary": "Partial response"}

    problems = validate_ai_copilot_response(invalid)

    assert problems
    for field in RESPONSE_REQUIRED_FIELDS:
        if field != "executiveSummary":
            assert any(field in problem for problem in problems)


def test_response_contract_validation_accepts_complete_response():
    valid = {
        "executiveSummary": "Portfolio is exposed to the active storm scenario.",
        "keyFindings": [],
        "riskDrivers": [],
        "stormImpactAssessment": "",
        "maintenanceAssetHealthInsights": "",
        "financialExposure": "",
        "capitalPlanningRecommendations": [],
        "priorityAssets": [],
        "operationalActionPlan": {
            "immediateActions": [],
            "nearTermActions": [],
            "strategicActions": [],
        },
        "dataGapsConfidence": [],
    }

    assert validate_ai_copilot_response(valid) == []


def test_ai_copilot_endpoint_returns_mock_response_without_credentials(monkeypatch):
    monkeypatch.delenv("AI_PLATFORM_AGENT_ENDPOINT", raising=False)
    monkeypatch.delenv("AI_PLATFORM_AGENT_ID", raising=False)

    from app import app

    client = app.test_client()
    response = client.post(
        "/api/ai-copilot/analyze",
        json={
            "taskType": "portfolio_review",
            "userQuestion": "What should leadership know?",
            "dataContext": {"requestedMetrics": ["capitalROI"]},
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["mode"] == "mock"
    assert body["taskType"] == "portfolio_review"
    assert validate_ai_copilot_response(body["result"]) == []
    # capitalROI is implemented as of Phase C: available, not missing.
    assert "capitalROI" in body["diagnostics"]["availableMetrics"]
    assert "capitalROI" not in body["diagnostics"]["missingMetrics"]
    # The endpoint defaults to the compact, PI-backed state shape.
    assert body["diagnostics"]["stateShape"] == "compact"


def test_ai_copilot_endpoint_can_opt_into_full_state(monkeypatch):
    from app import app

    client = app.test_client()
    response = client.post(
        "/api/ai-copilot/analyze",
        json={
            "taskType": "portfolio_review",
            "userQuestion": "Full state please.",
            "dataContext": {},
            "compact": False,
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["diagnostics"]["stateShape"] == "full"
    assert validate_ai_copilot_response(body["result"]) == []


def test_ai_copilot_endpoint_rejects_unknown_task_type():
    from app import app

    client = app.test_client()
    response = client.post(
        "/api/ai-copilot/analyze",
        json={"taskType": "unsupported", "userQuestion": "Hello", "dataContext": {}},
    )

    assert response.status_code == 400
    assert "taskType" in response.get_json()["error"]


def test_ai_copilot_endpoint_returns_error_when_mock_fallback_disabled(monkeypatch):
    def fail_platform(_state):
        raise AIPlatformUnavailable("AI Platform message text did not contain JSON")

    monkeypatch.setattr(adapter, "_call_ai_platform_agent", fail_platform)
    monkeypatch.setenv("AI_COPILOT_ENABLE_MOCK_FALLBACK", "false")

    from app import app

    client = app.test_client()
    response = client.post(
        "/api/ai-copilot/analyze",
        json={
            "taskType": "portfolio_review",
            "userQuestion": "What should leadership know?",
            "dataContext": {},
        },
    )

    assert response.status_code == 502
    body = response.get_json()
    assert body["mode"] == "error"
    assert body["error"] == "AI Platform call failed"
    assert body["diagnostics"]["aiPlatformError"] == (
        "AI Platform message text did not contain JSON"
    )


def test_ai_platform_call_uses_basic_auth_conversation_protocol(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self.payload.encode("utf-8")

    def fake_urlopen(request, timeout):
        body = request.data.decode("utf-8") if request.data else ""
        calls.append(
            {
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "body": body,
                "timeout": timeout,
            }
        )
        if request.full_url.endswith("/auth/token"):
            return FakeResponse('{"access_token": "TOKEN-123"}')
        return FakeResponse(
            '{"rich_content": {"message": {"text": "```json\\n'
            '{\\"executiveSummary\\": \\"AI platform response\\", '
            '\\"keyFindings\\": [], \\"riskDrivers\\": [], '
            '\\"stormImpactAssessment\\": \\"\\", '
            '\\"maintenanceAssetHealthInsights\\": \\"\\", '
            '\\"financialExposure\\": \\"\\", '
            '\\"capitalPlanningRecommendations\\": [], '
            '\\"priorityAssets\\": [], '
            '\\"operationalActionPlan\\": {'
            '\\"immediateActions\\": [], '
            '\\"nearTermActions\\": [], '
            '\\"strategicActions\\": []'
            '}, '
            '\\"dataGapsConfidence\\": []}'
            '\\n```"}}}'
        )

    monkeypatch.setattr(adapter, "urlopen", fake_urlopen)
    monkeypatch.setenv("AI_PLATFORM_AUTH_URL", "https://meshstage.lessen.com/auth/token")
    monkeypatch.setenv("AI_PLATFORM_USERNAME", "demo-user")
    monkeypatch.setenv("AI_PLATFORM_PASSWORD", "demo-password")
    monkeypatch.setenv("AI_PLATFORM_BASE_URL", "https://meshstage.lessen.com")
    monkeypatch.setenv("AI_PLATFORM_AGENT_ID", "agent-123")
    monkeypatch.setenv("AI_PLATFORM_CONVERSATION_ID", "conversation-456")

    response = run_ai_copilot_analysis(
        task_type="portfolio_review",
        user_question="Summarize the portfolio.",
        data_context={},
    )

    assert response["mode"] == "ai_platform"
    assert response["result"]["executiveSummary"] == "AI platform response"
    assert calls[0]["url"] == "https://meshstage.lessen.com/auth/token"
    assert calls[0]["headers"]["Authorization"].startswith("Basic ")
    assert calls[1]["url"] == (
        "https://meshstage.lessen.com/onebrain/conversation/"
        "agent-123/conversation-456"
    )
    assert calls[1]["headers"]["Authorization"] == "Bearer TOKEN-123"
    payload = adapter.json.loads(calls[1]["body"])
    assert payload["text"] == "Summarize the portfolio."
    assert payload["states"][0]["key"] == "aiCopilotState"
    assert payload["states"][0]["value"]["taskType"] == "portfolio_review"
    assert len(payload["states"][0]["value"]["layer1Results"]) <= 20
    assert payload["states"][0]["value"]["stateMeta"]["fullLayer1ResultsCount"] == 56


def test_load_env_file_reads_values_without_overriding_existing_env(tmp_path, monkeypatch):
    # This test explicitly exercises real .env loading, so opt back in (the
    # autouse fixture disables it elsewhere) and point at a temp file.
    monkeypatch.delenv("AI_COPILOT_SKIP_DOTENV", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AI_PLATFORM_USERNAME=from-file",
                "AI_PLATFORM_PASSWORD=\"quoted password\"",
                "AI_PLATFORM_BASE_URL=https://meshstage.lessen.com",
                "AI_PLATFORM_AGENT_ID=agent-from-file",
                "AI_PLATFORM_CONVERSATION_ID=conversation-from-file",
                "AI_PLATFORM_STATE_KEY=aiCopilotState",
                "COMMENTED_VALUE=kept # inline comment",
                "# ignored comment",
                "",
            ]
        ),
        encoding="utf-8",
    )
    for key in (
        "AI_PLATFORM_USERNAME",
        "AI_PLATFORM_PASSWORD",
        "AI_PLATFORM_BASE_URL",
        "AI_PLATFORM_AGENT_ID",
        "AI_PLATFORM_CONVERSATION_ID",
        "AI_PLATFORM_STATE_KEY",
        "COMMENTED_VALUE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AI_PLATFORM_USERNAME", "from-shell")

    loaded = load_env_file(env_file)

    assert loaded["AI_PLATFORM_PASSWORD"] == "quoted password"
    assert loaded["AI_PLATFORM_AGENT_ID"] == "agent-from-file"
    assert os.environ["AI_PLATFORM_USERNAME"] == "from-shell"
    assert os.environ["AI_PLATFORM_PASSWORD"] == "quoted password"
    assert os.environ["COMMENTED_VALUE"] == "kept"

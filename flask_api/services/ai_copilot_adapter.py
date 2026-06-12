"""Layer 3 AI Copilot adapter for the internal AI Platform Agent model.

This module intentionally does not import OpenAI or Azure OpenAI SDKs. It builds
the State contract from deterministic demo outputs, optionally calls the
internal AI Platform Agent API, validates the structured response shape, and
falls back to a deterministic mock response for local demos.
"""

import base64
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.analysis_scope import resolve_analysis_scope, select_storm_event
from services.capital_planning import compute_phase_b
from services.data_loader import load_json
from services.portfolio_intelligence import compute_all_asset_health
from services.portfolio_intelligence_api import build_portfolio_intelligence
from services.risk_engine import DEFAULT_ANALYSIS_TIME, analyze_risk


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
TEMPLATE_VERSION = "property_portfolio_copilot_template.v1"
STATE_VERSION = "ai_copilot_state.v1"
RESPONSE_VERSION = "ai_copilot_response.v1"
DEFAULT_AGENT_ID = "property_portfolio_copilot"
VALID_TASK_TYPES = ("portfolio_review", "storm_impact", "capital_planning")

RESPONSE_REQUIRED_FIELDS = (
    "executiveSummary",
    "keyFindings",
    "riskDrivers",
    "stormImpactAssessment",
    "maintenanceAssetHealthInsights",
    "financialExposure",
    "capitalPlanningRecommendations",
    "priorityAssets",
    "operationalActionPlan",
    "dataGapsConfidence",
)

OPERATIONAL_ACTION_FIELDS = (
    "immediateActions",
    "nearTermActions",
    "strategicActions",
)
DEFAULT_PLATFORM_LAYER1_LIMIT = 20
DEFAULT_PLATFORM_ACTION_LIMIT = 12


def build_ai_copilot_state(task_type, user_question, data_context=None):
    """Build the State payload for the AI Platform Agent from demo data."""
    if task_type not in VALID_TASK_TYPES:
        raise ValueError(f"taskType must be one of {', '.join(VALID_TASK_TYPES)}")

    data_context = dict(data_context or {})
    analysis_time = data_context.get("time", DEFAULT_ANALYSIS_TIME)

    layer2 = analyze_risk(analysis_time)
    phase_a_results = compute_all_asset_health()
    phase_b = compute_phase_b()
    layer1_results = _combine_layer1_results(phase_a_results, phase_b)

    available_metrics = sorted({result["metric"] for result in layer1_results})
    available_metrics.extend(_layer2_metric_names(layer2))
    available_metrics = sorted(set(available_metrics))

    requested_metrics = data_context.get("requestedMetrics") or []
    missing_metrics = sorted(
        {
            str(metric)
            for metric in requested_metrics
            if str(metric) not in available_metrics
        }
    )

    data_quality_notes = _collect_data_quality_notes(layer1_results)
    for metric in missing_metrics:
        data_quality_notes.append(f"Requested metric is not available in State: {metric}")

    state = {
        "taskType": task_type,
        "userQuestion": str(user_question or ""),
        "portfolioSummary": _build_portfolio_summary(layer2, phase_b),
        "stormEvent": _build_storm_event(layer2),
        "layer1Results": layer1_results,
        "watchList": _build_watch_list(layer2, phase_b, phase_a_results),
        "operationalActions": _build_operational_actions(layer2),
        "availableMetrics": available_metrics,
        "missingMetrics": missing_metrics,
        "dataQualityNotes": _unique_preserve_order(data_quality_notes),
    }
    return state


COMPACT_WATCH_LIST_SIZE = 4
COMPACT_MAX_DATA_QUALITY_NOTES = 8


def build_compact_ai_copilot_state(task_type, user_question, data_context=None,
                                   scope=None):
    """Build a compact State payload from the Portfolio Intelligence API output.

    This is the integration point requested for Layer 3: instead of assembling
    raw Layer 1/2 structures, it consumes the same read-only aggregation the
    frontend uses (``build_portfolio_intelligence``) and trims it to what the
    agent needs:

    - top ``COMPACT_WATCH_LIST_SIZE`` assets only, ordered by the final Phase C
      priorityRanking and carrying insuranceGap / bestCapitalAction (all
      computed by the backend engines, never by the AI)
    - NO full raw layer1Results (kept as an empty list for schema compatibility)
    - NO full raw operationalActions (empty list)
    - availableMetrics / missingMetrics from PI diagnostics
    - concise, de-duplicated dataQualityNotes (PI warnings + requested-metric gaps)

    ``scope`` is a resolved analysis scope (services.analysis_scope); the same
    scoped Portfolio Intelligence result the frontend sees feeds the agent, so
    the frontend never has to ship raw data to the Copilot.
    """
    if task_type not in VALID_TASK_TYPES:
        raise ValueError(f"taskType must be one of {', '.join(VALID_TASK_TYPES)}")

    if scope is None:
        scope = resolve_analysis_scope()

    data_context = dict(data_context or {})
    intelligence = build_portfolio_intelligence(scope=scope)
    summary = intelligence["portfolioSummary"]
    diagnostics = intelligence["diagnostics"]
    analysis_scope = diagnostics.get("analysisScope", {})
    results_by_property = {
        r["propertyId"]: r for r in intelligence.get("propertyIntelligenceResults", [])
    }

    available_metrics = sorted(set(diagnostics.get("includedMetrics", [])))
    pi_missing = set(diagnostics.get("missingMetrics", []))

    # Honor caller-requested metrics: anything requested but not available is a gap.
    requested_metrics = data_context.get("requestedMetrics") or []
    for metric in requested_metrics:
        if str(metric) not in available_metrics:
            pi_missing.add(str(metric))
    missing_metrics = sorted(pi_missing)

    # Compact watch list: top N by the FINAL Phase C priorityRanking, with the
    # deterministic backend-calculated metrics the agent reasons over (the AI
    # never computes insuranceGap/capitalROI/priorityRanking itself).
    ranked = intelligence.get("finalPriorityList") or intelligence["watchList"]
    watch_list = []
    for item in ranked[:COMPACT_WATCH_LIST_SIZE]:
        pid = item.get("propertyId")
        full = results_by_property.get(pid, {})
        rv = full.get("riskScore_v2") or {}
        si = full.get("stormImpactLevel") or {}
        lf = full.get("lossForecast") or {}
        ah = full.get("assetHealthScore") or {}
        ig = full.get("insuranceGap") or {}
        pr = full.get("priorityRanking") or {}
        best = full.get("bestCapitalAction")
        watch_list.append(
            {
                "propertyId": pid,
                "name": item.get("propertyName"),
                "county": item.get("county"),
                "priorityRank": pr.get("priorityRank"),
                "priorityScore": pr.get("priorityScore"),
                "riskScore_v2": rv.get("score"),
                "riskBand": rv.get("band"),
                "stormImpactLevel": si.get("level"),
                "stormImpactScore": si.get("score"),
                "distanceToStormPathMiles": si.get("distanceMiles"),
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
                "confidence": item.get("confidence"),
                "drivers": (pr.get("drivers") or rv.get("drivers") or [])[:3],
            }
        )

    # Concise data-quality notes: PI warnings first, then requested-metric gaps.
    data_quality_notes = list(diagnostics.get("warnings", []))
    for metric in missing_metrics:
        if metric in (str(m) for m in requested_metrics):
            data_quality_notes.append(f"Requested metric is not available in State: {metric}")
    data_quality_notes = _unique_preserve_order(data_quality_notes)[:COMPACT_MAX_DATA_QUALITY_NOTES]

    compact_summary = {
        "totalProperties": summary.get("totalProperties"),
        "severeImpactCount": summary.get("severeImpactCount"),
        "affectedPropertyCount": summary.get("affectedPropertyCount"),
        # All portfolio properties are evaluated; storm impact decays with
        # distance to the storm path ("None" = outside meaningful range).
        "stormImpactDistribution": diagnostics.get("stormImpactDistribution"),
        "highRiskCount": summary.get("highRiskCount"),
        "totalLossForecast": summary.get("totalLossForecast"),
        "averageAssetHealthScore": summary.get("averageAssetHealthScore"),
        "totalInsuranceGap": summary.get("totalInsuranceGap"),
        "topPriorityPropertyId": summary.get("topPriorityPropertyId"),
        "topRiskDrivers": summary.get("topRiskDrivers", []),
        "calculationVersion": diagnostics.get("calculationVersion"),
        # Alias so the shared mock-response builder (which reads propertyCount)
        # renders real numbers from the compact summary. affectedPropertyCount
        # above is the real distance-decay rollup (Severe+High+Medium).
        "propertyCount": summary.get("totalProperties"),
    }

    state = {
        "taskType": task_type,
        "userQuestion": str(user_question or ""),
        # The scope the backend used to build this state, so the agent (and
        # anyone reading the payload) knows which slice of data it describes.
        "analysisScope": {
            "portfolioId": analysis_scope.get("portfolioId"),
            "analysisYear": analysis_scope.get("analysisYear"),
            "stormEventId": analysis_scope.get("stormEventId"),
            "workOrderWindow": analysis_scope.get("workOrderWindow"),
        },
        "portfolioSummary": compact_summary,
        "stormEvent": _build_compact_storm_event(summary, scope),
        # Raw per-property results intentionally omitted from the compact state;
        # the agent works from the watch list + summary instead.
        "layer1Results": [],
        "watchList": watch_list,
        "operationalActions": [],
        "availableMetrics": available_metrics,
        "missingMetrics": missing_metrics,
        "dataQualityNotes": data_quality_notes,
    }
    return state


def _build_compact_storm_event(summary, scope):
    """Minimal storm context for the compact state, selected by the scope."""
    _, storm_meta, _ = select_storm_event(scope)
    return {
        "eventId": storm_meta.get("stormEventId"),
        "eventName": storm_meta.get("eventName"),
        "eventType": storm_meta.get("eventType"),
        "windSpeedMph": storm_meta.get("windSpeedMph"),
        "rainfallForecastInches": storm_meta.get("rainfallForecastInches"),
        "severeImpactCount": summary.get("severeImpactCount"),
    }


def run_ai_copilot_analysis(task_type, user_question, data_context=None, compact=False,
                            scenario=None):
    """Run the Copilot via AI Platform when configured, otherwise use mock.

    When ``compact`` is True the State is built from the Portfolio Intelligence
    API output (top-4 watch list, no raw layer1Results / operationalActions),
    which is the shape the frontend and the AI Platform agent consume. When
    False the legacy full-State builder is used (preserves existing behavior).

    ``scenario`` is the caller's analysis scope, e.g.::

        {"portfolioId": "FL-DEMO", "analysisYear": 2026, "stormEventId": "..."}

    All fields are optional; missing fields fall back to the demo defaults.
    Raises ValueError when scenario.analysisYear is not a usable year. The
    scope only drives calculations on the compact (Portfolio Intelligence)
    path; the legacy full path keeps its fixed demo scenario and says so in
    the diagnostics.
    """
    scenario = dict(scenario or {})
    scope = resolve_analysis_scope(
        portfolio_id=scenario.get("portfolioId"),
        analysis_year=scenario.get("analysisYear"),
        storm_event_id=scenario.get("stormEventId"),
    )

    load_env_file()
    if compact:
        state = build_compact_ai_copilot_state(
            task_type, user_question, data_context, scope=scope
        )
        scope_diagnostics = state["analysisScope"]
    else:
        state = build_ai_copilot_state(task_type, user_question, data_context)
        scope_diagnostics = {
            "portfolioId": scope["portfolioId"],
            "analysisYear": scope["analysisYear"],
            "stormEventId": scope["stormEventId"],
            "note": "Legacy full state uses the fixed demo scenario; "
                    "scope drives calculations on the compact path only.",
        }
    diagnostics = {
        "templateVersion": TEMPLATE_VERSION,
        "stateVersion": STATE_VERSION,
        "stateShape": "compact" if compact else "full",
        "analysisScope": scope_diagnostics,
        "availableMetrics": state["availableMetrics"],
        "missingMetrics": state["missingMetrics"],
        "usedCalculatedMetrics": state["availableMetrics"],
    }

    try:
        result = _call_ai_platform_agent(state)
        mode = "ai_platform"
    except AIPlatformUnavailable as exc:
        diagnostics["aiPlatformError"] = str(exc)
        if not _mock_fallback_enabled():
            return {
                "mode": "error",
                "taskType": task_type,
                "error": "AI Platform call failed",
                "diagnostics": diagnostics,
            }
        result = build_mock_ai_copilot_response(state)
        mode = "mock"

    problems = validate_ai_copilot_response(result)
    if problems:
        diagnostics["aiPlatformValidationErrors"] = problems
        if mode == "ai_platform" and not _mock_fallback_enabled():
            return {
                "mode": "error",
                "taskType": task_type,
                "error": "AI Platform response failed validation",
                "diagnostics": diagnostics,
            }
        result = build_mock_ai_copilot_response(state)
        mode = "mock"

    return {
        "mode": mode,
        "taskType": task_type,
        "result": result,
        "diagnostics": diagnostics,
    }


def validate_ai_copilot_response(response):
    """Validate the required response contract shape without extra packages."""
    problems = []
    if not isinstance(response, dict):
        return ["response must be an object"]

    expected_types = {
        "executiveSummary": str,
        "keyFindings": list,
        "riskDrivers": list,
        "stormImpactAssessment": str,
        "maintenanceAssetHealthInsights": str,
        "financialExposure": str,
        "capitalPlanningRecommendations": list,
        "priorityAssets": list,
        "operationalActionPlan": dict,
        "dataGapsConfidence": list,
    }

    for field in RESPONSE_REQUIRED_FIELDS:
        if field not in response:
            problems.append(f"missing required field: {field}")
            continue
        expected_type = expected_types[field]
        if not isinstance(response[field], expected_type):
            problems.append(
                f"field {field} should be {expected_type.__name__}, "
                f"got {type(response[field]).__name__}"
            )

    action_plan = response.get("operationalActionPlan")
    if isinstance(action_plan, dict):
        for field in OPERATIONAL_ACTION_FIELDS:
            if field not in action_plan:
                problems.append(f"operationalActionPlan missing required field: {field}")
            elif not isinstance(action_plan[field], list):
                problems.append(f"operationalActionPlan.{field} should be list")

    return problems


def _mock_fallback_enabled():
    value = os.getenv("AI_COPILOT_ENABLE_MOCK_FALLBACK", "true").strip().lower()
    return value not in ("0", "false", "no", "off")


def load_env_file(path=DEFAULT_ENV_FILE):
    """Load simple KEY=VALUE settings from .env without overriding env vars.

    Set AI_COPILOT_SKIP_DOTENV=1 to make this a no-op (used by tests so they do
    not pick up real local credentials and attempt live network calls). Normal
    runtime leaves the variable unset, so .env is still read as before.
    """
    skip = os.getenv("AI_COPILOT_SKIP_DOTENV", "").strip().lower()
    if skip in ("1", "true", "yes", "on"):
        return {}

    env_path = Path(path)
    if not env_path.exists():
        return {}

    loaded = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _parse_env_value(value)
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


def build_mock_ai_copilot_response(state):
    """Build a deterministic local-demo response from supplied State only."""
    summary = state["portfolioSummary"]
    watch_list = state["watchList"]
    operational_actions = state["operationalActions"]
    top_assets = watch_list[:5]

    highest_risk = top_assets[0] if top_assets else {}
    executive_summary = (
        f"{summary.get('propertyCount', 0)} properties were reviewed using "
        f"{', '.join(state['availableMetrics'])}. "
        f"Top attention should go to {highest_risk.get('name', 'the watch list')} "
        "based on the provided deterministic metrics."
    )

    key_findings = [
        f"{summary.get('affectedPropertyCount', 0)} properties have Medium-or-worse storm impact (all properties are evaluated; impact decays with distance).",
        f"{len(watch_list)} assets are on the Copilot watch list.",
    ]
    if summary.get("totalLossForecast") is not None:
        key_findings.append(
            f"Layer 1 lossForecast totals ${summary['totalLossForecast']:,.0f} across assets with available valuation inputs."
        )

    risk_drivers = []
    for asset in top_assets[:3]:
        for driver in asset.get("drivers", [])[:2]:
            risk_drivers.append(f"{asset['name']}: {driver}")

    immediate_actions = [
        action["action"]
        for action in operational_actions
        if action.get("horizon") == "immediate"
    ][:6]
    near_term_actions = [
        action["action"]
        for action in operational_actions
        if action.get("horizon") == "near_term"
    ][:6]

    return {
        "executiveSummary": executive_summary,
        "keyFindings": key_findings,
        "riskDrivers": risk_drivers,
        "stormImpactAssessment": _mock_storm_assessment(state),
        "maintenanceAssetHealthInsights": _mock_asset_health_insights(top_assets),
        "financialExposure": _mock_financial_exposure(summary),
        "capitalPlanningRecommendations": _mock_capital_recommendations(state),
        "priorityAssets": top_assets,
        "operationalActionPlan": {
            "immediateActions": immediate_actions,
            "nearTermActions": near_term_actions,
            "strategicActions": _mock_strategic_actions(state),
        },
        "dataGapsConfidence": state["dataQualityNotes"] or [
            "Mock fallback used deterministic State only; no AI Platform call was completed."
        ],
    }


def _call_ai_platform_agent(state):
    if _has_conversation_platform_config():
        return _call_conversation_platform_agent(state)

    endpoint = os.getenv("AI_PLATFORM_AGENT_ENDPOINT")
    if not endpoint:
        raise AIPlatformUnavailable("AI Platform endpoint is not configured")

    payload = {
        "agentId": os.getenv("AI_PLATFORM_AGENT_ID", DEFAULT_AGENT_ID),
        "templateVersion": TEMPLATE_VERSION,
        "stateVersion": STATE_VERSION,
        "responseVersion": RESPONSE_VERSION,
        "state": state,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("AI_PLATFORM_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = float(os.getenv("AI_PLATFORM_TIMEOUT_SECONDS", "5"))
    request = Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise AIPlatformUnavailable(str(exc)) from exc

    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise AIPlatformUnavailable("AI Platform returned non-JSON response") from exc

    if isinstance(parsed, dict) and isinstance(parsed.get("result"), dict):
        return parsed["result"]
    if isinstance(parsed, dict):
        return parsed
    raise AIPlatformUnavailable("AI Platform response was not an object")


def _has_conversation_platform_config():
    required = (
        "AI_PLATFORM_USERNAME",
        "AI_PLATFORM_PASSWORD",
        "AI_PLATFORM_AGENT_ID",
        "AI_PLATFORM_CONVERSATION_ID",
    )
    return all(os.getenv(name) for name in required)


def _call_conversation_platform_agent(state):
    token = _fetch_ai_platform_token()
    agent_id = os.getenv("AI_PLATFORM_AGENT_ID")
    conversation_id = os.getenv("AI_PLATFORM_CONVERSATION_ID")
    base_url = os.getenv("AI_PLATFORM_BASE_URL", "https://meshstage.lessen.com").rstrip("/")
    endpoint = os.getenv(
        "AI_PLATFORM_CONVERSATION_ENDPOINT",
        f"{base_url}/onebrain/conversation/{agent_id}/{conversation_id}",
    )
    payload = {
        "text": state.get("userQuestion", ""),
        "states": [
            {
                "key": os.getenv("AI_PLATFORM_STATE_KEY", "aiCopilotState"),
                "value": _build_platform_state(state),
            }
        ],
    }

    parsed = _post_json(endpoint, payload, {"Authorization": f"Bearer {token}"})
    return _extract_ai_platform_result(parsed)


def _build_platform_state(state):
    """Trim high-volume state fields before sending to the AI Platform."""
    layer1_limit = int(
        os.getenv("AI_PLATFORM_MAX_LAYER1_RESULTS", str(DEFAULT_PLATFORM_LAYER1_LIMIT))
    )
    action_limit = int(
        os.getenv("AI_PLATFORM_MAX_OPERATIONAL_ACTIONS", str(DEFAULT_PLATFORM_ACTION_LIMIT))
    )
    priority_property_ids = {
        item.get("propertyId") for item in state.get("watchList", []) if item.get("propertyId")
    }
    priority_layer1_results = [
        result
        for result in state.get("layer1Results", [])
        if result.get("propertyId") in priority_property_ids
    ]

    platform_state = dict(state)
    platform_state["layer1Results"] = priority_layer1_results[:layer1_limit]
    platform_state["operationalActions"] = state.get("operationalActions", [])[:action_limit]
    platform_state["stateMeta"] = {
        "fullLayer1ResultsCount": len(state.get("layer1Results", [])),
        "sentLayer1ResultsCount": len(platform_state["layer1Results"]),
        "fullOperationalActionsCount": len(state.get("operationalActions", [])),
        "sentOperationalActionsCount": len(platform_state["operationalActions"]),
        "platformStateIsTrimmed": True,
    }
    return platform_state


def _fetch_ai_platform_token():
    username = os.getenv("AI_PLATFORM_USERNAME")
    password = os.getenv("AI_PLATFORM_PASSWORD")
    auth_url = os.getenv(
        "AI_PLATFORM_AUTH_URL",
        f"{os.getenv('AI_PLATFORM_BASE_URL', 'https://meshstage.lessen.com').rstrip('/')}/auth/token",
    )
    credentials = f"{username}:{password}"
    encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    headers = {"Authorization": f"Basic {encoded_credentials}", "Content-Type": "*/*"}

    request = Request(auth_url, headers=headers, method="POST")
    timeout = float(os.getenv("AI_PLATFORM_TIMEOUT_SECONDS", "5"))
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise AIPlatformUnavailable(str(exc)) from exc

    token = _extract_token(raw)
    if not token:
        raise AIPlatformUnavailable("AI Platform auth response did not include a token")
    return token


def _post_json(endpoint, payload, headers=None):
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    body = json.dumps(payload).encode("utf-8")
    request = Request(endpoint, data=body, headers=request_headers, method="POST")
    timeout = float(os.getenv("AI_PLATFORM_TIMEOUT_SECONDS", "5"))
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise AIPlatformUnavailable(str(exc)) from exc

    try:
        return json.loads(raw)
    except ValueError as exc:
        raise AIPlatformUnavailable("AI Platform returned non-JSON response") from exc


def _extract_token(raw):
    try:
        parsed = json.loads(raw)
    except ValueError:
        return raw.strip()
    if isinstance(parsed, dict):
        return parsed.get("token") or parsed.get("access_token")
    if isinstance(parsed, str):
        return parsed.strip()
    return None


def _extract_ai_platform_result(parsed):
    if not isinstance(parsed, dict):
        raise AIPlatformUnavailable("AI Platform response was not an object")

    if isinstance(parsed.get("result"), dict):
        return parsed["result"]

    text = (
        parsed.get("rich_content", {})
        .get("message", {})
        .get("text")
    )
    if isinstance(text, str):
        return _parse_ai_platform_text_response(text)

    if all(field in parsed for field in RESPONSE_REQUIRED_FIELDS):
        return parsed

    raise AIPlatformUnavailable("AI Platform response did not contain a Copilot result")


def _parse_ai_platform_text_response(text):
    content = text.strip()
    if "```json" in content:
        content = content.split("```json", 1)[1]
        content = content.split("```", 1)[0]
    elif "```" in content:
        content = content.split("```", 1)[1]
        content = content.split("```", 1)[0]

    try:
        parsed = json.loads(content.strip())
    except ValueError as exc:
        raise AIPlatformUnavailable("AI Platform message text did not contain JSON") from exc
    if not isinstance(parsed, dict):
        raise AIPlatformUnavailable("AI Platform message JSON was not an object")
    return parsed


def _parse_env_value(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    if "#" in value:
        value = value.split("#", 1)[0].rstrip()
    return value


def _combine_layer1_results(phase_a_results, phase_b):
    results = list(phase_a_results)
    for metric_results in phase_b.values():
        results.extend(metric_results)
    return sorted(results, key=lambda result: (result.get("propertyId", ""), result.get("metric", "")))


def _layer2_metric_names(layer2):
    names = {"riskScore", "riskLevel", "estimatedRepairExposure"}
    if layer2.get("portfolioSummary"):
        names.add("portfolioSummary")
    return sorted(names)


def _build_portfolio_summary(layer2, phase_b):
    properties = layer2.get("properties", [])
    loss_results = phase_b.get("lossForecast", [])
    computable_losses = [
        item["expectedLoss"] for item in loss_results if item.get("expectedLoss") is not None
    ]
    return {
        "eventId": layer2.get("eventId"),
        "analysisTime": layer2.get("analysisTime"),
        "scenarioStage": layer2.get("scenarioStage"),
        "propertyCount": len(load_json("properties.json").get("properties", [])),
        "affectedPropertyCount": len(properties),
        "layer2Summary": layer2.get("portfolioSummary", {}),
        "totalEstimatedRepairExposure": layer2.get("portfolioSummary", {}).get(
            "totalEstimatedRepairExposure"
        ),
        "totalLossForecast": round(sum(computable_losses), 2) if computable_losses else None,
    }


def _build_storm_event(layer2):
    try:
        storm_path = load_json("storm_path.json")
    except (FileNotFoundError, ValueError):
        storm_path = {}
    return {
        "eventId": layer2.get("eventId") or storm_path.get("stormEventId"),
        "analysisTime": layer2.get("analysisTime"),
        "scenarioStage": layer2.get("scenarioStage"),
        "eventName": storm_path.get("eventName"),
        "eventType": storm_path.get("eventType"),
        "category": storm_path.get("category"),
        "windSpeedMph": storm_path.get("windSpeedMph"),
        "rainfallForecastInches": storm_path.get("rainfallForecastInches"),
        "impactWindowStart": storm_path.get("impactWindowStart"),
        "impactWindowEnd": storm_path.get("impactWindowEnd"),
    }


def _build_watch_list(layer2, phase_b, phase_a_results):
    layer2_by_property = {item["propertyId"]: item for item in layer2.get("properties", [])}
    health_by_property = {item["propertyId"]: item for item in phase_a_results}
    storm_by_property = {
        item["propertyId"]: item for item in phase_b.get("stormImpactLevel", [])
    }
    risk_v2_by_property = {item["propertyId"]: item for item in phase_b.get("riskScore_v2", [])}
    loss_by_property = {item["propertyId"]: item for item in phase_b.get("lossForecast", [])}

    watch_list = []
    for property_id, layer2_item in layer2_by_property.items():
        health = health_by_property.get(property_id, {})
        storm = storm_by_property.get(property_id, {})
        risk_v2 = risk_v2_by_property.get(property_id, {})
        loss = loss_by_property.get(property_id, {})
        drivers = []
        drivers.extend(layer2_item.get("riskDrivers", [])[:2])
        drivers.extend(risk_v2.get("drivers", [])[:2])
        watch_list.append(
            {
                "propertyId": property_id,
                "name": layer2_item.get("name"),
                "market": layer2_item.get("market"),
                "riskLevel": layer2_item.get("riskLevel"),
                "riskScore": layer2_item.get("riskScore"),
                "assetHealthScore": health.get("score"),
                "stormImpactLevel": storm.get("level"),
                "riskScore_v2": risk_v2.get("score"),
                "lossForecast": loss.get("expectedLoss"),
                "estimatedRepairExposure": layer2_item.get("estimatedRepairExposure"),
                "drivers": drivers,
            }
        )
    return sorted(
        watch_list,
        key=lambda item: (
            -(item.get("riskScore_v2") or 0),
            -(item.get("riskScore") or 0),
            item.get("propertyId") or "",
        ),
    )


def _build_operational_actions(layer2):
    actions = []
    for property_result in layer2.get("properties", []):
        for action in property_result.get("recommendedActions", [])[:3]:
            actions.append(
                {
                    "propertyId": property_result.get("propertyId"),
                    "propertyName": property_result.get("name"),
                    "horizon": "immediate",
                    "action": action,
                    "source": "Layer 2 recommendedActions",
                }
            )
        for draft in property_result.get("recommendedDraftWorkOrders", [])[:2]:
            actions.append(
                {
                    "propertyId": property_result.get("propertyId"),
                    "propertyName": property_result.get("name"),
                    "horizon": "near_term",
                    "action": draft.get("title") or draft.get("description"),
                    "source": "Layer 2 recommendedDraftWorkOrders",
                    "priority": draft.get("priority"),
                }
            )
    return actions


def _collect_data_quality_notes(layer1_results):
    notes = []
    for result in layer1_results:
        for note in result.get("dataQualityNotes", []):
            notes.append(f"{result.get('propertyId')} {result.get('metric')}: {note}")
    return notes


def _mock_storm_assessment(state):
    storm = state["stormEvent"]
    summary = state["portfolioSummary"]
    if storm.get("eventName"):
        return (
            f"{storm['eventName']} is represented in State with "
            f"{storm.get('windSpeedMph', 'unknown')} mph wind and "
            f"{summary.get('affectedPropertyCount', 0)} affected Layer 2 properties."
        )
    return "Storm assessment is limited because storm event details are missing from State."


def _mock_asset_health_insights(priority_assets):
    weak_assets = [
        asset for asset in priority_assets if (asset.get("assetHealthScore") or 100) < 60
    ]
    if weak_assets:
        names = ", ".join(asset["name"] for asset in weak_assets[:3])
        return f"Asset-health attention is concentrated in {names} based on assetHealthScore."
    return "Available assetHealthScore values do not show a sub-60 priority asset in the top watch list."


def _mock_financial_exposure(summary):
    parts = []
    if summary.get("totalEstimatedRepairExposure") is not None:
        parts.append(
            f"Layer 2 repair exposure totals ${summary['totalEstimatedRepairExposure']:,.0f}."
        )
    if summary.get("totalLossForecast") is not None:
        parts.append(f"Layer 1 lossForecast totals ${summary['totalLossForecast']:,.0f}.")
    if not parts:
        return "Financial exposure is limited because exposure metrics are missing from State."
    return " ".join(parts)


def _mock_capital_recommendations(state):
    if "capitalROI" in state["missingMetrics"]:
        return [
            "Use available lossForecast and riskScore_v2 to shortlist assets, but defer ROI ranking until capitalROI is available."
        ]
    return [
        "Prioritize capital review for assets that combine high riskScore_v2, weak assetHealthScore, and material lossForecast."
    ]


def _mock_strategic_actions(state):
    actions = []
    if state["taskType"] == "capital_planning":
        actions.append("Build a capital planning view from riskScore_v2, lossForecast, and available action data.")
    actions.append("Track missing metrics as explicit confidence gaps before making investment commitments.")
    return actions


def _unique_preserve_order(items):
    seen = set()
    unique = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


class AIPlatformUnavailable(Exception):
    """Raised when the internal AI Platform cannot provide a valid response."""

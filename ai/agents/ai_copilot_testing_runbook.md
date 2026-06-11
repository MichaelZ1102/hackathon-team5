# AI Copilot Testing Runbook

This document explains how to test the Layer 3 AI Copilot integration against the internal AI Platform Agent.

## 1. Files Involved

- Backend endpoint: `POST /api/ai-copilot/analyze`
- Adapter: `flask_api/services/ai_copilot_adapter.py`
- Local config: `.env`
- Example config: `.env.example`
- Platform prompt: `ai/agents/property_portfolio_copilot_platform_prompt.md`

## 2. Required AI Platform Agent Setup

In the AI Platform Agent prompt/template, configure the prompt from:

```text
ai/agents/property_portfolio_copilot_platform_prompt.md
```

The prompt expects a dynamic state parameter named:

```text
aiCopilotState
```

The template variable syntax should be:

```text
{{ aiCopilotState }}
```

The backend sends this state through the conversation API payload:

```json
{
  "text": "user question",
  "states": [
    {
      "key": "aiCopilotState",
      "value": {
        "taskType": "portfolio_review",
        "userQuestion": "...",
        "portfolioSummary": {},
        "stormEvent": {},
        "layer1Results": [],
        "watchList": [],
        "operationalActions": [],
        "availableMetrics": [],
        "missingMetrics": [],
        "dataQualityNotes": []
      }
    }
  ]
}
```

## 3. Local `.env` Settings

Create or edit the root `.env` file:

```bash
/Users/pete/Documents/Src/AIHackathon/hackathon-team5/.env
```

Required values:

```bash
AI_PLATFORM_USERNAME=
AI_PLATFORM_PASSWORD=
AI_PLATFORM_AGENT_ID=
AI_PLATFORM_CONVERSATION_ID=
AI_PLATFORM_BASE_URL=https://meshstage.lessen.com
```

Optional values:

```bash
AI_PLATFORM_AUTH_URL=https://meshstage.lessen.com/auth/token
AI_PLATFORM_CONVERSATION_ENDPOINT=
AI_PLATFORM_STATE_KEY=aiCopilotState
AI_PLATFORM_TIMEOUT_SECONDS=20
```

Notes:

- Do not commit `.env`.
- `.env` is already ignored by git.
- Use `.env.example` as the shareable template.
- If `AI_PLATFORM_TIMEOUT_SECONDS` is missing, the default is 5 seconds.
- During manual testing, 20 seconds is safer because the AI Platform can be slow.

## 4. Start the Flask API

From the project root:

```bash
cd /Users/pete/Documents/Src/AIHackathon/hackathon-team5
flask_api/.venv/bin/python flask_api/app.py
```

The API should start at:

```text
http://127.0.0.1:5000
```

## 5. Test Through the HTTP Endpoint

In another terminal:

```bash
curl -s http://127.0.0.1:5000/api/ai-copilot/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "taskType": "portfolio_review",
    "userQuestion": "Please summarize portfolio risk for leadership.",
    "scenario": {
      "portfolioId": "FL-DEMO",
      "analysisYear": 2026,
      "stormEventId": "TOR-FL-2026-0612"
    },
    "dataContext": {
      "requestedMetrics": ["insuranceGap", "capitalROI", "priorityRanking"]
    }
  }'
```

`scenario` is the optional analysis scope (all fields optional; defaults are
`FL-DEMO` / `2026` / the current demo storm). The backend builds the scoped
Copilot state from the Portfolio Intelligence result itself — the frontend
must NOT pass raw portfolio data. The resolved scope is echoed back in
`diagnostics.analysisScope`.

Expected success signal:

```json
{
  "mode": "ai_platform",
  "taskType": "portfolio_review",
  "result": {
    "executiveSummary": "...",
    "keyFindings": [],
    "riskDrivers": [],
    "stormImpactAssessment": "...",
    "maintenanceAssetHealthInsights": "...",
    "financialExposure": "...",
    "capitalPlanningRecommendations": [],
    "priorityAssets": [],
    "operationalActionPlan": {
      "immediateActions": [],
      "nearTermActions": [],
      "strategicActions": []
    },
    "dataGapsConfidence": []
  },
  "diagnostics": {
    "templateVersion": "property_portfolio_copilot_template.v1",
    "stateVersion": "ai_copilot_state.v1",
    "availableMetrics": [],
    "missingMetrics": [],
    "usedCalculatedMetrics": []
  }
}
```

If `mode` is `mock`, the backend did not receive a valid AI Platform response and used local fallback.

## 6. Direct Adapter Smoke Test

This tests AI Platform auth, state building, conversation API call, and response contract validation without starting Flask.

```bash
cd /Users/pete/Documents/Src/AIHackathon/hackathon-team5

flask_api/.venv/bin/python -c '
import os
import sys
sys.path.insert(0, "flask_api")
import services.ai_copilot_adapter as a

a.load_env_file()
os.environ["AI_PLATFORM_TIMEOUT_SECONDS"] = os.getenv("AI_PLATFORM_TIMEOUT_SECONDS", "20")

state = a.build_ai_copilot_state(
    "portfolio_review",
    "Please summarize portfolio risk for leadership.",
    {}
)

print("env_loaded", all(os.getenv(k) for k in [
    "AI_PLATFORM_USERNAME",
    "AI_PLATFORM_PASSWORD",
    "AI_PLATFORM_AGENT_ID",
    "AI_PLATFORM_CONVERSATION_ID"
]))
print("available_metrics", state["availableMetrics"])

try:
    result = a._call_ai_platform_agent(state)
    print("platform_call", "ok")
    print("validation_errors", a.validate_ai_copilot_response(result))
    print("executive_summary", str(result.get("executiveSummary", ""))[:300])
except Exception as exc:
    print("platform_call", "failed")
    print("error_type", type(exc).__name__)
    print("error", str(exc)[:500])
    raise SystemExit(2)
'
```

Expected output:

```text
env_loaded True
available_metrics [...]
platform_call ok
validation_errors []
executive_summary ...
```

## 7. Auth-Only Test

Use this if the full test fails and you want to isolate whether credentials/token exchange works.

```bash
cd /Users/pete/Documents/Src/AIHackathon/hackathon-team5

flask_api/.venv/bin/python -c '
import os
import sys
sys.path.insert(0, "flask_api")
import services.ai_copilot_adapter as a

a.load_env_file()
os.environ["AI_PLATFORM_TIMEOUT_SECONDS"] = os.getenv("AI_PLATFORM_TIMEOUT_SECONDS", "20")

print("env_loaded", all(os.getenv(k) for k in [
    "AI_PLATFORM_USERNAME",
    "AI_PLATFORM_PASSWORD",
    "AI_PLATFORM_AGENT_ID",
    "AI_PLATFORM_CONVERSATION_ID"
]))

try:
    token = a._fetch_ai_platform_token()
    print("auth", "ok")
    print("token_prefix", token[:6] + "...")
except Exception as exc:
    print("auth", "failed")
    print("error_type", type(exc).__name__)
    print("error", str(exc)[:500])
    raise SystemExit(2)
'
```

Expected output:

```text
env_loaded True
auth ok
token_prefix eyJhbG...
```

## 8. Response Inspection Test

Use this if auth works but `/api/ai-copilot/analyze` returns `mode: mock`.

This checks what the AI Platform returned without printing secrets.

```bash
cd /Users/pete/Documents/Src/AIHackathon/hackathon-team5

flask_api/.venv/bin/python -c '
import os
import sys
sys.path.insert(0, "flask_api")
import services.ai_copilot_adapter as a

a.load_env_file()
os.environ["AI_PLATFORM_TIMEOUT_SECONDS"] = os.getenv("AI_PLATFORM_TIMEOUT_SECONDS", "20")

state = a.build_ai_copilot_state(
    "portfolio_review",
    "Please summarize portfolio risk for leadership. Return only JSON matching the required AI Copilot response contract.",
    {}
)

token = a._fetch_ai_platform_token()
base = os.getenv("AI_PLATFORM_BASE_URL", "https://meshstage.lessen.com").rstrip("/")
endpoint = os.getenv("AI_PLATFORM_CONVERSATION_ENDPOINT") or (
    f"{base}/onebrain/conversation/"
    f"{os.getenv(\"AI_PLATFORM_AGENT_ID\")}/"
    f"{os.getenv(\"AI_PLATFORM_CONVERSATION_ID\")}"
)

payload = {
    "text": state["userQuestion"],
    "states": [
        {
            "key": os.getenv("AI_PLATFORM_STATE_KEY", "aiCopilotState"),
            "value": state
        }
    ]
}

parsed = a._post_json(endpoint, payload, {"Authorization": f"Bearer {token}"})
print("top_keys", sorted(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__)

text = ""
if isinstance(parsed, dict):
    text = parsed.get("rich_content", {}).get("message", {}).get("text", "")

print("text_prefix", text[:1000].replace("\\n", "\\\\n"))
print("text_len", len(text))
'
```

Look for:

- A JSON object in `text_prefix`.
- It should include `executiveSummary`, `keyFindings`, `riskDrivers`, and `operationalActionPlan`.
- It should not say `No portfolio data was provided`.

If it says `No portfolio data was provided`, the Agent template is not reading `{{ aiCopilotState }}` correctly.

## 9. Test Task Types

Portfolio review:

```bash
curl -s http://127.0.0.1:5000/api/ai-copilot/analyze \
  -H "Content-Type: application/json" \
  -d '{"taskType":"portfolio_review","userQuestion":"What should leadership know?","dataContext":{}}'
```

Storm impact:

```bash
curl -s http://127.0.0.1:5000/api/ai-copilot/analyze \
  -H "Content-Type: application/json" \
  -d '{"taskType":"storm_impact","userQuestion":"Which assets need immediate storm response?","dataContext":{}}'
```

Capital planning:

```bash
curl -s http://127.0.0.1:5000/api/ai-copilot/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "taskType":"capital_planning",
    "userQuestion":"What should we prioritize for capital planning?",
    "dataContext":{"requestedMetrics":["capitalROI","assetHealthScore","lossForecast"]}
  }'
```

For the capital planning test, `capitalROI` should appear in:

```json
"diagnostics": {
  "missingMetrics": ["capitalROI"]
}
```

## 10. How to Interpret Results

### `mode: ai_platform`

The backend successfully:

1. Loaded `.env`.
2. Got an AI Platform token.
3. Sent `aiCopilotState` to the conversation endpoint.
4. Parsed the platform response.
5. Validated the response contract.

### `mode: mock`

The backend used deterministic local fallback.

Common causes:

- Missing `.env` values.
- Auth endpoint failed.
- Conversation endpoint failed or timed out.
- Agent returned text that was not valid JSON.
- Agent returned JSON, but not the required response shape.

### `validation_errors` is not empty

The platform returned JSON, but it did not match the required response contract.

Most common issue:

- `executiveSummary` returned as an object instead of a string.
- Missing `operationalActionPlan.immediateActions`.
- Markdown fences or explanatory text wrapped around JSON.

## 11. Common Troubleshooting

### `env_loaded False`

Check `.env` exists at:

```text
/Users/pete/Documents/Src/AIHackathon/hackathon-team5/.env
```

Check required keys are not empty:

```bash
AI_PLATFORM_USERNAME=
AI_PLATFORM_PASSWORD=
AI_PLATFORM_AGENT_ID=
AI_PLATFORM_CONVERSATION_ID=
```

### Auth fails

Likely causes:

- Wrong username/password.
- `AI_PLATFORM_AUTH_URL` is wrong.
- VPN/network issue.
- AI Platform auth service unavailable.

### SSL timeout or EOF

Examples:

```text
The handshake operation timed out
UNEXPECTED_EOF_WHILE_READING
```

Try:

```bash
AI_PLATFORM_TIMEOUT_SECONDS=20
```

Also check VPN/network access.

### Platform response says no data was provided

Likely cause:

- Agent template is not referencing `{{ aiCopilotState }}`.
- The platform parameter name is not `aiCopilotState`.
- The platform requires explicit parameter registration.

Check the Agent template includes:

```text
{{ aiCopilotState }}
```

and the platform parameter is named:

```text
aiCopilotState
```

### Response is JSON but endpoint returns `mock`

Run the response inspection test and check whether the JSON matches this exact shape:

```json
{
  "executiveSummary": "string",
  "keyFindings": [],
  "riskDrivers": [],
  "stormImpactAssessment": "string",
  "maintenanceAssetHealthInsights": "string",
  "financialExposure": "string",
  "capitalPlanningRecommendations": [],
  "priorityAssets": [],
  "operationalActionPlan": {
    "immediateActions": [],
    "nearTermActions": [],
    "strategicActions": []
  },
  "dataGapsConfidence": []
}
```

## 12. Automated Regression Tests

Run local automated tests:

```bash
cd /Users/pete/Documents/Src/AIHackathon/hackathon-team5
flask_api/.venv/bin/python -m pytest flask_api/tests/ -q
```

These tests do not require real AI Platform credentials.

They verify:

- State builder includes available metrics.
- Missing metrics become data gaps.
- Mock fallback works without credentials.
- AI Platform conversation protocol shape is correct.
- `.env` loading works.
- Existing Layer 1 and Layer 2 tests still pass.

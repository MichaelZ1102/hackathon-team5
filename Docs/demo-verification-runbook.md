# Demo Verification Runbook

Golden-path verification for the Portfolio Intelligence + AI Copilot demo, plus
a 3-minute demo script. Run this before any demo or handoff.

Current expected state (as of 2026-06-12):

- 290-property Florida portfolio (`mock_data/properties.json`)
- Default storm event: `FL-HUR-2026-FCST-01` (Tropical Storm Marco forecast) —
  Layer 1 and Layer 2 analyze the same storm
- All seven Layer 1 metrics implemented: assetHealthScore, stormImpactLevel
  (distance-decayed, levels Severe/High/Medium/Low/None), riskScore_v2,
  lossForecast, insuranceGap, capitalROI, priorityRanking
- Top priority asset: **STORMPATH-006** (St. Petersburg Harbor Apartments 0006)
- Full backend test suite: 141 tests

> **Storm impact is not binary.** Every property is evaluated based on
> distance to the storm path. Properties closer to the path receive higher
> `stormImpactScore`, while far-away properties may receive `None` impact —
> evaluated, but outside meaningful range (storm loss 0; all other metrics
> still computed).

---

## A. Start the backend

```bash
cd hackathon-team5/flask_api
.venv/bin/python app.py
```

Default bind is `127.0.0.1:5000`.

Pitfalls:

- **Port 5000 is contested on macOS** — AirPlay Receiver answers 403 there when
  Flask is down. If `app.py` reports "Address already in use", run on another
  port and substitute it in every call below:
  `.venv/bin/python -c "from app import app, STORAGE_DIR; STORAGE_DIR.mkdir(parents=True, exist_ok=True); app.run(host='127.0.0.1', port=5050)"`
- The venv must contain `requests` (`pip install -r requirements.txt` if the
  server fails on import).
- Swagger UI for interactive exploration: `http://127.0.0.1:5000/api/docs`.

```bash
export BASE=http://127.0.0.1:5000   # adjust if you changed the port
```

## B. Call the deterministic metrics endpoint

```bash
curl -s "$BASE/api/portfolio/intelligence?portfolioId=FL-DEMO&analysisYear=2026&stormEventId=FL-HUR-2026-FCST-01" -o /tmp/pi.json
```

## C. Verify the metrics response

One-shot check (prints PASS/FAIL per assertion):

```bash
python3 - << 'EOF'
import json
d = json.load(open('/tmp/pi.json'))
s, diag = d['portfolioSummary'], d['diagnostics']
top = d['finalPriorityList'][0]
gaps = [r for r in d['propertyIntelligenceResults']
        if (r['insuranceGap'].get('insuranceGap') or 0) > 0]
checks = [
    ("HTTP body parsed (200)",            True),
    ("7 includedMetrics",                 len(diag['includedMetrics']) == 7),
    ("missingMetrics is []",              diag['missingMetrics'] == []),
    ("finalPriorityList[0] exists",       bool(top)),
    ("top asset is STORMPATH-006",        top['propertyId'] == 'STORMPATH-006'),
    ("totalLossForecast > 0",             s['totalLossForecast'] > 0),
    ("totalInsuranceGap > 0",             s['totalInsuranceGap'] > 0),
    (">=1 property insuranceGap > 0",     len(gaps) >= 1),
    ("top asset has bestCapitalAction",   top['bestCapitalAction'] is not None),
    ("storm distribution sums to 290",
     sum(diag['stormImpactDistribution'].values()) == 290),
    ("stormImpactDistribution exists",
     set(diag['stormImpactDistribution']) == {'Severe','High','Medium','Low','None'}),
    (">=1 property has None impact",      diag['stormImpactDistribution']['None'] >= 1),
    ("finalPriorityList carries stormImpactScore + distance",
     'stormImpactScore' in top and 'distanceToStormPathMiles' in top),
]
for name, ok in checks:
    print(("PASS " if ok else "FAIL "), name)
assert all(ok for _, ok in checks)
print("\nGolden values:")
print(" totalLossForecast:", f"${s['totalLossForecast']:,.0f}")
print(" totalInsuranceGap:", f"${s['totalInsuranceGap']:,.0f}")
print(" top:", top['propertyId'], "score", top['priorityScore'],
      "| gap", f"${top['insuranceGap']:,.0f}",
      "| best action", top['bestCapitalAction']['actionType'],
      f"ROI {top['bestCapitalAction']['capitalROI']}x")
print(" stormImpactDistribution:", diag['stormImpactDistribution'])
EOF
```

Expected golden values (deterministic for the default scope):

| Field | Expected |
| --- | --- |
| `portfolioSummary.totalProperties` | 290 |
| `portfolioSummary.totalLossForecast` | ≈ $156,240,672 |
| `portfolioSummary.totalInsuranceGap` | $2,722,571.53 (STORMPATH-006 $1.68M + GPS0041213 $1.04M) |
| `portfolioSummary.affectedPropertyCount` | 217 (Severe+High+Medium) |
| `finalPriorityList[0].propertyId` | STORMPATH-006 |
| `finalPriorityList[0].priorityScore` | 83.1 |
| `finalPriorityList[0].bestCapitalAction` | CAP-STORMPATH-006-2, Envelope Sealing, $112,000, ROI 18.09× |
| `diagnostics.stormImpactDistribution` | Severe 63 · High 99 · Medium 55 · Low 67 · None 6 |
| `diagnostics.includedMetrics` | all 7 metrics |
| `diagnostics.missingMetrics` | `[]` |

## D. Call the AI advisory endpoint

```bash
curl -s -X POST "$BASE/api/ai-copilot/analyze" \
  -H "Content-Type: application/json" \
  -d '{
    "taskType": "portfolio_review",
    "userQuestion": "Explain the top portfolio risks and recommended capital priorities.",
    "scenario": {
      "portfolioId": "FL-DEMO",
      "analysisYear": 2026,
      "stormEventId": "FL-HUR-2026-FCST-01"
    },
    "dataContext": {
      "requestedMetrics": ["insuranceGap", "capitalROI", "priorityRanking"]
    }
  }' -o /tmp/ai.json
```

Note: this call is slower than the metrics endpoint when AI Platform
credentials are configured in `.env` (`mode: "ai_platform"`, ~10-30 s). With no
credentials it returns the deterministic mock instantly (`mode: "mock"`).
Either mode is demo-acceptable; the mock is fully grounded in the same metrics.

## E. Verify the AI response

```bash
python3 - << 'EOF'
import json
b = json.load(open('/tmp/ai.json'))
diag, result = b['diagnostics'], b['result']
text = json.dumps(result)
checks = [
    ("mode is ai_platform or mock",      b['mode'] in ('ai_platform', 'mock')),
    ("7 availableMetrics",               len(diag['availableMetrics']) == 7),
    ("missingMetrics is []",             diag['missingMetrics'] == []),
    ("executiveSummary exists",          bool(result.get('executiveSummary'))),
    ("priorityAssets exists",            bool(result.get('priorityAssets'))),
    ("references top asset",             'STORMPATH-006' in text
                                          or 'St. Petersburg Harbor' in text),
    ("scope echoed",                     diag['analysisScope']['stormEventId']
                                          == 'FL-HUR-2026-FCST-01'),
]
for name, ok in checks:
    print(("PASS " if ok else "FAIL "), name)
assert all(ok for _, ok in checks)
print("\nmode:", b['mode'])
print("executiveSummary:", result['executiveSummary'][:200])
EOF
```

Also confirm manually that the AI narrative does **not invent unsupported
metrics** — every number it cites must exist in the metrics response from
step C (the agent template forbids calculating or inventing metrics; the
backend builds its state from the same scoped aggregation).

If `mode` is `"error"` (HTTP 502): the AI Platform call failed and mock
fallback is disabled. Either unset `AI_COPILOT_ENABLE_MOCK_FALLBACK=false` or
fix the platform credentials. The metrics endpoint is unaffected either way.

## F. Layer 2 smoke (operational endpoints)

```bash
curl -s -o /dev/null -w "/api/risk/timeline    -> %{http_code}\n" "$BASE/api/risk/timeline"
curl -s -o /dev/null -w "/api/risk/properties  -> %{http_code}\n" "$BASE/api/risk/properties"
```

Both must return 200. Default Layer 2 analysis time is the hurricane landfall
point `2026-08-17T06:00:00Z`; the top affected Layer 2 property is SA000
(Tampa Commons A000, Critical, riskScore 83).

## G. Full backend test suite

```bash
cd hackathon-team5/flask_api && .venv/bin/python -m pytest tests/ -q
```

Expected: **141 passed**, fully offline (tests never call the AI Platform).

---

## 3-Minute Demo Script

**1. Portfolio exposure (30 s).** "We're tracking a forecast hurricane —
Tropical Storm Marco — approaching Tampa Bay. The system evaluates **all 290
properties**, not just the ones in its path: storm impact decays with distance,
from 63 Severe assets near the track down to 6 with no meaningful impact.
Total forecast loss across the portfolio: **$156M**." *(Show the overview
cards and the storm impact distribution.)*

> Talking point: "This is why the system is more than a county-level alert.
> It evaluates how strongly each asset is exposed based on where it sits
> relative to the storm path."

**2. The top priority asset (60 s).** "The final ranking puts **St. Petersburg
Harbor Apartments (STORMPATH-006)** first, and every factor is explainable:
it sits **5 miles from the projected track** (storm impact 100/100, Severe);
its **asset health is 49/100** with riskScore_v2 of 83 — an older, vulnerable
building; its **forecast loss is $4.05M**; and critically, its carrier cut the
windstorm coverage limit at the 2026 renewal, leaving a **$1.68M insurance
gap** — the only uncovered exposure in the portfolio. The system also already
knows the best response: **Envelope Sealing at $112K, returning 18× in
avoided loss**." *(Walk down finalPriorityList[0]'s fields.)*

**3. AI Copilot (60 s).** "Now the AI layer: we send only the scenario — which
portfolio, which year, which storm — never raw data. The backend computes
everything deterministically and the AI narrates it for leadership: executive
summary, key findings, priority assets, action plan, and explicit data-quality
caveats. If the AI platform is slow or down, the metrics dashboard is
unaffected — the AI panel degrades alone." *(Run the analyze call, show the
executive summary referencing the same numbers.)*

**4. Value statement (10 s).** "The system converts storm risk, maintenance
history, insurance exposure, and capital actions into an actionable asset
priority decision."

---

## Known demo risks

- **Port 5000 conflict (macOS AirPlay)** — verify step A before going live.
- **Live AI latency** — with real credentials the analyze call took ~25 s in
  testing. Pre-warm it once before the demo, or demo in mock mode.
- **Live AI narrative drift** — the platform agent may lean on Layer 2 phrasing
  ("affected properties") rather than the new distance-decay framing; the
  template rule was updated, but the deployed agent prompt may lag. The mock
  response is always consistent with the metrics.
- **Stale server processes** — debug-mode Flask instances from earlier sessions
  may hold ports 5001/5002 with old code. Kill them or use a fresh port.
- 260 properties outside the top-30 have no capital actions (by design), so
  their priority confidence shows Low — expected, surfaced in dataQualityNotes.

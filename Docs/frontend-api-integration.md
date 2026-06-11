# Frontend API Integration Guide

This document describes the current backend API surface for the Property Portfolio Intelligence + AI Copilot demo. It is based on the Flask routes currently present in the codebase.

## Architecture Rules

- The frontend must call only the demo backend APIs.
- The frontend must not call the internal AI Platform directly.
- The frontend must not include AI Platform URLs, credentials, bearer tokens, prompts, agent IDs, conversation IDs, or agent configuration.
- The frontend must not pass full raw property, work-order, weather, valuation, insurance, contractor, or capital data to the AI Copilot endpoint.
- The frontend should pass only scenario scope and user intent. Example:

```json
{
  "portfolioId": "FL-DEMO",
  "analysisYear": 2026,
  "stormEventId": "TOR-FL-2026-0612"
}
```

The backend is responsible for selecting scoped demo data, running deterministic calculations, building compact AI state, calling the internal AI Platform Agent, validating AI response shape, and falling back or returning an AI error depending on backend configuration.

## Why There Are Two Endpoints

The frontend integration is deliberately split into two endpoints with different latency and reliability profiles:

| | `GET /api/portfolio/intelligence` | `POST /api/ai-copilot/analyze` |
| --- | --- | --- |
| Role | Deterministic metrics | AI advisory / interpretation |
| Data source | Local mock data + deterministic Layer 1 engines | The same scoped metrics, narrated by the internal AI Platform Agent |
| Calls AI? | **Never.** The metrics code path has no AI imports and no network calls (pinned by `tests/test_endpoint_separation.py`). | Yes, when configured; deterministic mock fallback otherwise |
| Latency | Fast (milliseconds, local computation) | Potentially slow (remote AI Platform call, ~seconds, may time out) |
| Frontend dependency | Required for the metrics UI | Optional enhancement; metrics UI must render without it |

The dependency is strictly one-way: the AI Copilot adapter consumes the same scoped `build_portfolio_intelligence` aggregation the metrics endpoint serves. The metrics endpoint never imports or waits on AI code, so a slow or failed AI Platform can never delay or break the metrics UI.

**Recommended frontend flow:**

1. Call `GET /api/portfolio/intelligence` with the scenario.
2. Render the metrics UI immediately from that response.
3. Then call `POST /api/ai-copilot/analyze` with the same scenario (scope + question only — no metric values, no raw data).
4. Render the AI advisory panel when it returns.
5. If the AI call times out or fails, keep the metrics UI visible and show an AI-unavailable/fallback indicator in the advisory panel only. Use a client-side timeout on the AI call (e.g. 15–30 s) and never gate metrics rendering on it.

**What the frontend must not do:** call the AI Platform directly; pass deterministic metric values or raw property/work-order/weather/valuation data into the AI endpoint; block or hide metrics because AI failed; recompute any Layer 1 metric in the browser.

## Data Model and Dataset Characteristics

All current demo behavior is driven by local JSON files under `mock_data/`. These files are synthetic demo data, not live operational systems or production data. Frontend integration should treat API responses as read-only projections over these JSON datasets.

Primary JSON datasets:

| File | Main collection | Key fields | Used for |
| --- | --- | --- | --- |
| `mock_data/properties.json` | `properties[]` | `propertyId`, `name`, `market`, `city`, `county`, `lat`, `lng`, `assetType`, `units`, `yearBuilt`, `roofAgeYears`, `hvacAvgAgeYears`, `occupancyRate`, `exteriorCondition`, `treeCanopyRisk`, `floodZoneExposure` | Property master data, map/table display, asset vulnerability inputs |
| `mock_data/weather_events.json` | `event`, `timeline[]` | `eventId`, timeline `time`, `stage`, `centerLat`, `centerLng`, `affectedCounties[]`, county `riskLevel`, `windSpeedMph`, `tornadoProbability`, `hailRisk` | Layer 2 storm timeline and county risk |
| `mock_data/storm_path.json` | storm path object | `stormEventId`, `eventName`, `windSpeedMph`, `rainfallForecastInches`, `impactWindowStart`, `impactWindowEnd`, `projectedPath[]` | Layer 1 `stormImpactLevel` distance/path calculation |
| `mock_data/work_orders.json` | `workOrders[]` | `workOrderId`, `propertyId`, `category`, `createdDate`, `completedDate`, `cost`, `status`, `isRepeatIssue`, `contractorId` | `assetHealthScore`, maintenance risk, draft work-order context |
| `mock_data/valuations.json` | `valuations[]` | `propertyId`, `replacementValue`, `marketValue`, `insuredValue`, `lastValuationDate` | `lossForecast`, `riskScore_v2` asset-value component |
| `mock_data/lease_exposure.json` | `leaseExposure[]` | `propertyId`, `occupiedUnits`, `vacantUnits`, `renewalsDueNext90Days`, `averageMonthlyRent`, `atRiskResidentCount` | Layer 2 business/resident exposure |
| `mock_data/contractors.json` | `contractors[]` | `contractorId`, `name`, `serviceType`, `serviceCounties`, `availableWithinHours`, `rating`, `averageCostLevel` | Embedded contractor recommendations in Layer 2 property results |
| `mock_data/insurance_policies.json` | `policies[]` | `policyId`, `propertyId`, `coverageTypes`, `coverageLimit`, deductibles, exclusions | `insuranceGap` (Phase C); policies filtered to those in force for the analysis year |
| `mock_data/capital_actions.json` | `capitalActions[]` | `capitalActionId`, `propertyId`, `actionType`, `estimatedCost`, `estimatedRiskReduction`, `usefulLifeYears` | `capitalROI` and `bestCapitalAction` (Phase C) |
| `mock_data/capex_plan.json` | `capexItems[]` | `capexId`, `propertyId`, `item`, `plannedYear`, `estimatedCost`, `priority`, `status` | Layer 2 risk context and repair exposure context |

Important data relationships:

- `propertyId` is the primary join key across properties, work orders, valuations, lease exposure, insurance policies, capital actions, and many Layer 1/Layer 2 outputs.
- `contractorId` links historical work orders and contractor directory records, while current contractor recommendations are returned as embedded recommendation objects.
- `stormEventId` links the default scenario (`TOR-FL-2026-0612`) to storm path and weather event context.
- The demo portfolio is `FL-DEMO`; the resolver falls back to this dataset when unknown portfolio IDs are supplied.
- The default analysis year is `2026`; it controls valuation validity and the rolling work-order lookback window.
- Each JSON file includes `meta` describing synthetic demo data. The API may return `meta`, `metadata`, or `diagnostics` depending on the endpoint; use these fields for non-blocking UI notes.

Frontend implications:

- Render API responses, not raw JSON files. The frontend should not read files from `mock_data/` directly.
- Expect Florida-specific synthetic properties and markets such as Orlando, Tampa Bay, Space Coast, Jacksonville, Miami, and Gulf Coast cities.
- Treat all dollar values as USD unless the response explicitly says otherwise.
- Treat coordinates as approximate display coordinates for demo mapping.
- Treat `watchList` as a legacy compatibility list (pre-Phase C sort); use `finalPriorityList` for the final business ranking.
- All seven Layer 1 metrics are implemented (Phases A+B+C): `assetHealthScore`, `stormImpactLevel`, `riskScore_v2`, `lossForecast`, `insuranceGap`, `capitalROI`, `priorityRanking`. None are frontend calculation tasks.
- Treat the storm timeline as scenario data from `weather_events.json`, not as a live feed or current weather service.
- Prefer `propertyId`, `stormEventId`, and scenario scope as stable frontend identifiers. Display names are suitable for UI labels but should not be used as join keys.

## Recommended Frontend Flow

1. Initialize default scenario:

```js
const defaultScenario = {
  portfolioId: "FL-DEMO",
  analysisYear: 2026,
  stormEventId: "TOR-FL-2026-0612",
};
```

2. Call `GET /api/portfolio/intelligence`.
3. Render portfolio overview cards, severe/high risk counts, `totalLossForecast`, `averageAssetHealthScore`, `watchList`, property table/map, loss forecast, and storm impact fields.
4. Call `POST /api/ai-copilot/analyze` with the same scenario.
5. Render AI executive summary, key findings, risk drivers, financial exposure, capital planning recommendations, priority assets, operational action plan, and data gaps/confidence.
6. On scenario change, reload both `/api/portfolio/intelligence` and `/api/ai-copilot/analyze`.
7. If backend APIs fail, keep static demo fallback data visible and show a "Demo fallback data" indicator.
8. If AI Copilot fails, keep Portfolio Intelligence visible and show an AI unavailable/fallback message.

## Base URL

Use the Flask server URL for the current local run, for example:

```text
http://127.0.0.1:5055
```

Do not hard-code this value in production frontend code. Use an environment-specific API base URL.

---

# GET /api/portfolio/intelligence

## Purpose

Returns read-only portfolio-level Layer 1 Portfolio Intelligence. It aggregates deterministic per-property calculations for:

- `assetHealthScore` (Phase A)
- `stormImpactLevel`, `riskScore_v2`, `lossForecast` (Phase B)
- `insuranceGap`, `capitalROI` / `bestCapitalAction`, `priorityRanking` (Phase C)

It processes multiple properties under the selected scenario. It does not require `propertyId`.

This endpoint is purely deterministic: it never calls AI, the AI Platform, or any network service, and returns quickly from local data. This is enforced by tests (`tests/test_endpoint_separation.py`).

This endpoint is the frontend's main aggregated view over the JSON-backed portfolio data. It joins property master records, scoped maintenance history, scoped valuations, and storm-path context into calculated metric objects.

## When the Frontend Should Call It

Call this endpoint when:

- the dashboard first loads
- the user changes scenario scope
- the frontend needs portfolio cards, property table/map data, watch list, or deterministic metric values

## Query Parameters

| Name | Required | Default | Current behavior |
| --- | --- | --- | --- |
| `portfolioId` | No | `FL-DEMO` | Unknown IDs fall back to the demo portfolio and add diagnostics notes. |
| `analysisYear` | No | `2026` | Must be an integer year between 1900 and 2100. Controls work-order lookback and valuation validity. |
| `stormEventId` | No | current demo storm | Unknown IDs fall back to the current demo storm and add diagnostics notes. |

## Required Parameters

None.

## Optional Parameters

`portfolioId`, `analysisYear`, `stormEventId`.

## Default Behavior

Calling without query parameters uses the default demo scenario:

```json
{
  "portfolioId": "FL-DEMO",
  "analysisYear": 2026,
  "stormEventId": "TOR-FL-2026-0612"
}
```

## Example Request

```http
GET /api/portfolio/intelligence?portfolioId=FL-DEMO&analysisYear=2026&stormEventId=TOR-FL-2026-0612
```

```js
const response = await fetch(
  `${apiBaseUrl}/api/portfolio/intelligence?` +
    new URLSearchParams({
      portfolioId: "FL-DEMO",
      analysisYear: "2026",
      stormEventId: "TOR-FL-2026-0612",
    })
);
const data = await response.json();
```

## Example Response Shape

```json
{
  "portfolioSummary": {
    "totalProperties": 14,
    "severeImpactCount": 8,
    "highRiskCount": 4,
    "totalLossForecast": 23526415.74,
    "averageAssetHealthScore": 63.2,
    "totalInsuranceGap": 630502.4,
    "topPriorityPropertyId": "FL-LAK-044",
    "topRiskDrivers": []
  },
  "propertyIntelligenceResults": [
    {
      "propertyId": "FL-ORL-102",
      "propertyName": "Orlando Lakeside Villas",
      "county": "Orange",
      "location": {
        "city": "Orlando",
        "market": "Orlando",
        "lat": 28.5383,
        "lng": -81.3792
      },
      "assetHealthScore": {},
      "stormImpactLevel": {},
      "riskScore_v2": {},
      "lossForecast": {},
      "insuranceGap": {},
      "priorityRanking": {},
      "bestCapitalAction": {},
      "drivers": [],
      "confidence": "High",
      "dataQualityNotes": []
    }
  ],
  "watchList": [
    {
      "watchRank": 1,
      "propertyId": "FL-ORL-102",
      "propertyName": "Orlando Lakeside Villas",
      "county": "Orange",
      "riskScore_v2": 72,
      "riskBand": "High",
      "stormImpactLevel": "Severe",
      "lossForecast": 3532464,
      "assetHealthScore": 44,
      "drivers": [],
      "confidence": "High",
      "note": "Compatibility list (pre-Phase C sort); see finalPriorityList for the final priorityRanking."
    }
  ],
  "finalPriorityList": [
    {
      "priorityRank": 1,
      "priorityScore": 82.6,
      "propertyId": "FL-LAK-044",
      "propertyName": "Lakeland Grove Apartments",
      "county": "Polk",
      "riskScore_v2": 80,
      "lossForecast": 2680502.4,
      "assetHealthScore": 24,
      "insuranceGap": 630502.4,
      "bestCapitalAction": {
        "capitalActionId": "CAP-LAK-2",
        "actionType": "Envelope Sealing",
        "estimatedCost": 75000.0,
        "capitalROI": 17.87
      },
      "rankingDrivers": [],
      "confidence": "High"
    }
  ],
  "capitalActionResults": [
    {
      "propertyId": "FL-ORL-102",
      "metric": "capitalROI",
      "capitalActionId": "CAP-ORL-1",
      "actionType": "Roof Replacement",
      "estimatedCost": 620000.0,
      "estimatedRiskReduction": 0.24,
      "estimatedAvoidedLoss": 4238956.8,
      "capitalROI": 6.84,
      "horizonYears": 5,
      "drivers": [],
      "confidence": "High",
      "dataQualityNotes": []
    }
  ],
  "diagnostics": {
    "calculationVersion": "layer1-phaseA+B+C-v3-scoped",
    "analysisScope": {
      "portfolioId": "FL-DEMO",
      "analysisYear": 2026,
      "stormEventId": "TOR-FL-2026-0612",
      "requestedStormEventId": "TOR-FL-2026-0612",
      "workOrderWindow": {
        "start": "2024-12-31",
        "end": "2026-12-31",
        "lookbackMonths": 24
      },
      "workOrdersInScope": 0,
      "workOrdersExcluded": 0,
      "valuationsValidForYear": 0,
      "valuationsExcluded": 0,
      "notes": []
    },
    "includedMetrics": [
      "assetHealthScore",
      "stormImpactLevel",
      "riskScore_v2",
      "lossForecast",
      "insuranceGap",
      "capitalROI",
      "priorityRanking"
    ],
    "missingMetrics": [],
    "dataSourcesUsed": [],
    "warnings": []
  }
}
```

## Phase C Example: insuranceGap (underinsured property)

The demo dataset includes one intentionally underinsured property (FL-LAK-044; the carrier cut the windstorm limit at the 2026 renewal — see the `note` on `POL-LAK-044`). Its per-property `insuranceGap` object renders like this:

```json
{
  "propertyId": "FL-LAK-044",
  "metric": "insuranceGap",
  "insuranceGap": 630502.4,
  "coverageLimit": 1750000.0,
  "applicableDeductible": 300000.0,
  "coveredAmount": 1750000.0,
  "deductibleType": "namedStorm",
  "policyId": "POL-LAK-044",
  "drivers": [
    "namedStorm deductible $300,000 applied",
    "Forecast loss $2,680,502 exceeds coverage by $630,502"
  ],
  "confidence": "High",
  "dataQualityNotes": []
}
```

Fully covered properties return the same shape with `"insuranceGap": 0.0`; properties whose policy or loss forecast is missing return `"insuranceGap": null` with explanatory `dataQualityNotes`. `portfolioSummary.totalInsuranceGap` sums the computable gaps (currently `630502.4`).

## Key Response Fields for Frontend Rendering

- `portfolioSummary.totalProperties`: total portfolio size.
- `portfolioSummary.severeImpactCount`: count of severe storm-impact properties.
- `portfolioSummary.highRiskCount`: count of high `riskScore_v2` properties.
- `portfolioSummary.totalLossForecast`: aggregate deterministic loss forecast.
- `portfolioSummary.averageAssetHealthScore`: portfolio health overview.
- `portfolioSummary.topRiskDrivers`: concise portfolio risk-driver list.
- `propertyIntelligenceResults[]`: per-property calculated Layer 1 result envelope.
- `propertyIntelligenceResults[].assetHealthScore`: calculated asset-health metric object.
- `propertyIntelligenceResults[].stormImpactLevel`: calculated storm-impact metric object.
- `propertyIntelligenceResults[].riskScore_v2`: calculated Layer 1 risk score object.
- `propertyIntelligenceResults[].lossForecast`: calculated loss forecast object.
- `propertyIntelligenceResults[].insuranceGap`: uncovered forecast loss after deductible and coverage limit (Phase C).
- `propertyIntelligenceResults[].priorityRanking`: final rank + weighted priorityScore (Phase C).
- `propertyIntelligenceResults[].bestCapitalAction`: highest-ROI capital action for the property, or null.
- `watchList`: legacy compatibility list (pre-Phase C sort); kept for older consumers.
- `finalPriorityList`: the final Phase C portfolio ranking — use this for priority displays.
- `capitalActionResults`: per-action capitalROI results for the capital-planning view.
- `portfolioSummary.totalInsuranceGap` / `portfolioSummary.topPriorityPropertyId`: Phase C rollups.
- `diagnostics.analysisScope`: resolved scope and data selection diagnostics.
- `diagnostics.missingMetrics`: empty as of Phase C; would name any metric not computable.

## Error Handling

- `400` when `analysisYear` is not an integer or outside supported range.
- Unknown `portfolioId` or `stormEventId` currently do not fail; the backend serves demo data and adds diagnostics notes.
- The frontend should render diagnostics notes as non-blocking warnings.

## Fallback Behavior

If the endpoint fails, the frontend may keep static demo data visible and show a "Demo fallback data" indicator.

## What Frontend Must NOT Do

- Do not recompute Layer 1 formulas in the browser.
- Do not treat `watchList` as the final ranking; use `finalPriorityList`.
- Do not pass raw property/work-order/valuation/weather data to AI Copilot.
- Do not block metrics rendering on the AI advisory call.

---

# POST /api/ai-copilot/analyze

## Purpose

Backend-owned AI Copilot/BFF endpoint. The frontend calls this endpoint for advisory analysis; the backend builds `aiCopilotState`, calls the internal AI Platform Agent, validates the result, and returns a structured response.

The frontend must not call the AI Platform directly.

The backend builds Copilot state from the same scoped Portfolio Intelligence aggregation the metrics endpoint serves (`build_portfolio_intelligence(scope=...)`), so the AI narrates exactly the numbers the metrics UI shows. The frontend sends scenario scope and intent only — never metric values or raw data. All deterministic metrics (including `insuranceGap`, `capitalROI`, `priorityRanking`) are computed by the backend engines; the AI never calculates them.

This endpoint may be slower than the metrics endpoint because it calls the internal AI Platform. Call it after metrics have rendered, apply a client-side timeout, and degrade only the advisory panel on failure.

## When the Frontend Should Call It

Call after portfolio intelligence has loaded, or whenever scenario scope or user question changes.

## Request Body

```json
{
  "taskType": "portfolio_review",
  "userQuestion": "Summarize the current portfolio storm-risk and capital-planning priorities.",
  "scenario": {
    "portfolioId": "FL-DEMO",
    "analysisYear": 2026,
    "stormEventId": "TOR-FL-2026-0612"
  },
  "dataContext": {
    "requestedMetrics": ["insuranceGap", "capitalROI", "priorityRanking"]
  },
  "compact": true
}
```

## Required Parameters

None are strictly required by current backend behavior. Defaults are applied.

For frontend clarity, send:

- `taskType`
- `userQuestion`
- `scenario`

## Optional Parameters

| Name | Current behavior |
| --- | --- |
| `taskType` | Defaults to `portfolio_review`. Valid values: `portfolio_review`, `storm_impact`, `capital_planning`. |
| `userQuestion` | Defaults to empty string. Send the user-facing prompt/question. |
| `scenario.portfolioId` | Defaults to `FL-DEMO`. Unknown IDs fall back to demo data with diagnostics. |
| `scenario.analysisYear` | Defaults to `2026`; invalid values return `400`. |
| `scenario.stormEventId` | Defaults to current demo storm; unknown IDs fall back with diagnostics. |
| `dataContext.requestedMetrics` | Optional list; unavailable requested metrics appear in `diagnostics.missingMetrics` and/or `result.dataGapsConfidence`. |
| `compact` | Defaults to `true` in the Flask route. Use compact mode for frontend calls. |

## Default Behavior

The route defaults to:

- `taskType`: `portfolio_review`
- `scenario`: demo scope
- `compact`: `true`

Compact mode builds AI state from the Portfolio Intelligence aggregation instead of passing raw or full backend data.

## Example Request

```js
const response = await fetch(`${apiBaseUrl}/api/ai-copilot/analyze`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    taskType: "portfolio_review",
    userQuestion:
      "Summarize the current portfolio storm-risk and capital-planning priorities.",
    scenario: {
      portfolioId: "FL-DEMO",
      analysisYear: 2026,
      stormEventId: "TOR-FL-2026-0612",
    },
    dataContext: {
      requestedMetrics: ["insuranceGap", "capitalROI", "priorityRanking"],
    },
  }),
});

const data = await response.json();
```

## Example Response Shape

```json
{
  "mode": "ai_platform",
  "taskType": "portfolio_review",
  "diagnostics": {
    "templateVersion": "property_portfolio_copilot_template.v1",
    "stateVersion": "ai_copilot_state.v1",
    "stateShape": "compact",
    "analysisScope": {
      "portfolioId": "FL-DEMO",
      "analysisYear": 2026,
      "stormEventId": "TOR-FL-2026-0612",
      "workOrderWindow": {
        "start": "2024-12-31",
        "end": "2026-12-31",
        "lookbackMonths": 24
      }
    },
    "availableMetrics": [
      "assetHealthScore",
      "capitalROI",
      "insuranceGap",
      "lossForecast",
      "priorityRanking",
      "riskScore_v2",
      "stormImpactLevel"
    ],
    "missingMetrics": [],
    "usedCalculatedMetrics": [
      "assetHealthScore",
      "capitalROI",
      "insuranceGap",
      "lossForecast",
      "priorityRanking",
      "riskScore_v2",
      "stormImpactLevel"
    ]
  },
  "result": {
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
}
```

Error mode shape when mock fallback is disabled:

```json
{
  "mode": "error",
  "taskType": "portfolio_review",
  "error": "AI Platform call failed",
  "diagnostics": {
    "aiPlatformError": "AI Platform message text did not contain JSON"
  }
}
```

## Key Response Fields for Frontend Rendering

- `mode`: `ai_platform`, `mock`, or `error`.
- `diagnostics.availableMetrics`: metrics available to the Copilot state.
- `diagnostics.missingMetrics`: known missing metrics.
- `diagnostics.analysisScope`: scenario actually used by backend, when available.
- `result.executiveSummary`: main AI summary.
- `result.keyFindings`: bullets/cards for top findings.
- `result.riskDrivers`: concise list of drivers.
- `result.stormImpactAssessment`: storm narrative.
- `result.maintenanceAssetHealthInsights`: asset-health narrative.
- `result.financialExposure`: financial/loss narrative.
- `result.capitalPlanningRecommendations`: recommendations.
- `result.priorityAssets`: AI-prioritized assets for display.
- `result.operationalActionPlan`: grouped action plan.
- `result.dataGapsConfidence`: gaps and confidence caveats.

## Error Handling

- `400` for invalid `taskType` or invalid `scenario.analysisYear`.
- `502` when the backend returns `mode: error`; this occurs when AI Platform fails and mock fallback is disabled.
- `200` with `mode: mock` when AI Platform fails but mock fallback is enabled.

## Fallback Behavior

Current backend supports mock fallback. When enabled, failed AI Platform calls return:

```json
{
  "mode": "mock",
  "result": {}
}
```

Frontend should:

- display Portfolio Intelligence normally
- show an "AI generated demo fallback" or "AI unavailable, showing demo advisory output" indicator when `mode === "mock"`
- show a failure banner and hide/disable AI advisory panels when `mode === "error"`

## What Frontend Must NOT Do

- Do not call AI Platform endpoints directly.
- Do not include AI Platform credentials, tokens, prompts, URLs, agent IDs, or conversation IDs.
- Do not pass full raw data into this endpoint.
- Do not assume `mode: mock` is AI Platform output.
- Do not rely on AI output for deterministic metrics; render deterministic metrics from `/api/portfolio/intelligence`.

---

# GET /api/risk/timeline

## Purpose

Returns the synthetic tornado weather event and timeline points used by the Layer 2 operational response demo.

This endpoint is a direct API view over the weather timeline JSON shape, not a live weather feed.

## When the Frontend Should Call It

Call when rendering storm timeline controls, scenario timeline labels, or weather-stage selection UI.

## Query Parameters

None.

## Required Parameters

None.

## Optional Parameters

None.

## Default Behavior

Returns the full demo weather event and all timeline points.

## Example Request

```js
const response = await fetch(`${apiBaseUrl}/api/risk/timeline`);
const data = await response.json();
```

## Example Response Shape

```json
{
  "event": {},
  "timeline": [
    {
      "timestamp": "2026-06-12T14:00:00-04:00",
      "stage": "Peak tornado risk",
      "countyRisk": []
    }
  ],
  "meta": {}
}
```

## Key Response Fields for Frontend Rendering

- `event`: storm event metadata.
- `timeline[]`: timeline points for selector/playback UI.
- `timeline[].timestamp`: pass as `time` to Layer 2 risk endpoints.
- `timeline[].stage`: display label.
- `meta`: dataset metadata.

## Error Handling

No explicit route-level errors are currently defined.

## Fallback Behavior

If unavailable, use static demo timeline data and show a demo fallback indicator.

## What Frontend Must NOT Do

- Do not infer property-level risk from timeline alone.
- Do not mutate weather event data in the browser.

---

# GET /api/risk/properties

## Purpose

Returns Layer 2 operational storm-risk analysis for affected properties at a selected timeline point.

This endpoint includes operational response fields such as recommended actions, draft work orders, and contractor recommendations embedded in property results.

The response is generated from JSON-backed property, weather, maintenance, lease exposure, CapEx, and contractor data. It is deterministic for the selected timeline point.

## When the Frontend Should Call It

Call when rendering the operational response dashboard, affected property list, Layer 2 risk cards, work-order drafts, or contractor recommendations.

## Query Parameters

| Name | Required | Default | Current behavior |
| --- | --- | --- | --- |
| `time` | No | backend `DEFAULT_ANALYSIS_TIME` | Must match a valid weather timeline point. |

## Required Parameters

None.

## Optional Parameters

`time`.

## Default Behavior

Uses the peak tornado-risk default analysis time.

## Example Request

```js
const response = await fetch(
  `${apiBaseUrl}/api/risk/properties?` +
    new URLSearchParams({ time: "2026-06-12T14:00:00-04:00" })
);
const data = await response.json();
```

## Example Response Shape

```json
{
  "eventId": "TOR-FL-2026-0612",
  "analysisTime": "2026-06-12T14:00:00-04:00",
  "scenarioStage": "Peak tornado risk",
  "portfolioSummary": {},
  "properties": [
    {
      "propertyId": "FL-ORL-102",
      "name": "Orlando Lakeside Villas",
      "market": "Orlando",
      "city": "Orlando",
      "county": "Orange",
      "riskScore": 81,
      "riskLevel": "Critical",
      "estimatedRepairExposure": 78000,
      "scoreBreakdown": {},
      "riskDrivers": [],
      "recommendedActions": [],
      "recommendedDraftWorkOrders": [],
      "recommendedContractors": [],
      "llmContext": {}
    }
  ],
  "llmInputs": {},
  "dataSources": [],
  "metadata": {}
}
```

## Key Response Fields for Frontend Rendering

- `portfolioSummary`: Layer 2 operational overview.
- `properties[]`: affected property results.
- `properties[].riskScore`: Layer 2 risk score; distinct from Layer 1 `riskScore_v2`.
- `properties[].riskLevel`: `Low`, `Medium`, `High`, or `Critical`.
- `properties[].estimatedRepairExposure`: operational repair exposure estimate.
- `properties[].riskDrivers`: explainers for operational risk.
- `properties[].recommendedActions`: immediate operational actions.
- `properties[].recommendedDraftWorkOrders`: proposed work orders requiring user confirmation.
- `properties[].recommendedContractors`: contractor recommendations embedded in each property result.

## Error Handling

- `400` when `time` does not match an available timeline point.

## Fallback Behavior

If unavailable, keep Layer 1 Portfolio Intelligence visible and show Layer 2 operational response as unavailable.

## What Frontend Must NOT Do

- Do not treat Layer 2 `riskScore` as Layer 1 `riskScore_v2`.
- Do not confirm draft work orders without user action.
- Do not assume contractor recommendations are available as a separate endpoint.

---

# POST /api/ai/recommendations

## Purpose

Returns deterministic AI-style recommendation content generated from Layer 2 risk-engine output. This is not the Layer 3 AI Copilot endpoint.

## When the Frontend Should Call It

Use for legacy/demo recommendation panels if needed. Prefer `/api/ai-copilot/analyze` for the Layer 3 Copilot experience.

## Request Body

```json
{
  "time": "2026-06-12T14:00:00-04:00",
  "propertyId": "FL-ORL-102"
}
```

## Required Parameters

None.

## Optional Parameters

- `time`: defaults to `DEFAULT_ANALYSIS_TIME`.
- `propertyId`: when omitted, returns portfolio-level recommendation.

## Default Behavior

Without `propertyId`, returns portfolio-level recommendation. With `propertyId`, returns property-level recommendation for that affected property.

## Example Request

```js
const response = await fetch(`${apiBaseUrl}/api/ai/recommendations`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    time: "2026-06-12T14:00:00-04:00",
    propertyId: "FL-ORL-102",
  }),
});
```

## Example Response Shape

```json
{
  "generationMode": "template",
  "summary": "string",
  "recommendedActions": [],
  "recommendedContractors": []
}
```

## Key Response Fields for Frontend Rendering

Shape is service-generated and may differ between portfolio and property responses. Render defensively.

## Error Handling

- `400` for invalid timeline point.
- `404` when `propertyId` is not found in affected results.

## Fallback Behavior

Use static recommendation text or hide the legacy recommendation panel.

## What Frontend Must NOT Do

- Do not confuse this endpoint with the AI Copilot.
- Do not call AI Platform from the browser.

---

# POST /api/notifications/draft

## Purpose

Returns resident notification drafts for one affected property.

## When the Frontend Should Call It

Call when a user opens a notification-draft workflow for a specific affected property.

## Request Body

```json
{
  "time": "2026-06-12T14:00:00-04:00",
  "propertyId": "FL-ORL-102"
}
```

## Required Parameters

- `propertyId`

## Optional Parameters

- `time`: defaults to `DEFAULT_ANALYSIS_TIME`.

## Default Behavior

Uses default analysis time when `time` is omitted.

## Example Request

```js
const response = await fetch(`${apiBaseUrl}/api/notifications/draft`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    propertyId: "FL-ORL-102",
    time: "2026-06-12T14:00:00-04:00",
  }),
});
```

## Example Response Shape

```json
{
  "generationMode": "template",
  "eventId": "TOR-FL-2026-0612",
  "analysisTime": "2026-06-12T14:00:00-04:00",
  "propertyId": "FL-ORL-102",
  "propertyName": "Orlando Lakeside Villas",
  "riskLevel": "Critical",
  "sms": "string",
  "pushNotification": {},
  "email": {}
}
```

## Key Response Fields for Frontend Rendering

- `sms`: SMS draft.
- `pushNotification`: push notification draft object.
- `email`: email draft object.
- `riskLevel`: display risk context.

## Error Handling

- `400` when `propertyId` is missing or `time` is invalid.
- `404` when `propertyId` is not found in affected results.

## Fallback Behavior

Show "notification draft unavailable" and allow user to retry.

## What Frontend Must NOT Do

- Do not send notifications automatically.
- Do not treat drafts as confirmed or delivered communications.

---

# POST /api/work-orders/draft

## Purpose

Returns draft work orders for one affected property. Drafts require user confirmation.

## When the Frontend Should Call It

Call when a user views operational response tasks for a specific affected property.

## Request Body

```json
{
  "time": "2026-06-12T14:00:00-04:00",
  "propertyId": "FL-ORL-102"
}
```

## Required Parameters

- `propertyId`

## Optional Parameters

- `time`: defaults to `DEFAULT_ANALYSIS_TIME`.

## Default Behavior

Uses default analysis time when `time` is omitted.

## Example Request

```js
const response = await fetch(`${apiBaseUrl}/api/work-orders/draft`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    propertyId: "FL-ORL-102",
    time: "2026-06-12T14:00:00-04:00",
  }),
});
```

## Example Response Shape

```json
{
  "eventId": "TOR-FL-2026-0612",
  "analysisTime": "2026-06-12T14:00:00-04:00",
  "propertyId": "FL-ORL-102",
  "propertyName": "Orlando Lakeside Villas",
  "riskLevel": "Critical",
  "riskScore": 81,
  "draftWorkOrders": [
    {
      "title": "Post-storm roof inspection",
      "category": "Roof",
      "priority": "Urgent",
      "reason": "string",
      "recommendedTiming": "string",
      "requiresUserConfirmation": true
    }
  ]
}
```

## Key Response Fields for Frontend Rendering

- `draftWorkOrders[]`: render as reviewable tasks.
- `draftWorkOrders[].requiresUserConfirmation`: should be true for draft actions.
- `priority`: display priority badge.
- `recommendedTiming`: display scheduling guidance.

## Error Handling

- `400` when `propertyId` is missing or `time` is invalid.
- `404` when `propertyId` is not found in affected results.

## Fallback Behavior

Show "draft work orders unavailable" and keep property risk details visible.

## What Frontend Must NOT Do

- Do not create confirmed work orders from this endpoint alone.
- Do not bypass user confirmation.

---

# POST /api/work-orders/confirm

## Purpose

Creates a confirmed mock work order from a draft work order and stores it in local Flask storage.

## When the Frontend Should Call It

Call only after the user explicitly confirms a draft work order.

## Request Body

```json
{
  "time": "2026-06-12T14:00:00-04:00",
  "propertyId": "FL-ORL-102",
  "draftIndex": 0,
  "confirmedBy": "demo-user"
}
```

## Required Parameters

Current route requires:

- `propertyId`

Frontend should also send:

- `draftIndex`

## Optional Parameters

- `time`: defaults to `DEFAULT_ANALYSIS_TIME`.
- `draftIndex`: current backend defaults to `0`, but frontend should not rely on that default.
- `confirmedBy`: defaults to `demo-user`.

## Default Behavior

Uses the selected property's `recommendedDraftWorkOrders[draftIndex]` and creates a local mock confirmed work order.

## Example Request

```js
const response = await fetch(`${apiBaseUrl}/api/work-orders/confirm`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    propertyId: "FL-ORL-102",
    draftIndex: 0,
    confirmedBy: currentUserId ?? "demo-user",
  }),
});
```

## Example Response Shape

```json
{
  "workOrderId": "AI-WO-1234ABCD",
  "source": "ai_risk_engine",
  "status": "Confirmed",
  "confirmedAt": "2026-06-11T00:00:00+00:00",
  "confirmedBy": "demo-user",
  "eventId": "TOR-FL-2026-0612",
  "analysisTime": "2026-06-12T14:00:00-04:00",
  "propertyId": "FL-ORL-102",
  "propertyName": "Orlando Lakeside Villas",
  "riskLevel": "Critical",
  "riskScore": 81,
  "draftIndex": 0,
  "workOrder": {}
}
```

## Key Response Fields for Frontend Rendering

- `workOrderId`: confirmation identifier.
- `status`: should be `Confirmed`.
- `confirmedAt`: timestamp.
- `workOrder`: selected draft content.

## Error Handling

- `400` when `propertyId` is missing, `draftIndex` is not an integer, `draftIndex` is out of range, or `time` is invalid.
- `404` when `propertyId` is not found in affected results.
- `201` on successful confirmation.

## Fallback Behavior

Show confirmation failure and keep draft work order visible for retry.

## What Frontend Must NOT Do

- Do not call automatically.
- Do not assume this creates a real external CMMS/work-order record.
- Do not hide the local/demo nature of the confirmation.

---

# Contractor Recommendations

## Current Behavior

There is no standalone contractor recommendation endpoint in the current Flask routes.

Contractor recommendations are currently embedded in Layer 2 property results:

- `GET /api/risk/properties` -> `properties[].recommendedContractors`
- `POST /api/ai/recommendations` may include recommendation content derived from `recommendedContractors`

## Frontend Guidance

Render contractor recommendations from `properties[].recommendedContractors` when showing Layer 2 affected property detail.

## Known Gap

A dedicated endpoint such as `GET /api/contractors/recommendations` or `POST /api/contractors/recommend` does not currently exist.

---

# Frontend Pseudo-Code

```js
const defaultScenario = {
  portfolioId: "FL-DEMO",
  analysisYear: 2026,
  stormEventId: "TOR-FL-2026-0612",
};

async function loadPortfolioIntelligence(scenario = defaultScenario) {
  const params = new URLSearchParams();
  if (scenario.portfolioId) params.set("portfolioId", scenario.portfolioId);
  if (scenario.analysisYear) params.set("analysisYear", String(scenario.analysisYear));
  if (scenario.stormEventId) params.set("stormEventId", scenario.stormEventId);

  const response = await fetch(
    `${apiBaseUrl}/api/portfolio/intelligence?${params.toString()}`
  );

  if (!response.ok) {
    throw new Error(`Portfolio Intelligence failed: ${response.status}`);
  }

  return response.json();
}

async function loadAiCopilotAnalysis(scenario = defaultScenario, options = {}) {
  const response = await fetch(`${apiBaseUrl}/api/ai-copilot/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      taskType: options.taskType ?? "portfolio_review",
      userQuestion:
        options.userQuestion ??
        "Summarize the current portfolio storm-risk and capital-planning priorities.",
      scenario,
      dataContext: {
        requestedMetrics:
          options.requestedMetrics ?? ["insuranceGap", "capitalROI", "priorityRanking"],
      },
      compact: true,
    }),
  });

  const body = await response.json().catch(() => null);

  if (!response.ok || body?.mode === "error") {
    throw new Error(
      body?.diagnostics?.aiPlatformError ??
        body?.error ??
        `AI Copilot failed: ${response.status}`
    );
  }

  return body;
}

async function refreshScenario(scenario = defaultScenario) {
  // 1) Metrics first: fast and deterministic. Render as soon as it returns.
  const portfolio = await loadPortfolioIntelligence(scenario);
  renderMetricsUI(portfolio);

  // 2) AI advisory second: may be slow; never blocks or hides the metrics UI.
  try {
    const copilot = await withTimeout(loadAiCopilotAnalysis(scenario), 30_000);
    renderAdvisoryPanel(copilot);
  } catch (error) {
    renderAdvisoryFallback(error); // metrics stay visible
  }
}
```

# Known Gaps

- Only one demo property (FL-LAK-044, intentionally underinsured per `insurance_policies.json` meta note) has a nonzero `insuranceGap`; all other coverage limits exceed their forecast losses.
- `watchList` survives only for backward compatibility; new UI should read `finalPriorityList`.
- No dedicated contractor recommendation endpoint exists; contractor recommendations are embedded in Layer 2 property results.
- `POST /api/work-orders/confirm` creates a local mock confirmation, not a real external work-order system record.
- AI Copilot can return `mode: mock` if mock fallback is enabled and the AI Platform fails or returns invalid JSON.
- AI Copilot can return `mode: error` with HTTP `502` when mock fallback is disabled and the AI Platform fails.
- `POST /api/ai/recommendations` is legacy/template-style content, not the Layer 3 AI Copilot.
- OpenAPI may not fully capture every recent implementation detail; prefer these route-level notes and current tests when integrating.

# Mismatches Between Intended Behavior and Current Implementation

- Intended contractor recommendation endpoints are mentioned as a category, but the current implementation exposes contractor recommendations only inside `/api/risk/properties` and legacy recommendation content.
- The AI Copilot request supports `scenario` and `compact`; older examples without these fields still work through defaults.
- `POST /api/work-orders/confirm` currently defaults `draftIndex` to `0`, although frontend should send it explicitly.

# Validation Notes

- Inspected current Flask routes in `flask_api/app.py`.
- Inspected Portfolio Intelligence response builder in `flask_api/services/portfolio_intelligence_api.py`.
- Inspected AI Copilot adapter behavior in `flask_api/services/ai_copilot_adapter.py`.
- Inspected scope resolution behavior in `flask_api/services/analysis_scope.py`.
- No AI Platform secrets, URLs beyond the local backend base URL, credentials, tokens, prompts, agent IDs, or conversation IDs are included here.

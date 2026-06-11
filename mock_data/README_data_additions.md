# Mock Data Additions — Capital Planning Foundation (Workstream B)

These files are **new** additions for the Layer 1 Portfolio Intelligence layer. They do **not** modify any existing mock data file. Each relates to `properties.json` by `propertyId` foreign key; new fields are kept here rather than inlined into `properties.json`, to protect the existing Layer 2 data contract.

All data is **synthetic demo data**.

## Files

| File | Array / shape | Foreign key | Feeds future Layer 1 metric |
|---|---|---|---|
| `valuations.json` | `valuations[]` | `propertyId` | `lossForecast`, `riskScore_v2` (assetValue factor) |
| `insurance_policies.json` | `policies[]` | `propertyId` | `insuranceGap` |
| `capital_actions.json` | `capitalActions[]` | `propertyId` | `capitalROI` |
| `storm_path.json` | object with `projectedPath[]` | related to event `TOR-FL-2026-0612` | `stormImpactLevel` (distance-from-path) |

Coverage: all **14** existing properties. The 14→20 expansion (gap D5) was intentionally **skipped** this round to keep the change small and safe; the existing engine and 14-property set are untouched.

## Engineered variation (for demo prioritization realism)

To make downstream ranking interesting, exposure is deliberately spread:

**High-exposure (designed to produce a positive future `insuranceGap` and/or strong `capitalROI`):**
- `FL-STP-077` St. Petersburg — flood High, built 1978, **insuredValue 6.5M < replacementValue 8.9M**, high deductibles, excludes Flood + Storm surge. Roof-replacement action has high risk reduction.
- `FL-MEL-066` Melbourne — flood High, **insured 5.8M < replacement 7.8M**, excludes Flood/Surge, near the storm path's east-coast segment.
- `FL-FMY-136` Fort Myers — coastal, flood High, **insured 9.5M < replacement 13.6M**, 500K windstorm deductible, roof ACV exclusion.
- `FL-SRQ-084` Sarasota — built 1986, **insured 7.0M < replacement 8.6M**, surge exclusion.

**Clean / well-covered (designed as low-priority contrast):**
- `FL-TPA-205` Tampa — Class A, built 2015, full Property+Windstorm+Flood, insured = replacement.
- `FL-MIA-222` Miami — Class A-, built 2011, full coverage incl. flood.
- `FL-PBI-173` West Palm Beach — Class A, built 2018, full coverage.
- `FL-NAP-051` Naples — Class A, built 2009, full coverage incl. flood.

Capital actions span both high value-for-money (e.g. `CAP-STP-1`, `CAP-LAK-1`, `CAP-FMY-1` roof replacements with 0.28–0.32 risk reduction) and low (e.g. `CAP-PBI-1`, `CAP-TPA-1` generators with 0.04–0.05), so future `capitalROI` will rank meaningfully.

## Storm path

`storm_path.json` traces a west-to-east track across central Florida (Pinellas/Hillsborough → Polk/Osceola → Orange/Seminole → Brevard/Volusia) consistent with the county timeline already in `weather_events.json`. The `projectedPath` lat/lng points let a future `stormImpactLevel` calculation measure each property's distance to the track. Properties along the central corridor (Orlando, Kissimmee) and east coast (Melbourne, Daytona) sit closest.

## Constraints honored

- No existing mock file structure changed (verified by leaving all 6 original files untouched).
- No calculation logic added — data only.
- Every `propertyId` referenced exists in `properties.json` (verified).

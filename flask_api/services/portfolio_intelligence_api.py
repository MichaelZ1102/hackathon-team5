"""Read-only aggregation for the Layer 1 Portfolio Intelligence API.

Joins the deterministic Phase A (assetHealthScore) and Phase B
(stormImpactLevel, riskScore_v2, lossForecast) results with property master
data and shapes them into a single read-only response for the frontend and the
(future) AI Copilot.

This module performs NO calculation of its own beyond aggregation/sorting; all
numeric metrics come from portfolio_intelligence (Phase A) and capital_planning
(Phase B). It does not touch Layer 2, the JS engine, or riskScore_v1.

Not yet implemented (intentionally): insuranceGap, capitalROI, priorityRanking.
The watchList here uses a TEMPORARY deterministic sort, not a final ranking.
"""

from services.capital_planning import compute_phase_b
from services.data_loader import load_json
from services.portfolio_intelligence import compute_all_asset_health


CALCULATION_VERSION = "layer1-phaseA+B-v1"
INCLUDED_METRICS = ["assetHealthScore", "stormImpactLevel", "riskScore_v2", "lossForecast"]
MISSING_METRICS = ["insuranceGap", "capitalROI", "priorityRanking"]
DATA_SOURCES_USED = [
    "mock_data/properties.json",
    "mock_data/work_orders.json",
    "mock_data/valuations.json",
    "mock_data/storm_path.json",
]

_CONFIDENCE_RANK = {"Low": 0, "Medium": 1, "High": 2}

# How many properties appear on the temporary watch list.
WATCH_LIST_SIZE = 5


def _least_confident(levels):
    """Return the most severe (lowest) confidence among a list of levels."""
    present = [lvl for lvl in levels if lvl in _CONFIDENCE_RANK]
    if not present:
        return "Low"
    return min(present, key=lambda lvl: _CONFIDENCE_RANK[lvl])


def _index_by_property(results):
    return {r["propertyId"]: r for r in results}


def build_portfolio_intelligence(data_dir=None):
    """Build the full read-only Portfolio Intelligence payload.

    Returns a dict with portfolioSummary, propertyIntelligenceResults,
    watchList, and diagnostics. Deterministic for a fixed data set.
    """
    if data_dir is None:
        properties_data = load_json("properties.json")
    else:
        from pathlib import Path

        properties_data = load_json(Path(data_dir) / "properties.json")

    properties = properties_data.get("properties", [])

    asset_health = _index_by_property(compute_all_asset_health(data_dir))
    phase_b = compute_phase_b(data_dir)
    storm = _index_by_property(phase_b["stormImpactLevel"])
    risk_v2 = _index_by_property(phase_b["riskScore_v2"])
    loss = _index_by_property(phase_b["lossForecast"])

    property_results = []
    warnings = []

    for prop in properties:
        pid = prop.get("propertyId")
        ah = asset_health.get(pid)
        si = storm.get(pid)
        rv = risk_v2.get(pid)
        lf = loss.get(pid)

        # Aggregate drivers + data-quality notes across all four metrics.
        drivers = []
        notes = []
        confidences = []
        for metric_result in (ah, si, rv, lf):
            if not metric_result:
                continue
            drivers.extend(metric_result.get("drivers", []))
            notes.extend(metric_result.get("dataQualityNotes", []))
            confidences.append(metric_result.get("confidence", "Low"))

        overall_confidence = _least_confident(confidences)

        if lf and lf.get("expectedLoss") is None:
            warnings.append(f"{pid}: lossForecast not computable (missing valuation)")
        if overall_confidence == "Low":
            warnings.append(f"{pid}: overall confidence is Low")

        property_results.append(
            {
                "propertyId": pid,
                "propertyName": prop.get("name"),
                "county": prop.get("county"),
                "location": {
                    "city": prop.get("city"),
                    "market": prop.get("market"),
                    "lat": prop.get("lat"),
                    "lng": prop.get("lng"),
                },
                "assetHealthScore": ah,
                "stormImpactLevel": si,
                "riskScore_v2": rv,
                "lossForecast": lf,
                "drivers": drivers,
                "confidence": overall_confidence,
                "dataQualityNotes": notes,
            }
        )

    # Deterministic ordering of the full result set by propertyId.
    property_results.sort(key=lambda r: r["propertyId"])

    watch_list = _build_watch_list(property_results)
    summary = _build_summary(property_results, watch_list)
    diagnostics = {
        "calculationVersion": CALCULATION_VERSION,
        "includedMetrics": list(INCLUDED_METRICS),
        "missingMetrics": list(MISSING_METRICS),
        "dataSourcesUsed": list(DATA_SOURCES_USED),
        "warnings": warnings,
    }

    return {
        "portfolioSummary": summary,
        "propertyIntelligenceResults": property_results,
        "watchList": watch_list,
        "diagnostics": diagnostics,
    }


def _watch_sort_key(result):
    """TEMPORARY deterministic watch-list sort (NOT the final priorityRanking).

    Higher riskScore_v2, then higher lossForecast, then lower assetHealthScore
    (worse health = higher priority). propertyId is the final tie-breaker so the
    order is fully deterministic.
    """
    rv = result.get("riskScore_v2") or {}
    lf = result.get("lossForecast") or {}
    ah = result.get("assetHealthScore") or {}
    risk = rv.get("score", 0)
    expected_loss = lf.get("expectedLoss") or 0
    health = ah.get("score", 100)  # missing health treated as healthy (low priority)
    # Negate the "higher is more urgent" fields; health ascending; id ascending.
    return (-risk, -expected_loss, health, result.get("propertyId", ""))


def _build_watch_list(property_results):
    ordered = sorted(property_results, key=_watch_sort_key)
    watch = []
    for rank, r in enumerate(ordered[:WATCH_LIST_SIZE], start=1):
        rv = r.get("riskScore_v2") or {}
        lf = r.get("lossForecast") or {}
        ah = r.get("assetHealthScore") or {}
        si = r.get("stormImpactLevel") or {}
        watch.append(
            {
                "watchRank": rank,
                "propertyId": r["propertyId"],
                "propertyName": r["propertyName"],
                "county": r["county"],
                "riskScore_v2": rv.get("score"),
                "riskBand": rv.get("band"),
                "stormImpactLevel": si.get("level"),
                "lossForecast": lf.get("expectedLoss"),
                "assetHealthScore": ah.get("score"),
                "drivers": (rv.get("drivers") or [])[:3],
                "confidence": r["confidence"],
                "note": "Temporary deterministic sort; final priorityRanking not yet implemented.",
            }
        )
    return watch


def _build_summary(property_results, watch_list):
    total = len(property_results)

    severe_count = sum(
        1
        for r in property_results
        if (r.get("stormImpactLevel") or {}).get("level") == "Severe"
    )
    high_risk_count = sum(
        1
        for r in property_results
        if (r.get("riskScore_v2") or {}).get("band") == "High"
    )

    losses = [
        (r.get("lossForecast") or {}).get("expectedLoss")
        for r in property_results
    ]
    total_loss = round(sum(v for v in losses if v is not None), 2)

    health_scores = [
        (r.get("assetHealthScore") or {}).get("score")
        for r in property_results
    ]
    health_scores = [s for s in health_scores if s is not None]
    avg_health = round(sum(health_scores) / len(health_scores), 1) if health_scores else None

    # topRiskDrivers: drivers from the watch-list properties' riskScore_v2,
    # de-duplicated while preserving order (most-urgent first).
    top_drivers = []
    seen = set()
    for w in watch_list:
        for d in w.get("drivers", []):
            if d not in seen:
                seen.add(d)
                top_drivers.append(d)

    return {
        "totalProperties": total,
        "severeImpactCount": severe_count,
        "highRiskCount": high_risk_count,
        "totalLossForecast": total_loss,
        "averageAssetHealthScore": avg_health,
        "topRiskDrivers": top_drivers[:6],
    }


__all__ = ["build_portfolio_intelligence", "CALCULATION_VERSION"]

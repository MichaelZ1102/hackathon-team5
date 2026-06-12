"""Guards for mock-data referential integrity and storm alignment.

The 2026-06 merge of main replaced properties.json/work_orders.json with a
290-property dataset while valuations/insurance/capital actions still pointed
at the old FL-* ids, silently nulling lossForecast/insuranceGap/capitalROI
portfolio-wide. These tests make that failure mode loud:

  * no orphaned valuation / policy / capital-action records
  * full valuation + policy coverage (so lossForecast/insuranceGap compute)
  * the demo focal points (visible gap, strong ROI candidate) stay intact
  * Layer 1 (storm_path.json) and Layer 2 (risk_engine) analyze the SAME storm
"""

import json
import os
import sys

FLASK_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FLASK_API_DIR not in sys.path:
    sys.path.insert(0, FLASK_API_DIR)

import pytest

from services.data_loader import load_json
from services.phase_c import compute_phase_c
from services.risk_engine import DEFAULT_EVENT_ID


@pytest.fixture(scope="module")
def property_ids():
    return {p["propertyId"] for p in load_json("properties.json")["properties"]}


# ---------------------------------------------------------------------------
# Referential integrity: every join-dependent file matches properties.json
# ---------------------------------------------------------------------------

def test_no_orphaned_valuations(property_ids):
    valuations = load_json("valuations.json")["valuations"]
    orphans = [v["propertyId"] for v in valuations if v["propertyId"] not in property_ids]
    assert orphans == [], f"valuations reference unknown properties: {orphans[:5]}"


def test_no_orphaned_insurance_policies(property_ids):
    policies = load_json("insurance_policies.json")["policies"]
    orphans = [p["propertyId"] for p in policies if p["propertyId"] not in property_ids]
    assert orphans == [], f"policies reference unknown properties: {orphans[:5]}"


def test_no_orphaned_capital_actions(property_ids):
    actions = load_json("capital_actions.json")["capitalActions"]
    orphans = [a["propertyId"] for a in actions if a["propertyId"] not in property_ids]
    assert orphans == [], f"capital actions reference unknown properties: {orphans[:5]}"


def test_every_property_has_valuation_and_policy(property_ids):
    """Full coverage keeps lossForecast/insuranceGap computable portfolio-wide."""
    valued = {v["propertyId"] for v in load_json("valuations.json")["valuations"]}
    insured = {p["propertyId"] for p in load_json("insurance_policies.json")["policies"]}
    assert property_ids - valued == set(), f"{len(property_ids - valued)} properties lack a valuation"
    assert property_ids - insured == set(), f"{len(property_ids - insured)} properties lack a policy"


def test_no_orphaned_work_orders(property_ids):
    work_orders = load_json("work_orders.json")["workOrders"]
    orphans = {w["propertyId"] for w in work_orders} - property_ids
    assert orphans == set(), f"work orders reference unknown properties: {sorted(orphans)[:5]}"


# ---------------------------------------------------------------------------
# Demo focal points: metrics stay visible and computable
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def phase_c():
    return compute_phase_c()


def test_loss_forecast_computable_for_demo_subset(property_ids):
    """All 290 properties must produce a numeric lossForecast (valuation joins)."""
    from services.capital_planning import compute_phase_b

    losses = compute_phase_b()["lossForecast"]
    not_computable = [r["propertyId"] for r in losses if r["expectedLoss"] is None]
    assert not_computable == [], (
        f"lossForecast not computable for {len(not_computable)} properties, "
        f"e.g. {not_computable[:5]}"
    )


def test_at_least_one_visible_insurance_gap(phase_c):
    gaps = [g for g in phase_c["insuranceGap"] if (g["insuranceGap"] or 0) > 0]
    assert gaps, "no property shows a visible insuranceGap; demo focal point lost"


def test_priority_properties_have_computable_capital_roi(phase_c):
    """Every generated capital action must produce a numeric ROI, and at least
    one priority property must have a strong (>1x) best action."""
    rois = phase_c["capitalROI"]
    assert rois, "no capital actions in dataset"
    not_computable = [r["capitalActionId"] for r in rois if r["capitalROI"] is None]
    assert not_computable == [], f"capitalROI not computable for: {not_computable[:5]}"
    best = phase_c["bestCapitalActionByProperty"]
    strong = [b for b in best.values() if b and b["capitalROI"] and b["capitalROI"] > 1.0]
    assert strong, "no property has a capital action with ROI > 1x"


def test_top_priority_ranking_not_low_confidence(phase_c):
    """The head of the final ranking must not be Low confidence (that was the
    symptom of the broken joins)."""
    top = phase_c["priorityRanking"][0]
    assert top["confidence"] in ("High", "Medium"), (
        f"top priority {top['propertyId']} is Low confidence: {top['dataQualityNotes']}"
    )


# ---------------------------------------------------------------------------
# Storm alignment: Layer 1 and Layer 2 must analyze the same default event
# ---------------------------------------------------------------------------

def test_layer1_and_layer2_default_storm_events_align():
    storm_path = load_json("storm_path.json")
    assert storm_path["stormEventId"] == DEFAULT_EVENT_ID, (
        f"Layer 1 storm_path.json ({storm_path['stormEventId']}) and Layer 2 "
        f"default event ({DEFAULT_EVENT_ID}) are different storms"
    )


def test_storm_path_matches_weather_event_track_window():
    """storm_path.json must stay derived from the weather event it names."""
    storm_path = load_json("storm_path.json")
    events = load_json("weather_events.json")["events"]
    event = next(e for e in events if e["id"] == storm_path["stormEventId"])
    assert storm_path["impactWindowStart"] == event["timeRange"]["start"]
    assert storm_path["impactWindowEnd"] == event["timeRange"]["end"]
    assert len(storm_path["projectedPath"]) == len(event["timeline"])
    assert storm_path["windSpeedMph"] == max(
        pt["windSpeedMph"] for pt in event["timeline"]
    )

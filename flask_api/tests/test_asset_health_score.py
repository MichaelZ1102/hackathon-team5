"""Tests for Layer 1 assetHealthScore (Workstream A, Phase A).

Run from the hackathon-team5 directory:
    flask_api/.venv/bin/python -m pytest flask_api/tests/ -v
"""

import os
import sys

# Make the flask_api package root importable (mirrors how app.py is run) so that
# ``import services.*`` resolves regardless of pytest's invocation directory.
FLASK_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FLASK_API_DIR not in sys.path:
    sys.path.insert(0, FLASK_API_DIR)

import pytest

from services.layer1_schema import (
    ASSET_HEALTH_METRIC,
    REQUIRED_FIELDS,
    band_for_score,
    validate_layer1_result,
)
from services.portfolio_intelligence import (
    ANALYSIS_YEAR,
    compute_all_asset_health,
    compute_asset_health_score,
)


# ---------------------------------------------------------------------------
# (a) Known hand-computed input -> exact expected score (locks the formula)
# ---------------------------------------------------------------------------

def test_known_input_exact_score():
    """Lock the formula with a fully hand-computed example.

    Penalties:
      building age 2026-1996 = 30y  -> -8  (>=25 tier)
      hvac age 13y                  -> -8  (>=12 tier)
      roof age 22y                  -> -18 (>=20 tier)
      exterior Poor                 -> -12
      1 open critical WO            -> -8
      2 recurring WOs               -> -10 (5 each)
      repair spend 1500+1500+0=3000 -> -5  (>=2500 tier)
    total penalty = 69 -> score = 100 - 69 = 31 -> band Poor
    """
    prop = {
        "propertyId": "TEST-1",
        "yearBuilt": 1996,
        "roofAgeYears": 22,
        "hvacAvgAgeYears": 13,
        "exteriorCondition": "Poor",
    }
    work_orders = [
        {"category": "Roof Leak", "status": "Open", "isRepeatIssue": True, "cost": 1500},
        {"category": "Water Intrusion", "status": "Completed", "isRepeatIssue": True, "cost": 1500},
        {"category": "Gutter Cleaning", "status": "Completed", "isRepeatIssue": False, "cost": 0},
    ]

    result = compute_asset_health_score(prop, work_orders)

    assert result["score"] == 31
    assert result["band"] == "Poor"
    assert result["confidence"] == "High"
    assert result["dataQualityNotes"] == []
    assert result["metric"] == ASSET_HEALTH_METRIC


def test_pristine_property_scores_100():
    """A new, well-maintained property with no penalties scores a perfect 100."""
    prop = {
        "propertyId": "TEST-NEW",
        "yearBuilt": ANALYSIS_YEAR,  # brand new
        "roofAgeYears": 1,
        "hvacAvgAgeYears": 1,
        "exteriorCondition": "Good",
    }
    work_orders = [
        {"category": "Gutter Cleaning", "status": "Completed", "isRepeatIssue": False, "cost": 100},
        {"category": "Exterior Lighting", "status": "Completed", "isRepeatIssue": False, "cost": 100},
        {"category": "Fence Damage", "status": "Completed", "isRepeatIssue": False, "cost": 100},
    ]

    result = compute_asset_health_score(prop, work_orders)

    assert result["score"] == 100
    assert result["band"] == "Strong"
    assert result["confidence"] == "High"


# ---------------------------------------------------------------------------
# (b) Boundary cases
# ---------------------------------------------------------------------------

def test_very_old_roof_and_building_drives_low_score():
    """Extreme age values push the score toward Poor without going below 0."""
    prop = {
        "propertyId": "TEST-OLD",
        "yearBuilt": 1950,        # ~76y -> -12
        "roofAgeYears": 40,       # -> -18
        "hvacAvgAgeYears": 30,    # -> -8
        "exteriorCondition": "Poor",  # -> -12
    }
    result = compute_asset_health_score(prop, [])

    # age 12+8 + roof 18+12 = 50 penalty -> 50, but no work orders => Low confidence
    assert 0 <= result["score"] <= 100
    assert result["score"] == 50
    assert result["band"] == "Concerning"
    assert result["confidence"] == "Low"


def test_score_never_below_zero():
    """Stacked maximum penalties clamp at 0 rather than going negative."""
    prop = {
        "propertyId": "TEST-WORST",
        "yearBuilt": 1900,
        "roofAgeYears": 99,
        "hvacAvgAgeYears": 99,
        "exteriorCondition": "Poor",
    }
    work_orders = [
        {"category": "Roof Leak", "status": "Open", "isRepeatIssue": True, "cost": 5000}
        for _ in range(10)
    ]
    result = compute_asset_health_score(prop, work_orders)

    assert result["score"] == 0
    assert result["band"] == "Poor"


def test_zero_work_orders_downgrades_confidence():
    """No work order history => Low confidence and an explanatory note."""
    prop = {
        "propertyId": "TEST-NOWO",
        "yearBuilt": 2018,  # ~8y, no age penalty
        "roofAgeYears": 8,
        "hvacAvgAgeYears": 6,
        "exteriorCondition": "Good",
    }
    result = compute_asset_health_score(prop, [])

    assert result["confidence"] == "Low"
    assert any("No work order history" in note for note in result["dataQualityNotes"])
    # No maintenance penalties means a healthy score despite low confidence.
    assert result["score"] == 100


def test_all_recurring_work_orders_caps_recurring_penalty():
    """Many recurring issues stop adding penalty once the cap is reached."""
    prop = {
        "propertyId": "TEST-RECUR",
        "yearBuilt": 2015,
        "roofAgeYears": 5,
        "hvacAvgAgeYears": 4,
        "exteriorCondition": "Good",
    }
    # 6 recurring, non-critical, zero-cost work orders.
    # recurring penalty would be 6*5=30 but is capped at 20.
    work_orders = [
        {"category": "Gutter Cleaning", "status": "Completed", "isRepeatIssue": True, "cost": 0}
        for _ in range(6)
    ]
    result = compute_asset_health_score(prop, work_orders)

    # Only the capped recurring penalty applies: 100 - 20 = 80.
    assert result["score"] == 80
    assert result["band"] == "Strong"
    assert any("recurring" in d.lower() for d in result["drivers"])


def test_open_critical_work_order_penalty_caps():
    """Open critical work order penalty is capped regardless of count."""
    prop = {
        "propertyId": "TEST-CRIT",
        "yearBuilt": 2015,
        "roofAgeYears": 5,
        "hvacAvgAgeYears": 4,
        "exteriorCondition": "Good",
    }
    # 5 open critical WOs => 5*8=40 but capped at 24. Not flagged recurring.
    work_orders = [
        {"category": "Roof Leak", "status": "Open", "isRepeatIssue": False, "cost": 0}
        for _ in range(5)
    ]
    result = compute_asset_health_score(prop, work_orders)

    # 100 - 24 = 76 (only the capped open-critical penalty applies).
    assert result["score"] == 76


def test_completed_critical_work_orders_not_counted_as_open():
    """Completed critical work orders incur no open-critical penalty."""
    prop = {
        "propertyId": "TEST-DONE",
        "yearBuilt": 2015,
        "roofAgeYears": 5,
        "hvacAvgAgeYears": 4,
        "exteriorCondition": "Good",
    }
    work_orders = [
        {"category": "Roof Leak", "status": "Completed", "isRepeatIssue": False, "cost": 0},
        {"category": "Water Intrusion", "status": "Closed", "isRepeatIssue": False, "cost": 0},
        {"category": "HVAC Exterior Unit", "status": "Completed", "isRepeatIssue": False, "cost": 0},
    ]
    result = compute_asset_health_score(prop, work_orders)

    # No open critical, no recurring, no cost penalty -> perfect.
    assert result["score"] == 100
    assert result["confidence"] == "High"


# ---------------------------------------------------------------------------
# (c) Missing-field case -> confidence downgraded, notes non-empty, no crash
# ---------------------------------------------------------------------------

def test_missing_single_field_downgrades_to_medium():
    """One missing required field => Medium confidence and a note, no crash."""
    prop = {
        "propertyId": "TEST-MISS1",
        # yearBuilt missing
        "roofAgeYears": 12,
        "hvacAvgAgeYears": 8,
        "exteriorCondition": "Fair",
    }
    work_orders = [
        {"category": "Roof Leak", "status": "Completed", "isRepeatIssue": True, "cost": 1200},
        {"category": "Window Seal", "status": "Completed", "isRepeatIssue": False, "cost": 800},
        {"category": "Gutter Damage", "status": "Completed", "isRepeatIssue": False, "cost": 600},
    ]
    result = compute_asset_health_score(prop, work_orders)

    assert result["confidence"] == "Medium"
    assert result["dataQualityNotes"]  # non-empty
    assert any("yearBuilt" in note for note in result["dataQualityNotes"])
    # Missing field is not guessed: building-age penalty is simply omitted.
    assert not any("Building age" in d for d in result["drivers"])


def test_missing_multiple_fields_downgrades_to_low():
    """Two or more missing required fields => Low confidence, notes for each."""
    prop = {
        "propertyId": "TEST-MISS2",
        # yearBuilt and roofAgeYears missing
        "hvacAvgAgeYears": 11,
        "exteriorCondition": "Fair",
    }
    work_orders = [
        {"category": "Roof Leak", "status": "Completed", "isRepeatIssue": True, "cost": 1200},
        {"category": "Window Seal", "status": "Completed", "isRepeatIssue": False, "cost": 800},
        {"category": "Gutter Damage", "status": "Completed", "isRepeatIssue": False, "cost": 600},
    ]
    result = compute_asset_health_score(prop, work_orders)

    assert result["confidence"] == "Low"
    assert any("yearBuilt" in note for note in result["dataQualityNotes"])
    assert any("roofAgeYears" in note for note in result["dataQualityNotes"])


def test_empty_property_does_not_crash():
    """A nearly empty record must not raise and must report data gaps."""
    result = compute_asset_health_score({"propertyId": "TEST-EMPTY"}, [])

    assert result["confidence"] == "Low"
    assert result["dataQualityNotes"]
    assert 0 <= result["score"] <= 100
    # With no penalties computable, score defaults to 100 (Strong) but Low confidence.
    assert result["score"] == 100


# ---------------------------------------------------------------------------
# (d) Schema shape: every result has all required keys/types, band matches score
# ---------------------------------------------------------------------------

def _assert_valid_shape(result):
    problems = validate_layer1_result(result)
    assert problems == [], f"schema problems: {problems}"

    for field, expected_type in REQUIRED_FIELDS.items():
        assert field in result, f"missing field {field}"
        assert isinstance(result[field], expected_type), (
            f"{field} should be {expected_type.__name__}"
        )

    # Band must match the score's range.
    assert result["band"] == band_for_score(result["score"])


def test_single_result_schema_shape():
    prop = {
        "propertyId": "TEST-SHAPE",
        "yearBuilt": 2000,
        "roofAgeYears": 16,
        "hvacAvgAgeYears": 10,
        "exteriorCondition": "Fair",
    }
    result = compute_asset_health_score(prop, [])
    _assert_valid_shape(result)


def test_band_matches_score_across_thresholds():
    """Band boundaries align with the design doc (80/60/40)."""
    assert band_for_score(100) == "Strong"
    assert band_for_score(80) == "Strong"
    assert band_for_score(79) == "Stable"
    assert band_for_score(60) == "Stable"
    assert band_for_score(59) == "Concerning"
    assert band_for_score(40) == "Concerning"
    assert band_for_score(39) == "Poor"
    assert band_for_score(0) == "Poor"


def test_compute_all_asset_health_on_real_data():
    """Every result from the real mock data conforms to the schema."""
    results = compute_all_asset_health()

    assert len(results) == 14  # current portfolio size
    seen_ids = set()
    for result in results:
        _assert_valid_shape(result)
        assert result["metric"] == ASSET_HEALTH_METRIC
        seen_ids.add(result["propertyId"])
    assert len(seen_ids) == 14  # all unique

    # Deterministic ordering: worst health first.
    scores = [r["score"] for r in results]
    assert scores == sorted(scores)


def test_deterministic_repeatable():
    """Repeated runs over the same data produce identical output."""
    first = compute_all_asset_health()
    second = compute_all_asset_health()
    assert first == second


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

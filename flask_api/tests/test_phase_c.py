"""Focused + integration tests for the Layer 1 Phase C calculation chain.

Covers insuranceGap, capitalROI, and priorityRanking individually (including
the missing-data null paths), plus integration through the Portfolio
Intelligence API and the compact AI Copilot state. Layer 2 smoke checks prove
the operational workflows are unaffected.
"""

import os
import sys

FLASK_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FLASK_API_DIR not in sys.path:
    sys.path.insert(0, FLASK_API_DIR)

import pytest

from app import app as flask_app
from services.layer1_schema import (
    validate_capital_roi_result,
    validate_insurance_gap_result,
    validate_priority_ranking_result,
)
from services.phase_c import (
    PLANNING_PERIOD_YEARS,
    PRIORITY_WEIGHTS,
    compute_capital_roi,
    compute_insurance_gap,
    compute_phase_c,
    compute_priority_ranking,
    select_best_capital_action,
)


@pytest.fixture(autouse=True)
def offline_env(monkeypatch):
    monkeypatch.setenv("AI_COPILOT_SKIP_DOTENV", "1")
    for name in (
        "AI_PLATFORM_AGENT_ENDPOINT", "AI_PLATFORM_AGENT_ID", "AI_PLATFORM_API_KEY",
        "AI_PLATFORM_USERNAME", "AI_PLATFORM_PASSWORD", "AI_PLATFORM_CONVERSATION_ID",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def client():
    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


def make_loss(expected_loss, confidence="High"):
    return {"expectedLoss": expected_loss, "confidence": confidence}


POLICY = {
    "policyId": "POL-TEST",
    "propertyId": "TEST-001",
    "coverageLimit": 1_000_000,
    "namedStormDeductible": 100_000,
    "windstormDeductible": 50_000,
}


# ===========================================================================
# insuranceGap
# ===========================================================================

def test_insurance_gap_zero_when_coverage_absorbs_loss():
    # loss 500k, deductible 100k -> 400k needs coverage, limit 1M covers it.
    result = compute_insurance_gap("TEST-001", make_loss(500_000), POLICY)
    assert validate_insurance_gap_result(result) == []
    assert result["insuranceGap"] == 0.0
    assert result["applicableDeductible"] == 100_000.0
    assert result["deductibleType"] == "namedStorm"
    assert result["coveredAmount"] == 400_000.0


def test_insurance_gap_positive_when_loss_exceeds_limit_plus_deductible():
    # loss 1.5M: deductible 100k + covered 1.0M (limit) -> gap 400k.
    result = compute_insurance_gap("TEST-001", make_loss(1_500_000), POLICY)
    assert result["insuranceGap"] == pytest.approx(400_000.0)
    assert result["coveredAmount"] == 1_000_000.0
    assert any("exceeds coverage" in d for d in result["drivers"])


def test_insurance_gap_falls_back_to_windstorm_deductible():
    policy = dict(POLICY)
    del policy["namedStormDeductible"]
    result = compute_insurance_gap("TEST-001", make_loss(500_000), policy)
    assert result["deductibleType"] == "windstorm"
    assert result["applicableDeductible"] == 50_000.0


def test_insurance_gap_no_deductible_assumed_zero_with_note():
    policy = {"policyId": "POL-X", "coverageLimit": 1_000_000}
    result = compute_insurance_gap("TEST-001", make_loss(500_000), policy)
    assert result["applicableDeductible"] == 0.0
    assert result["deductibleType"] is None
    assert result["confidence"] == "Medium"
    assert any("deductible assumed 0" in n for n in result["dataQualityNotes"])


def test_insurance_gap_null_when_policy_missing():
    result = compute_insurance_gap("TEST-001", make_loss(500_000), None)
    assert validate_insurance_gap_result(result) == []
    assert result["insuranceGap"] is None
    assert result["confidence"] == "Low"
    assert any("No insurance policy" in n for n in result["dataQualityNotes"])


def test_insurance_gap_null_when_loss_forecast_missing():
    result = compute_insurance_gap("TEST-001", make_loss(None), POLICY)
    assert result["insuranceGap"] is None
    assert result["confidence"] == "Low"
    assert any("lossForecast not available" in n for n in result["dataQualityNotes"])


def test_insurance_gap_inherits_loss_confidence():
    result = compute_insurance_gap("TEST-001", make_loss(500_000, "Medium"), POLICY)
    assert result["confidence"] == "Medium"


# ===========================================================================
# capitalROI
# ===========================================================================

ACTION = {
    "capitalActionId": "CAP-TEST-1",
    "propertyId": "TEST-001",
    "actionType": "Roof Replacement",
    "estimatedCost": 200_000,
    "estimatedRiskReduction": 0.2,
    "usefulLifeYears": 25,
}


def test_capital_roi_formula():
    # avoided = 500k * 0.2 * min(25, PLANNING_PERIOD_YEARS) ; roi = avoided / 200k
    result = compute_capital_roi(ACTION, make_loss(500_000))
    assert validate_capital_roi_result(result) == []
    horizon = min(25, PLANNING_PERIOD_YEARS)
    expected_avoided = 500_000 * 0.2 * horizon
    assert result["horizonYears"] == horizon
    assert result["estimatedAvoidedLoss"] == pytest.approx(expected_avoided)
    assert result["capitalROI"] == pytest.approx(expected_avoided / 200_000, abs=0.01)


def test_capital_roi_horizon_capped_by_useful_life():
    action = dict(ACTION, usefulLifeYears=2)
    result = compute_capital_roi(action, make_loss(500_000))
    assert result["horizonYears"] == 2
    assert result["estimatedAvoidedLoss"] == pytest.approx(500_000 * 0.2 * 2)


def test_capital_roi_missing_useful_life_uses_planning_period_with_note():
    action = {k: v for k, v in ACTION.items() if k != "usefulLifeYears"}
    result = compute_capital_roi(action, make_loss(500_000))
    assert result["horizonYears"] == PLANNING_PERIOD_YEARS
    assert result["confidence"] == "Medium"
    assert any("usefulLifeYears" in n for n in result["dataQualityNotes"])


def test_capital_roi_null_when_loss_forecast_missing():
    result = compute_capital_roi(ACTION, make_loss(None))
    assert result["capitalROI"] is None
    assert result["estimatedAvoidedLoss"] is None
    assert result["confidence"] == "Low"
    assert any("lossForecast" in n for n in result["dataQualityNotes"])


def test_capital_roi_null_when_cost_or_reduction_missing():
    no_cost = {k: v for k, v in ACTION.items() if k != "estimatedCost"}
    no_red = {k: v for k, v in ACTION.items() if k != "estimatedRiskReduction"}
    for action, missing in ((no_cost, "estimatedCost"), (no_red, "estimatedRiskReduction")):
        result = compute_capital_roi(action, make_loss(500_000))
        assert result["capitalROI"] is None
        assert any(missing in n for n in result["dataQualityNotes"])


def test_best_capital_action_picks_highest_roi_deterministically():
    a = compute_capital_roi(dict(ACTION, capitalActionId="CAP-A"), make_loss(500_000))
    b = compute_capital_roi(
        dict(ACTION, capitalActionId="CAP-B", estimatedCost=50_000), make_loss(500_000)
    )
    none_roi = compute_capital_roi(
        {"capitalActionId": "CAP-C", "propertyId": "TEST-001"}, make_loss(None)
    )
    best = select_best_capital_action([a, none_roi, b])
    assert best["capitalActionId"] == "CAP-B"
    assert select_best_capital_action([none_roi]) is None


# ===========================================================================
# priorityRanking
# ===========================================================================

def make_ranking_input(pid, risk=50, loss=100_000, health=50, gap=50_000, roi=2.0):
    return {
        "propertyId": pid,
        "riskScoreV2": risk, "riskConfidence": "High",
        "lossForecast": loss, "lossConfidence": "High",
        "assetHealthScore": health, "healthConfidence": "High",
        "insuranceGap": gap, "gapConfidence": "High",
        "bestCapitalROI": roi,
    }


def test_priority_ranking_weights_sum_to_one():
    assert sum(PRIORITY_WEIGHTS.values()) == pytest.approx(1.0)


def test_priority_ranking_orders_by_weighted_score():
    worst = make_ranking_input("P-WORST", risk=90, loss=1_000_000, health=20,
                               gap=500_000, roi=10.0)
    best = make_ranking_input("P-BEST", risk=10, loss=10_000, health=95, gap=0, roi=0.5)
    results = compute_priority_ranking([best, worst])
    assert [r["propertyId"] for r in results] == ["P-WORST", "P-BEST"]
    assert [r["priorityRank"] for r in results] == [1, 2]
    assert results[0]["priorityScore"] > results[1]["priorityScore"]
    for r in results:
        assert validate_priority_ranking_result(r) == []


def test_priority_ranking_max_normalized_property_scores_full_components():
    only = make_ranking_input("P-ONLY", risk=100, loss=500_000, health=0,
                              gap=100_000, roi=5.0)
    result = compute_priority_ranking([only])[0]
    # The single property is the portfolio max on every normalized component.
    assert result["components"]["lossForecastNormalized"] == 100.0
    assert result["components"]["insuranceGapNormalized"] == 100.0
    assert result["components"]["capitalUrgency"] == 100.0
    assert result["priorityScore"] == 100.0


def test_priority_ranking_missing_components_contribute_zero_with_notes():
    incomplete = {
        "propertyId": "P-SPARSE",
        "riskScoreV2": 80, "riskConfidence": "High",
        "lossForecast": None, "lossConfidence": "Low",
        "assetHealthScore": None, "healthConfidence": None,
        "insuranceGap": None, "gapConfidence": "Low",
        "bestCapitalROI": None,
    }
    result = compute_priority_ranking([incomplete])[0]
    assert result["confidence"] == "Low"
    assert result["priorityScore"] == pytest.approx(PRIORITY_WEIGHTS["riskScore_v2"] * 80, abs=0.1)
    assert sum("contributes 0" in n for n in result["dataQualityNotes"]) == 4


def test_priority_ranking_ties_break_on_property_id():
    a = make_ranking_input("P-A")
    b = make_ranking_input("P-B")
    results = compute_priority_ranking([b, a])
    assert [r["propertyId"] for r in results] == ["P-A", "P-B"]


# ===========================================================================
# Orchestration on the real demo data
# ===========================================================================

def test_compute_phase_c_covers_full_portfolio():
    out = compute_phase_c()
    assert len(out["insuranceGap"]) == 14
    assert len(out["priorityRanking"]) == 14
    assert len(out["capitalROI"]) == 18  # one per capital action
    assert [r["priorityRank"] for r in out["priorityRanking"]] == list(range(1, 15))
    for r in out["insuranceGap"]:
        assert validate_insurance_gap_result(r) == []
    for r in out["capitalROI"]:
        assert validate_capital_roi_result(r) == []
    for r in out["priorityRanking"]:
        assert validate_priority_ranking_result(r) == []


def test_compute_phase_c_is_deterministic():
    assert compute_phase_c() == compute_phase_c()


def test_demo_data_shows_a_visible_insurance_gap():
    """The demo dataset intentionally underinsures FL-LAK-044 (limit cut at the
    2026 renewal — see insurance_policies.json meta note) so the insuranceGap
    metric is visible in the UI. Guard that the demo story stays intact."""
    gaps = compute_phase_c()["insuranceGap"]
    lak = next(g for g in gaps if g["propertyId"] == "FL-LAK-044")
    assert lak["insuranceGap"] is not None and lak["insuranceGap"] > 0
    total = sum(g["insuranceGap"] for g in gaps if g["insuranceGap"] is not None)
    assert total > 0


def test_compute_phase_c_respects_analysis_scope():
    from services.analysis_scope import resolve_analysis_scope

    default = compute_phase_c()
    scoped = compute_phase_c(scope=resolve_analysis_scope(analysis_year=2025))
    # A different work-order/valuation scope changes the loss basis and so the
    # downstream capital ROI numbers.
    default_rois = [r["capitalROI"] for r in default["capitalROI"]]
    scoped_rois = [r["capitalROI"] for r in scoped["capitalROI"]]
    assert default_rois != scoped_rois


# ===========================================================================
# Portfolio Intelligence API integration
# ===========================================================================

def test_api_response_includes_phase_c_metrics(client):
    data = client.get("/api/portfolio/intelligence").get_json()

    first = data["propertyIntelligenceResults"][0]
    assert first["insuranceGap"]["metric"] == "insuranceGap"
    assert first["priorityRanking"]["metric"] == "priorityRanking"
    assert "bestCapitalAction" in first

    # Final ranking list exists alongside the compatibility watchList.
    assert "watchList" in data
    final = data["finalPriorityList"]
    assert len(final) == 14
    assert [row["priorityRank"] for row in final] == list(range(1, 15))
    assert final[0]["bestCapitalAction"] is not None
    assert "capitalROI" in final[0]["bestCapitalAction"]

    # Per-action results are exposed for the capital-planning view.
    assert len(data["capitalActionResults"]) == 18

    diag = data["diagnostics"]
    for m in ("insuranceGap", "capitalROI", "priorityRanking"):
        assert m in diag["includedMetrics"]
        assert m not in diag["missingMetrics"]

    summary = data["portfolioSummary"]
    assert "totalInsuranceGap" in summary
    assert summary["topPriorityPropertyId"] == final[0]["propertyId"]


def test_api_phase_c_changes_with_analysis_year(client):
    d26 = client.get("/api/portfolio/intelligence").get_json()
    d25 = client.get("/api/portfolio/intelligence?analysisYear=2025").get_json()
    rois26 = [a["capitalROI"] for a in d26["capitalActionResults"]]
    rois25 = [a["capitalROI"] for a in d25["capitalActionResults"]]
    assert rois26 != rois25


def test_watch_list_unchanged_for_compatibility(client):
    """The pre-Phase C watchList sort must still be served unchanged."""
    watch = client.get("/api/portfolio/intelligence").get_json()["watchList"]
    assert 1 <= len(watch) <= 5
    risks = [w["riskScore_v2"] for w in watch]
    assert risks == sorted(risks, reverse=True)


# ===========================================================================
# AI Copilot compact state integration
# ===========================================================================

def test_compact_state_includes_phase_c_metrics():
    from services.ai_copilot_adapter import build_compact_ai_copilot_state

    state = build_compact_ai_copilot_state(
        task_type="capital_planning",
        user_question="Where should capital go first?",
        data_context={"requestedMetrics": ["insuranceGap", "capitalROI", "priorityRanking"]},
    )

    for m in ("insuranceGap", "capitalROI", "priorityRanking"):
        assert m in state["availableMetrics"]
    assert state["missingMetrics"] == []

    # Watch list is ordered by the final ranking and carries Phase C values.
    ranks = [w["priorityRank"] for w in state["watchList"]]
    assert ranks == sorted(ranks)
    top = state["watchList"][0]
    assert top["priorityRank"] == 1
    assert top["insuranceGap"] is not None
    assert top["bestCapitalAction"] is not None
    assert top["bestCapitalAction"]["capitalROI"] is not None

    assert "totalInsuranceGap" in state["portfolioSummary"]
    assert state["portfolioSummary"]["topPriorityPropertyId"] == top["propertyId"]

    # Still compact: no raw per-record history goes to the AI.
    assert state["layer1Results"] == []
    assert state["operationalActions"] == []


def test_ai_copilot_endpoint_diagnostics_report_phase_c(client):
    resp = client.post(
        "/api/ai-copilot/analyze",
        json={
            "taskType": "capital_planning",
            "userQuestion": "Rank capital actions.",
            "scenario": {"analysisYear": 2026},
            "dataContext": {"requestedMetrics": ["insuranceGap", "capitalROI", "priorityRanking"]},
        },
    )
    assert resp.status_code == 200
    diag = resp.get_json()["diagnostics"]
    for m in ("insuranceGap", "capitalROI", "priorityRanking"):
        assert m in diag["availableMetrics"]
    assert diag["missingMetrics"] == []


# ===========================================================================
# Layer 2 smoke: operational workflows untouched
# ===========================================================================

def test_layer2_endpoints_still_work(client):
    timeline = client.get("/api/risk/timeline")
    assert timeline.status_code == 200

    props = client.get("/api/risk/properties")
    assert props.status_code == 200
    body = props.get_json()
    assert body["properties"]
    assert "riskScore" in body["properties"][0]  # riskScore_v1 intact

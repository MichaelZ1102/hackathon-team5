from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, request
from flask_swagger_ui import get_swaggerui_blueprint

from services.content_generator import (
    build_notification_draft,
    build_portfolio_recommendation,
    build_property_recommendation,
)
from services.data_loader import load_json, write_json
from services.portfolio_intelligence_api import build_portfolio_intelligence
from services.risk_engine import DEFAULT_ANALYSIS_TIME, analyze_risk


APP_DIR = Path(__file__).resolve().parent
STORAGE_DIR = APP_DIR / "storage"
CONFIRMED_WORK_ORDERS_FILE = STORAGE_DIR / "confirmed_work_orders.json"

app = Flask(__name__)

SWAGGER_URL = "/api/docs"
OPENAPI_URL = "/api/openapi.json"

swagger_ui = get_swaggerui_blueprint(
    SWAGGER_URL,
    OPENAPI_URL,
    config={"app_name": "Florida Tornado Response AI Analysis API"},
)

app.register_blueprint(swagger_ui, url_prefix=SWAGGER_URL)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


@app.route("/api/openapi.json", methods=["GET"])
def get_openapi_spec():
    return jsonify(load_json(APP_DIR / "openapi.json"))


@app.route("/api/risk/timeline", methods=["GET"])
def get_risk_timeline():
    weather_data = load_json("weather_events.json")
    return jsonify(
        {
            "event": weather_data["event"],
            "timeline": weather_data["timeline"],
            "meta": weather_data["meta"],
        }
    )


@app.route("/api/risk/properties", methods=["GET"])
def get_risk_properties():
    analysis_time = request.args.get("time", DEFAULT_ANALYSIS_TIME)
    try:
        result = analyze_risk(analysis_time)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(result)


@app.route("/api/portfolio/intelligence", methods=["GET"])
def get_portfolio_intelligence():
    """Read-only Layer 1 Portfolio Intelligence (Phase A + Phase B).

    Aggregates assetHealthScore, stormImpactLevel, riskScore_v2, and
    lossForecast. Does not modify any state, does not touch Layer 2 / the JS
    engine / riskScore_v1, and does not yet include insuranceGap, capitalROI,
    or priorityRanking.
    """
    return jsonify(build_portfolio_intelligence())


@app.route("/api/ai/recommendations", methods=["POST", "OPTIONS"])
def post_ai_recommendations():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    analysis_time = body.get("time", DEFAULT_ANALYSIS_TIME)
    property_id = body.get("propertyId")

    try:
        result = analyze_risk(analysis_time)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if property_id:
        property_result = find_property(result, property_id)
        if not property_result:
            return jsonify({"error": f"Property {property_id} was not found in affected results"}), 404
        return jsonify(build_property_recommendation(result, property_result))

    return jsonify(build_portfolio_recommendation(result))


@app.route("/api/notifications/draft", methods=["POST", "OPTIONS"])
def post_notification_draft():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    analysis_time = body.get("time", DEFAULT_ANALYSIS_TIME)
    property_id = body.get("propertyId")

    if not property_id:
        return jsonify({"error": "propertyId is required"}), 400

    try:
        result = analyze_risk(analysis_time)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    property_result = find_property(result, property_id)
    if not property_result:
        return jsonify({"error": f"Property {property_id} was not found in affected results"}), 404

    return jsonify(build_notification_draft(result, property_result))


@app.route("/api/work-orders/draft", methods=["POST", "OPTIONS"])
def post_work_order_draft():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    analysis_time = body.get("time", DEFAULT_ANALYSIS_TIME)
    property_id = body.get("propertyId")

    if not property_id:
        return jsonify({"error": "propertyId is required"}), 400

    try:
        result = analyze_risk(analysis_time)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    property_result = find_property(result, property_id)
    if not property_result:
        return jsonify({"error": f"Property {property_id} was not found in affected results"}), 404

    return jsonify(
        {
            "eventId": result["eventId"],
            "analysisTime": result["analysisTime"],
            "propertyId": property_result["propertyId"],
            "propertyName": property_result["name"],
            "riskLevel": property_result["riskLevel"],
            "riskScore": property_result["riskScore"],
            "draftWorkOrders": property_result["recommendedDraftWorkOrders"],
        }
    )


@app.route("/api/work-orders/confirm", methods=["POST", "OPTIONS"])
def post_work_order_confirm():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    analysis_time = body.get("time", DEFAULT_ANALYSIS_TIME)
    property_id = body.get("propertyId")
    draft_index = body.get("draftIndex", 0)
    confirmed_by = body.get("confirmedBy", "demo-user")

    if not property_id:
        return jsonify({"error": "propertyId is required"}), 400

    try:
        draft_index = int(draft_index)
    except (TypeError, ValueError):
        return jsonify({"error": "draftIndex must be an integer"}), 400

    try:
        result = analyze_risk(analysis_time)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    property_result = find_property(result, property_id)
    if not property_result:
        return jsonify({"error": f"Property {property_id} was not found in affected results"}), 404

    drafts = property_result["recommendedDraftWorkOrders"]
    if draft_index < 0 or draft_index >= len(drafts):
        return jsonify({"error": f"draftIndex {draft_index} is out of range"}), 400

    confirmed_work_order = {
        "workOrderId": f"AI-WO-{uuid4().hex[:8].upper()}",
        "source": "ai_risk_engine",
        "status": "Confirmed",
        "confirmedAt": datetime.now(timezone.utc).isoformat(),
        "confirmedBy": confirmed_by,
        "eventId": result["eventId"],
        "analysisTime": result["analysisTime"],
        "propertyId": property_result["propertyId"],
        "propertyName": property_result["name"],
        "riskLevel": property_result["riskLevel"],
        "riskScore": property_result["riskScore"],
        "draftIndex": draft_index,
        "workOrder": drafts[draft_index],
    }

    existing = load_confirmed_work_orders()
    existing["confirmedWorkOrders"].append(confirmed_work_order)
    write_json(CONFIRMED_WORK_ORDERS_FILE, existing)

    return jsonify(confirmed_work_order), 201


def find_property(analysis_result, property_id):
    for property_result in analysis_result["properties"]:
        if property_result["propertyId"] == property_id:
            return property_result
    return None


def load_confirmed_work_orders():
    if CONFIRMED_WORK_ORDERS_FILE.exists():
        return load_json(CONFIRMED_WORK_ORDERS_FILE)
    return {"confirmedWorkOrders": []}


if __name__ == "__main__":
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=True)

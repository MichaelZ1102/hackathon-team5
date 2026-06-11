from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from uuid import uuid4

import requests
from flask import Flask, jsonify, request
from flask_swagger_ui import get_swaggerui_blueprint

from services.content_generator import (
    build_notification_draft,
    build_portfolio_recommendation,
    build_property_recommendation,
)
from services.data_loader import load_json, write_json
from services.risk_engine import (
    DEFAULT_ANALYSIS_TIME,
    DEFAULT_EVENT_ID,
    analyze_risk,
    normalize_analysis_time,
    select_weather_event,
    select_weather_event_for_time,
)


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
    event_id = request.args.get("eventId")
    analysis_time = request.args.get("time")
    try:
        if analysis_time:
            analysis_time = normalize_analysis_time(analysis_time)
            selected_event = select_weather_event_for_time(
                weather_data, analysis_time, event_id
            )
        else:
            selected_event = select_weather_event(
                weather_data, event_id or DEFAULT_EVENT_ID
            )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    timeline = selected_event.get("timeline", [])
    timeline_point = None
    if analysis_time:
        timeline_point = next(
            (point for point in timeline if point["timestamp"] == analysis_time),
            None,
        )
        if not timeline_point:
            available_times = ", ".join(point["timestamp"] for point in timeline)
            return (
                jsonify(
                    {
                        "error": (
                            f"No weather timeline point found for {analysis_time} "
                            f"in event {selected_event['id']}. Available times: {available_times}"
                        )
                    }
                ),
                400,
            )

    return jsonify(
        {
            "event": selected_event,
            "timeline": [timeline_point] if timeline_point else timeline,
            "timelinePoint": timeline_point,
            "events": weather_data.get("events", []),
            "metadata": weather_data.get("metadata", {}),
            "meta": weather_data.get("metadata", {}),
        }
    )


@app.route("/api/risk/properties", methods=["GET"])
def get_risk_properties():
    analysis_time = request.args.get("time", DEFAULT_ANALYSIS_TIME)
    event_id = request.args.get("eventId")
    try:
        result = analyze_risk(analysis_time, event_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(result)


@app.route("/api/risk/storm-center/properties", methods=["GET"])
def get_properties_by_storm_center():
    analysis_time = request.args.get("time", DEFAULT_ANALYSIS_TIME)
    event_id = request.args.get("eventId")

    try:
        analysis_time = normalize_analysis_time(analysis_time)
        weather_data = load_json("weather_events.json")
        weather_event = select_weather_event_for_time(
            weather_data, analysis_time, event_id
        )
        timeline_point = find_timeline_point(weather_event, analysis_time)
        if not timeline_point:
            available_times = ", ".join(
                point["timestamp"] for point in weather_event.get("timeline", [])
            )
            return (
                jsonify(
                    {
                        "error": (
                            f"No weather timeline point found for {analysis_time} "
                            f"in event {weather_event['id']}. Available times: {available_times}"
                        )
                    }
                ),
                400,
            )

        center, center_source = resolve_storm_center(timeline_point)
        radius_km = parse_positive_float_arg(
            "radiusKm", timeline_point.get("impactRadiusKm") or 50
        )
        risk_result = analyze_risk(analysis_time, weather_event["id"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    properties_data = load_json("properties.json")
    risk_by_property_id = {
        property_result["propertyId"]: property_result
        for property_result in risk_result["properties"]
    }
    related_properties = []

    for property_item in properties_data["properties"]:
        distance_km = calculate_distance_km(
            center["lat"], center["lng"], property_item["lat"], property_item["lng"]
        )
        if distance_km > radius_km:
            continue

        risk_property = risk_by_property_id.get(property_item["propertyId"])
        related_properties.append(
            build_storm_center_property_result(
                property_item, risk_property, distance_km
            )
        )

    related_properties.sort(key=lambda item: (item["distanceKm"], item["propertyId"]))

    return jsonify(
        {
            "eventId": weather_event["id"],
            "eventName": weather_event["name"],
            "eventType": weather_event["type"],
            "analysisTime": timeline_point["timestamp"],
            "scenarioStage": timeline_point.get("phase", timeline_point.get("stageId")),
            "stormCenter": {
                "lat": center["lat"],
                "lng": center["lng"],
                "source": center_source,
            },
            "radiusKm": radius_km,
            "matchedProperties": len(related_properties),
            "properties": related_properties,
            "timelinePoint": {
                "stageId": timeline_point.get("stageId"),
                "phase": timeline_point.get("phase"),
                "windSpeedMph": timeline_point.get("windSpeedMph"),
                "precipitationMm": timeline_point.get("precipitationMm"),
                "snowfallCm": timeline_point.get("snowfallCm"),
                "impactRadiusKm": timeline_point.get("impactRadiusKm"),
                "confidence": timeline_point.get("confidence"),
            },
            "metadata": {
                "dataType": "synthetic_demo_data",
                "distanceFormula": "haversine",
                "importantNote": (
                    "Related properties are selected by distance from the storm center "
                    "within radiusKm. This output is for demo purposes only."
                ),
            },
        }
    )


@app.route("/api/ai/recommendations", methods=["POST", "OPTIONS"])
def post_ai_recommendations():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    analysis_time = body.get("time", DEFAULT_ANALYSIS_TIME)
    event_id = body.get("eventId")
    property_id = body.get("propertyId")

    try:
        result = analyze_risk(analysis_time, event_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if property_id:
        property_result = find_property(result, property_id)
        if not property_result:
            return property_not_found_response(result, property_id)
        return jsonify(build_property_recommendation(result, property_result))

    return jsonify(build_portfolio_recommendation(result))


@app.route("/api/notifications/draft", methods=["POST", "OPTIONS"])
def post_notification_draft():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    analysis_time = body.get("time", DEFAULT_ANALYSIS_TIME)
    event_id = body.get("eventId")
    property_id = body.get("propertyId")

    if not property_id:
        return jsonify({"error": "propertyId is required"}), 400

    try:
        result = analyze_risk(analysis_time, event_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    property_result = find_property(result, property_id)
    if not property_result:
        return property_not_found_response(result, property_id)

    return jsonify(build_notification_draft(result, property_result))


@app.route("/api/work-orders/draft", methods=["POST", "OPTIONS"])
def post_work_order_draft():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    analysis_time = body.get("time", DEFAULT_ANALYSIS_TIME)
    event_id = body.get("eventId")
    property_id = body.get("propertyId")

    if not property_id:
        return jsonify({"error": "propertyId is required"}), 400

    try:
        result = analyze_risk(analysis_time, event_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    property_result = find_property(result, property_id)
    if not property_result:
        return property_not_found_response(result, property_id)

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
    event_id = body.get("eventId")
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
        result = analyze_risk(analysis_time, event_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    property_result = find_property(result, property_id)
    if not property_result:
        return property_not_found_response(result, property_id)

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


def get_token_with_basic_auth(username, password):
    """Get a Bearer token from the Lessen auth API."""
    api_url = "https://meshstage.lessen.com/auth/token"

    try:
        response = requests.post(api_url, auth=(username, password), headers={"Accept": "*/*"}, timeout=30)
        response.raise_for_status()

        try:
            token_payload = response.json()
        except ValueError:
            return response.text.strip()

        if isinstance(token_payload, str):
            return token_payload.strip()

        if isinstance(token_payload, dict):
            for key in ("access_token", "token", "bearerToken", "bearer_token"):
                token = token_payload.get(key)
                if token:
                    return str(token).strip()

        return response.text.strip()
    except requests.RequestException as exc:
        print(f"Failed to get token: {exc}")
        return None


def call_instruction_api(token, instruction="#TEMPLATE#", template="", text="{}", states=None):
    """Call the Lessen Instruction API."""
    api_url = "https://meshstage.lessen.com/onebrain/instruct/67b8aa0e-702c-45f1-b0fa-cf86d1139b7c"

    if not token:
        print("Failed to call Instruction API: token is required")
        return None

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "instruction": instruction,
        "template": template,
        "text": text,
        "states": states or [],
    }

    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()

        try:
            return response.json()
        except ValueError:
            return response.text
    except requests.RequestException as exc:
        print(f"Failed to call Instruction API: {exc}")
        return None


def find_property(analysis_result, property_id):
    for property_result in analysis_result["properties"]:
        if property_result["propertyId"] == property_id:
            return property_result
    return None


def find_timeline_point(weather_event, analysis_time):
    return next(
        (
            point
            for point in weather_event.get("timeline", [])
            if point["timestamp"] == analysis_time
        ),
        None,
    )


def resolve_storm_center(timeline_point):
    if timeline_point.get("center"):
        return timeline_point["center"], "timelinePoint.center"

    geometry = timeline_point.get("geometry", {})
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Point" and coordinates and len(coordinates) >= 2:
        return {"lat": coordinates[1], "lng": coordinates[0]}, "timelinePoint.geometry"

    raise ValueError("Selected timeline point does not include a storm center")


def parse_positive_float_arg(name, default_value):
    raw_value = request.args.get(name, default_value)
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a positive number")

    if value <= 0:
        raise ValueError(f"{name} must be a positive number")

    return value


def calculate_distance_km(lat1, lng1, lat2, lng2):
    earth_radius_km = 6371.0088
    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)
    delta_lat = radians(lat2 - lat1)
    delta_lng = radians(lng2 - lng1)

    a = (
        sin(delta_lat / 2) ** 2
        + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lng / 2) ** 2
    )
    return round(earth_radius_km * 2 * asin(sqrt(a)), 2)


def build_storm_center_property_result(property_item, risk_property, distance_km):
    result = {
        "propertyId": property_item["propertyId"],
        "name": property_item["name"],
        "market": property_item["market"],
        "city": property_item["city"],
        "county": property_item["county"],
        "lat": property_item["lat"],
        "lng": property_item["lng"],
        "assetType": property_item["assetType"],
        "units": property_item["units"],
        "distanceKm": distance_km,
        "isAffected": bool(risk_property),
    }

    if risk_property:
        result.update(
            {
                "riskScore": risk_property["riskScore"],
                "riskLevel": risk_property["riskLevel"],
                "estimatedRepairExposure": risk_property["estimatedRepairExposure"],
                "recommendedActions": risk_property["recommendedActions"],
                "recommendedDraftWorkOrders": risk_property[
                    "recommendedDraftWorkOrders"
                ],
                "recommendedContractors": risk_property["recommendedContractors"],
            }
        )

    return result


def property_not_found_response(analysis_result, property_id):
    affected_property_ids = [
        property_result["propertyId"] for property_result in analysis_result["properties"]
    ]
    return (
        jsonify(
            {
                "error": (
                    f"Property {property_id} is not affected for event "
                    f"{analysis_result['eventId']} at {analysis_result['analysisTime']}."
                ),
                "eventId": analysis_result["eventId"],
                "analysisTime": analysis_result["analysisTime"],
                "affectedPropertyIds": affected_property_ids,
            }
        ),
        404,
    )


def load_confirmed_work_orders():
    if CONFIRMED_WORK_ORDERS_FILE.exists():
        return load_json(CONFIRMED_WORK_ORDERS_FILE)
    return {"confirmedWorkOrders": []}


if __name__ == "__main__":
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=True)

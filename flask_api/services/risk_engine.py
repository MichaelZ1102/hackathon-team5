from collections import defaultdict

from services.data_loader import load_json


DEFAULT_ANALYSIS_TIME = "2026-06-12T14:00:00-04:00"

WEIGHTS = {
    "weatherRisk": 0.40,
    "assetVulnerability": 0.25,
    "maintenanceRisk": 0.25,
    "businessImpact": 0.10,
}

WEATHER_BASE_SCORE = {
    "Low": 25,
    "Medium": 50,
    "High": 75,
    "Critical": 95,
}

MAINTENANCE_CATEGORY_POINTS = {
    "Roof Leak": 12,
    "Water Intrusion": 12,
    "HVAC Exterior Unit": 8,
    "Exterior Siding": 8,
    "Gutter Damage": 7,
    "Window Seal": 6,
    "Tree Limb Removal": 6,
    "Fence Damage": 4,
    "Exterior Lighting": 3,
    "Gutter Cleaning": 2,
}


def analyze_risk(analysis_time=DEFAULT_ANALYSIS_TIME):
    weather_data = load_json("weather_events.json")
    properties_data = load_json("properties.json")
    work_orders_data = load_json("work_orders.json")
    capex_data = load_json("capex_plan.json")
    lease_data = load_json("lease_exposure.json")
    contractors_data = load_json("contractors.json")

    timeline_point = next(
        (point for point in weather_data["timeline"] if point["time"] == analysis_time),
        None,
    )
    if not timeline_point:
        raise ValueError(f"No weather timeline point found for {analysis_time}")

    weather_by_county = {
        county_weather["county"]: county_weather
        for county_weather in timeline_point["affectedCounties"]
    }
    work_orders_by_property = group_by(work_orders_data["workOrders"], "propertyId")
    capex_by_property = group_by(capex_data["capexItems"], "propertyId")
    lease_by_property = {
        lease["propertyId"]: lease for lease in lease_data["leaseExposure"]
    }

    property_results = []
    for property_item in properties_data["properties"]:
        county_weather = weather_by_county.get(property_item["county"])
        if not county_weather:
            continue

        property_results.append(
            build_property_result(
                property_item=property_item,
                county_weather=county_weather,
                work_orders=work_orders_by_property.get(property_item["propertyId"], []),
                capex_items=capex_by_property.get(property_item["propertyId"], []),
                lease_exposure=lease_by_property.get(property_item["propertyId"], {}),
                contractors=contractors_data["contractors"],
            )
        )

    property_results.sort(key=lambda item: (-item["riskScore"], item["propertyId"]))
    portfolio_summary = summarize_portfolio(
        all_properties=properties_data["properties"],
        affected_results=property_results,
        lease_by_property=lease_by_property,
    )

    return {
        "eventId": weather_data["event"]["eventId"],
        "analysisTime": timeline_point["time"],
        "scenarioStage": timeline_point["stage"],
        "portfolioSummary": portfolio_summary,
        "properties": property_results,
        "llmInputs": {
            "executiveSummaryContext": build_executive_context(
                weather_event=weather_data["event"],
                timeline_point=timeline_point,
                portfolio_summary=portfolio_summary,
                property_results=property_results,
            ),
            "recommendedPromptFiles": [
                "llm_prompts/executive_summary_prompt.md",
                "llm_prompts/vp_email_prompt.md",
            ],
        },
        "dataSources": [
            "mock_data/weather_events.json",
            "mock_data/properties.json",
            "mock_data/work_orders.json",
            "mock_data/capex_plan.json",
            "mock_data/lease_exposure.json",
            "mock_data/contractors.json",
        ],
        "metadata": {
            "dataType": "synthetic_demo_data",
            "engineVersion": "0.1-flask",
            "generatedFor": "hackathon_demo",
            "importantNote": (
                "This output is for demo purposes. It is not official weather, "
                "emergency, insurance, or property condition data."
            ),
        },
    }


def build_property_result(
    property_item,
    county_weather,
    work_orders,
    capex_items,
    lease_exposure,
    contractors,
):
    score_breakdown = {
        "weatherRisk": calculate_weather_risk(county_weather),
        "assetVulnerability": calculate_asset_vulnerability(property_item),
        "maintenanceRisk": calculate_maintenance_risk(work_orders),
        "businessImpact": calculate_business_impact(property_item, lease_exposure),
        "weights": WEIGHTS,
        "formula": (
            "round(weatherRisk * 0.40 + assetVulnerability * 0.25 + "
            "maintenanceRisk * 0.25 + businessImpact * 0.10)"
        ),
    }
    risk_score = calculate_risk_score(score_breakdown)
    risk_level = risk_level_from_score(risk_score)
    draft_work_orders = build_draft_work_orders(
        property_item, work_orders, capex_items, risk_level
    )
    estimated_repair_exposure = estimate_repair_exposure(
        risk_level, draft_work_orders, capex_items
    )

    return {
        "propertyId": property_item["propertyId"],
        "name": property_item["name"],
        "market": property_item["market"],
        "city": property_item["city"],
        "county": property_item["county"],
        "lat": property_item["lat"],
        "lng": property_item["lng"],
        "assetType": property_item["assetType"],
        "units": property_item["units"],
        "isAffected": True,
        "riskScore": risk_score,
        "riskLevel": risk_level,
        "estimatedRepairExposure": estimated_repair_exposure,
        "scoreBreakdown": score_breakdown,
        "riskDrivers": build_risk_drivers(
            property_item, county_weather, work_orders, capex_items, lease_exposure
        ),
        "recommendedActions": build_recommended_actions(risk_level),
        "recommendedDraftWorkOrders": draft_work_orders,
        "recommendedContractors": build_contractor_recommendations(
            property_item, draft_work_orders, contractors
        ),
        "llmContext": build_llm_context(
            property_item,
            county_weather,
            work_orders,
            capex_items,
            estimated_repair_exposure,
        ),
    }


def calculate_weather_risk(county_weather):
    score = WEATHER_BASE_SCORE.get(county_weather["riskLevel"], 0)
    if county_weather["windSpeedMph"] >= 90:
        score += 5
    if county_weather["tornadoProbability"] >= 0.60:
        score += 5
    if county_weather["hailRisk"] == "High":
        score += 3
    return cap_score(score)


def calculate_asset_vulnerability(property_item):
    score = 0

    if property_item["roofAgeYears"] >= 20:
        score += 35
    elif property_item["roofAgeYears"] >= 15:
        score += 25
    elif property_item["roofAgeYears"] >= 10:
        score += 12

    if property_item["hvacAvgAgeYears"] >= 12:
        score += 20
    elif property_item["hvacAvgAgeYears"] >= 10:
        score += 12
    elif property_item["hvacAvgAgeYears"] >= 7:
        score += 6

    if property_item["exteriorCondition"] == "Poor":
        score += 25
    elif property_item["exteriorCondition"] == "Fair":
        score += 12

    if property_item["treeCanopyRisk"] == "High":
        score += 15
    elif property_item["treeCanopyRisk"] == "Medium":
        score += 8

    if property_item["floodZoneExposure"] == "High":
        score += 10
    elif property_item["floodZoneExposure"] == "Moderate":
        score += 5

    return cap_score(score)


def calculate_maintenance_risk(work_orders):
    score = 0
    total_cost = 0

    for work_order in work_orders:
        score += MAINTENANCE_CATEGORY_POINTS.get(work_order["category"], 0)
        if work_order.get("isRepeatIssue"):
            score += 5
        total_cost += work_order.get("cost", 0)

    if total_cost >= 10000:
        score += 15
    elif total_cost >= 5000:
        score += 10
    elif total_cost >= 2500:
        score += 5

    return cap_score(score)


def calculate_business_impact(property_item, lease_exposure):
    score = 0

    if property_item["units"] >= 100:
        score += 30
    elif property_item["units"] >= 70:
        score += 20
    elif property_item["units"] >= 40:
        score += 10

    if property_item["occupancyRate"] >= 0.95:
        score += 20
    elif property_item["occupancyRate"] >= 0.90:
        score += 12

    renewals = lease_exposure.get(
        "renewalsDueNext90Days",
        property_item.get("openLeaseExpirationsNext90Days", 0),
    )
    if renewals >= 20:
        score += 20
    elif renewals >= 10:
        score += 10

    residents = lease_exposure.get("atRiskResidentCount", 0)
    if residents >= 150:
        score += 20
    elif residents >= 100:
        score += 12

    rent = lease_exposure.get("averageMonthlyRent", 0)
    if rent >= 2200:
        score += 10
    elif rent >= 1800:
        score += 5

    return cap_score(score)


def calculate_risk_score(score_breakdown):
    return round(
        score_breakdown["weatherRisk"] * WEIGHTS["weatherRisk"]
        + score_breakdown["assetVulnerability"] * WEIGHTS["assetVulnerability"]
        + score_breakdown["maintenanceRisk"] * WEIGHTS["maintenanceRisk"]
        + score_breakdown["businessImpact"] * WEIGHTS["businessImpact"]
    )


def risk_level_from_score(score):
    if score <= 30:
        return "Low"
    if score <= 60:
        return "Medium"
    if score <= 80:
        return "High"
    return "Critical"


def build_risk_drivers(property_item, county_weather, work_orders, capex_items, lease):
    drivers = [
        f"{property_item['county']} County is in {county_weather['riskLevel']} tornado risk at the selected timeline point.",
        (
            f"County weather shows {county_weather['windSpeedMph']} mph wind speed "
            f"and {county_weather['tornadoProbability']} tornado probability."
        ),
    ]

    if property_item["roofAgeYears"] >= 15:
        drivers.append(
            f"Roof age is {property_item['roofAgeYears']} years, which increases storm vulnerability."
        )
    if property_item["hvacAvgAgeYears"] >= 10:
        drivers.append(f"Average HVAC age is {property_item['hvacAvgAgeYears']} years.")
    if property_item["exteriorCondition"] != "Good":
        drivers.append(f"Exterior condition is {property_item['exteriorCondition']}.")
    if property_item["treeCanopyRisk"] != "Low":
        drivers.append(f"Tree canopy risk is {property_item['treeCanopyRisk']}.")
    if property_item["floodZoneExposure"] == "High":
        drivers.append("Flood zone exposure is High.")

    work_order_summary = format_category_summary(work_orders)
    if work_order_summary:
        drivers.append(f"Recent work order patterns include {work_order_summary}.")

    repeat_count = len([item for item in work_orders if item.get("isRepeatIssue")])
    if repeat_count:
        drivers.append(f"{repeat_count} recent work orders are marked as repeat issues.")

    if capex_items:
        capex_summary = ", ".join(
            f"{item['item']} ({item['priority']})" for item in capex_items
        )
        drivers.append(f"CapEx plan includes {capex_summary}.")

    if lease.get("atRiskResidentCount", 0) >= 150:
        drivers.append(
            f"Estimated resident exposure is {lease['atRiskResidentCount']} people."
        )

    return drivers[:8]


def build_recommended_actions(risk_level):
    if risk_level == "Critical":
        return [
            "Send pre-storm notification to residents.",
            "Pre-book contractor capacity for likely post-storm inspections.",
            "Prepare post-storm inspection draft work orders.",
            "Escalate this property in the VP portfolio impact summary.",
        ]
    if risk_level == "High":
        return [
            "Send resident preparedness notification.",
            "Schedule post-storm inspection readiness.",
            "Check contractor availability for the highest-risk maintenance categories.",
        ]
    if risk_level == "Medium":
        return [
            "Monitor weather timeline changes.",
            "Prepare resident notification draft.",
            "Review recent maintenance history before the storm clears.",
        ]
    return ["Continue monitoring.", "No immediate operational action required."]


def build_draft_work_orders(property_item, work_orders, capex_items, risk_level):
    priority = "Urgent" if risk_level == "Critical" else "High" if risk_level == "High" else "Routine"
    timing = (
        "Within 24 hours after storm clearance"
        if risk_level == "Critical"
        else "Within 48 hours after storm clearance"
    )
    drafts = []

    if (
        property_item["roofAgeYears"] >= 15
        or has_category(work_orders, ["Roof Leak"])
        or has_capex_keyword(capex_items, "roof")
    ):
        drafts.append(
            {
                "title": "Post-storm roof inspection",
                "category": "Roof Inspection",
                "priority": priority,
                "reason": (
                    "Roof age, roof-related work order history, or roof CapEx plan "
                    "indicates elevated storm vulnerability."
                ),
                "recommendedTiming": timing,
                "requiresUserConfirmation": True,
            }
        )

    if (
        property_item["exteriorCondition"] != "Good"
        or has_category(work_orders, ["Water Intrusion", "Window Seal", "Exterior Siding"])
        or has_capex_keyword(capex_items, "exterior")
        or has_capex_keyword(capex_items, "envelope")
    ):
        drafts.append(
            {
                "title": "Post-storm water intrusion and exterior envelope inspection",
                "category": "Exterior Envelope",
                "priority": priority,
                "reason": (
                    "Exterior condition, water intrusion history, or envelope-related "
                    "CapEx indicates possible storm exposure."
                ),
                "recommendedTiming": timing,
                "requiresUserConfirmation": True,
            }
        )

    if property_item["hvacAvgAgeYears"] >= 10 or has_category(
        work_orders, ["HVAC Exterior Unit"]
    ):
        drafts.append(
            {
                "title": "Post-storm exterior HVAC unit inspection",
                "category": "HVAC Exterior Unit",
                "priority": priority,
                "reason": "HVAC age or prior exterior HVAC work orders indicate potential equipment exposure.",
                "recommendedTiming": timing,
                "requiresUserConfirmation": True,
            }
        )

    if property_item["treeCanopyRisk"] == "High" or has_category(
        work_orders, ["Tree Limb Removal"]
    ):
        drafts.append(
            {
                "title": "Tree limb and debris inspection",
                "category": "Tree and Debris",
                "priority": priority,
                "reason": "Tree canopy risk or prior tree limb work orders indicate debris exposure.",
                "recommendedTiming": timing,
                "requiresUserConfirmation": True,
            }
        )

    if not drafts:
        drafts.append(
            {
                "title": "Post-storm exterior safety inspection",
                "category": "Exterior Envelope",
                "priority": priority,
                "reason": "Property is in an affected county at the selected storm timeline point.",
                "recommendedTiming": timing,
                "requiresUserConfirmation": True,
            }
        )

    return drafts[:3]


def build_contractor_recommendations(property_item, draft_work_orders, contractors):
    needed_services = []
    for draft in draft_work_orders:
        service_type = map_category_to_service_type(draft["category"])
        if service_type not in needed_services:
            needed_services.append(service_type)

    recommendations = []
    for service_type in needed_services:
        matches = [
            contractor
            for contractor in contractors
            if contractor["serviceType"] == service_type
            and property_item["county"] in contractor["serviceCounties"]
        ]
        matches.sort(key=lambda item: (item["availableWithinHours"], -item["rating"]))
        if matches and not any(
            item["contractorId"] == matches[0]["contractorId"]
            for item in recommendations
        ):
            match = matches[0]
            recommendations.append(
                {
                    "contractorId": match["contractorId"],
                    "name": match["name"],
                    "serviceType": match["serviceType"],
                    "reason": (
                        f"Serves {property_item['county']} County and matches "
                        f"{service_type} response needs."
                    ),
                    "availableWithinHours": match["availableWithinHours"],
                }
            )

    return recommendations[:3]


def estimate_repair_exposure(risk_level, draft_work_orders, capex_items):
    base_by_level = {
        "Critical": 45000,
        "High": 25000,
        "Medium": 12000,
        "Low": 3000,
    }
    capex_exposure = min(
        sum(item.get("estimatedCost", 0) for item in capex_items) * 0.15,
        30000,
    )
    work_order_exposure = len(draft_work_orders) * 3000
    exposure = base_by_level.get(risk_level, 0) + capex_exposure + work_order_exposure
    return round(exposure / 1000) * 1000


def build_llm_context(property_item, county_weather, work_orders, capex_items, exposure):
    allowed_claims = [
        (
            f"The property is in {property_item['county']} County during a "
            f"{county_weather['riskLevel']} tornado risk timeline point."
        ),
        (
            f"The county weather event includes {county_weather['windSpeedMph']} mph wind speed "
            f"and {county_weather['tornadoProbability']} tornado probability."
        ),
        f"The property roof age is {property_item['roofAgeYears']} years.",
        f"The property exterior condition is {property_item['exteriorCondition']}.",
        "Any AI-generated work order requires user confirmation.",
    ]

    if work_orders:
        allowed_claims.append(
            f"Historical work orders include {format_category_summary(work_orders)}."
        )
    if capex_items:
        allowed_claims.append(
            f"CapEx plan includes {', '.join(item['item'] for item in capex_items)}."
        )

    return {
        "summaryInput": (
            f"{property_item['name']} is in {property_item['county']} County during "
            f"{county_weather['riskLevel']} tornado risk. County weather shows "
            f"{county_weather['windSpeedMph']} mph wind speed and "
            f"{county_weather['tornadoProbability']} tornado probability. The property has "
            f"a {property_item['roofAgeYears']}-year roof, {property_item['exteriorCondition']} "
            f"exterior condition, and {len(work_orders)} recent work orders. "
            f"Estimated repair exposure is {exposure}."
        ),
        "allowedClaims": allowed_claims,
        "disallowedClaims": [
            "Do not claim confirmed physical damage.",
            "Do not claim residents have been injured.",
            "Do not claim evacuation is mandatory.",
            "Do not claim insurance coverage or reimbursement.",
        ],
        "dataLimitations": [
            "No live radar feed is connected in this mock dataset.",
            "No confirmed post-storm inspection results are available yet.",
            "Repair exposure is an estimate for demo purposes.",
        ],
        "recommendedPromptFiles": [
            "llm_prompts/property_risk_explanation_prompt.md",
            "llm_prompts/tenant_notification_prompt.md",
            "llm_prompts/work_order_draft_prompt.md",
            "llm_prompts/contractor_recommendation_prompt.md",
        ],
    }


def summarize_portfolio(all_properties, affected_results, lease_by_property):
    count_by_level = defaultdict(int)
    for result in affected_results:
        count_by_level[result["riskLevel"]] += 1

    market_scores = defaultdict(lambda: {"count": 0, "totalScore": 0})
    for result in affected_results:
        market_scores[result["market"]]["count"] += 1
        market_scores[result["market"]]["totalScore"] += result["riskScore"]

    top_markets = sorted(
        market_scores.items(),
        key=lambda item: (-item[1]["totalScore"], -item[1]["count"], item[0]),
    )

    return {
        "totalProperties": len(all_properties),
        "affectedProperties": len(affected_results),
        "criticalRiskProperties": count_by_level["Critical"],
        "highRiskProperties": count_by_level["High"],
        "mediumRiskProperties": count_by_level["Medium"],
        "lowRiskProperties": count_by_level["Low"],
        "estimatedRepairExposure": sum(
            item["estimatedRepairExposure"] for item in affected_results
        ),
        "residentExposure": sum(
            lease_by_property.get(item["propertyId"], {}).get("atRiskResidentCount", 0)
            for item in affected_results
        ),
        "topAffectedMarkets": [market for market, _ in top_markets[:3]],
    }


def build_executive_context(weather_event, timeline_point, portfolio_summary, property_results):
    top_properties = [
        f"{item['name']} ({item['riskLevel']}, {item['riskScore']})"
        for item in sorted(property_results, key=lambda item: -item["riskScore"])[:3]
    ]
    themes = sorted(
        {
            draft["category"]
            for property_item in property_results
            for draft in property_item["recommendedDraftWorkOrders"]
        }
    )

    return {
        "weatherEvent": weather_event["name"],
        "analysisTime": timeline_point["time"],
        "scenarioStage": timeline_point["stage"],
        "affectedProperties": portfolio_summary["affectedProperties"],
        "criticalRiskProperties": portfolio_summary["criticalRiskProperties"],
        "highRiskProperties": portfolio_summary["highRiskProperties"],
        "estimatedRepairExposure": portfolio_summary["estimatedRepairExposure"],
        "residentExposure": portfolio_summary["residentExposure"],
        "topConcerns": [
            (
                f"{', '.join(portfolio_summary['topAffectedMarkets'])} are the main affected "
                "markets at this timeline point."
            ),
            f"Highest-risk properties: {'; '.join(top_properties)}.",
            f"{', '.join(themes)} are the main post-storm work order themes.",
        ],
        "recommendedNextActions": [
            "Send resident notifications for affected properties.",
            "Pre-book contractors for Critical and High risk assets.",
            "Prepare draft inspection work orders for user confirmation after storm clearance.",
        ],
    }


def format_category_summary(work_orders):
    counts = defaultdict(int)
    for work_order in work_orders:
        counts[work_order["category"]] += 1
    return ", ".join(f"{category} x{count}" for category, count in counts.items())


def has_category(work_orders, categories):
    return any(work_order["category"] in categories for work_order in work_orders)


def has_capex_keyword(capex_items, keyword):
    return any(keyword.lower() in item["item"].lower() for item in capex_items)


def map_category_to_service_type(category):
    return {
        "Roof Inspection": "Roofing",
        "Exterior Envelope": "Exterior Envelope",
        "HVAC Exterior Unit": "HVAC",
        "Tree and Debris": "Tree and Debris",
        "Water Intrusion": "Exterior Envelope",
    }.get(category, category)


def cap_score(score):
    return max(0, min(100, score))


def group_by(items, key):
    grouped = defaultdict(list)
    for item in items:
        grouped[item[key]].append(item)
    return grouped


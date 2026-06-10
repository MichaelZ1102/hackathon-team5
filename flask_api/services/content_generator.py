def build_property_recommendation(analysis_result, property_result):
    return {
        "generationMode": "template_generated",
        "eventId": analysis_result["eventId"],
        "analysisTime": analysis_result["analysisTime"],
        "propertyId": property_result["propertyId"],
        "propertyName": property_result["name"],
        "riskLevel": property_result["riskLevel"],
        "riskScore": property_result["riskScore"],
        "summary": (
            f"{property_result['name']} is currently rated {property_result['riskLevel']} "
            f"with a risk score of {property_result['riskScore']}. "
            f"The main drivers are weather exposure, asset vulnerability, maintenance history, "
            f"and resident or business impact."
        ),
        "topRiskDrivers": property_result["riskDrivers"][:5],
        "recommendedActions": property_result["recommendedActions"],
        "draftWorkOrders": property_result["recommendedDraftWorkOrders"],
        "recommendedContractors": property_result["recommendedContractors"],
        "llmReadyContext": property_result["llmContext"],
    }


def build_portfolio_recommendation(analysis_result):
    summary = analysis_result["portfolioSummary"]
    context = analysis_result["llmInputs"]["executiveSummaryContext"]

    return {
        "generationMode": "template_generated",
        "eventId": analysis_result["eventId"],
        "analysisTime": analysis_result["analysisTime"],
        "scenarioStage": analysis_result["scenarioStage"],
        "summary": (
            f"At {analysis_result['scenarioStage']}, {summary['affectedProperties']} properties "
            f"are affected. {summary['criticalRiskProperties']} are Critical and "
            f"{summary['highRiskProperties']} are High risk. Estimated repair exposure is "
            f"${summary['estimatedRepairExposure']:,}."
        ),
        "portfolioImpact": summary,
        "topConcerns": context["topConcerns"],
        "recommendedNextActions": context["recommendedNextActions"],
        "recommendedPromptFiles": analysis_result["llmInputs"]["recommendedPromptFiles"],
    }


def build_notification_draft(analysis_result, property_result):
    property_name = property_result["name"]
    risk_level = property_result["riskLevel"]
    stage = analysis_result["scenarioStage"]

    sms = (
        f"{property_name}: Severe weather risk is {risk_level}. "
        "Please secure outdoor items, keep phones charged, and follow local emergency guidance. "
        "We will share updates as conditions change."
    )

    return {
        "generationMode": "template_generated",
        "eventId": analysis_result["eventId"],
        "analysisTime": analysis_result["analysisTime"],
        "propertyId": property_result["propertyId"],
        "propertyName": property_name,
        "riskLevel": risk_level,
        "sms": sms[:320],
        "pushNotification": {
            "title": f"{risk_level} Weather Alert",
            "body": f"{property_name}: {stage}. Please review safety steps and watch for updates.",
        },
        "email": {
            "subject": f"{property_name}: Severe Weather Preparedness Update",
            "body": (
                f"Dear Resident,\n\n"
                f"{property_name} is currently included in our {risk_level} severe weather monitoring group. "
                f"Please secure outdoor belongings, keep your phone charged, and follow local emergency guidance. "
                f"Our team is preparing post-storm inspections and will send updates as conditions change.\n\n"
                f"This message is based on current weather risk information and does not indicate confirmed property damage.\n\n"
                f"Property Management Team"
            ),
        },
    }


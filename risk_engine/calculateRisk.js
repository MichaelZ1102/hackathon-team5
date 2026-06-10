const fs = require("fs");
const path = require("path");

const DEFAULT_ANALYSIS_TIME = "2026-06-12T14:00:00-04:00";
const ANALYSIS_TIME = process.argv[2] || DEFAULT_ANALYSIS_TIME;

const PROJECT_ROOT = path.resolve(__dirname, "..");
const MOCK_DATA_DIR = path.join(PROJECT_ROOT, "mock_data");
const OUTPUT_DIR = path.join(__dirname, "output");
const OUTPUT_FILE = path.join(OUTPUT_DIR, "risk_analysis_result.json");

const WEIGHTS = {
  weatherRisk: 0.4,
  assetVulnerability: 0.25,
  maintenanceRisk: 0.25,
  businessImpact: 0.1,
};

const WEATHER_BASE_SCORE = {
  Low: 25,
  Medium: 50,
  High: 75,
  Critical: 95,
};

const MAINTENANCE_CATEGORY_POINTS = {
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
};

function readJson(fileName) {
  const filePath = path.join(MOCK_DATA_DIR, fileName);
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function capScore(score) {
  return Math.max(0, Math.min(100, score));
}

function riskLevelFromScore(score) {
  if (score <= 30) return "Low";
  if (score <= 60) return "Medium";
  if (score <= 80) return "High";
  return "Critical";
}

function priorityFromRiskLevel(riskLevel) {
  if (riskLevel === "Critical") return "Urgent";
  if (riskLevel === "High") return "High";
  return "Routine";
}

function calculateWeatherRisk(countyWeather) {
  if (!countyWeather) return 0;

  let score = WEATHER_BASE_SCORE[countyWeather.riskLevel] || 0;
  if (countyWeather.windSpeedMph >= 90) score += 5;
  if (countyWeather.tornadoProbability >= 0.6) score += 5;
  if (countyWeather.hailRisk === "High") score += 3;

  return capScore(score);
}

function calculateAssetVulnerability(property) {
  let score = 0;

  if (property.roofAgeYears >= 20) score += 35;
  else if (property.roofAgeYears >= 15) score += 25;
  else if (property.roofAgeYears >= 10) score += 12;

  if (property.hvacAvgAgeYears >= 12) score += 20;
  else if (property.hvacAvgAgeYears >= 10) score += 12;
  else if (property.hvacAvgAgeYears >= 7) score += 6;

  if (property.exteriorCondition === "Poor") score += 25;
  else if (property.exteriorCondition === "Fair") score += 12;

  if (property.treeCanopyRisk === "High") score += 15;
  else if (property.treeCanopyRisk === "Medium") score += 8;

  if (property.floodZoneExposure === "High") score += 10;
  else if (property.floodZoneExposure === "Moderate") score += 5;

  return capScore(score);
}

function calculateMaintenanceRisk(workOrders) {
  let score = 0;
  let totalCost = 0;

  for (const workOrder of workOrders) {
    score += MAINTENANCE_CATEGORY_POINTS[workOrder.category] || 0;
    if (workOrder.isRepeatIssue) score += 5;
    totalCost += workOrder.cost || 0;
  }

  if (totalCost >= 10000) score += 15;
  else if (totalCost >= 5000) score += 10;
  else if (totalCost >= 2500) score += 5;

  return capScore(score);
}

function calculateBusinessImpact(property, leaseExposure) {
  let score = 0;

  if (property.units >= 100) score += 30;
  else if (property.units >= 70) score += 20;
  else if (property.units >= 40) score += 10;

  if (property.occupancyRate >= 0.95) score += 20;
  else if (property.occupancyRate >= 0.9) score += 12;

  const renewalsDue = leaseExposure?.renewalsDueNext90Days ?? property.openLeaseExpirationsNext90Days ?? 0;
  if (renewalsDue >= 20) score += 20;
  else if (renewalsDue >= 10) score += 10;

  const residents = leaseExposure?.atRiskResidentCount ?? 0;
  if (residents >= 150) score += 20;
  else if (residents >= 100) score += 12;

  const rent = leaseExposure?.averageMonthlyRent ?? 0;
  if (rent >= 2200) score += 10;
  else if (rent >= 1800) score += 5;

  return capScore(score);
}

function calculateRiskScore(scoreBreakdown) {
  return Math.round(
    scoreBreakdown.weatherRisk * WEIGHTS.weatherRisk +
      scoreBreakdown.assetVulnerability * WEIGHTS.assetVulnerability +
      scoreBreakdown.maintenanceRisk * WEIGHTS.maintenanceRisk +
      scoreBreakdown.businessImpact * WEIGHTS.businessImpact
  );
}

function countByCategory(workOrders) {
  return workOrders.reduce((acc, workOrder) => {
    acc[workOrder.category] = (acc[workOrder.category] || 0) + 1;
    return acc;
  }, {});
}

function hasCategory(workOrders, categories) {
  return workOrders.some((workOrder) => categories.includes(workOrder.category));
}

function formatCategorySummary(workOrders) {
  const counts = countByCategory(workOrders);
  return Object.entries(counts)
    .map(([category, count]) => `${category} x${count}`)
    .join(", ");
}

function buildRiskDrivers(property, countyWeather, workOrders, capexItems, leaseExposure) {
  const drivers = [];
  const repeatCount = workOrders.filter((workOrder) => workOrder.isRepeatIssue).length;
  const workOrderSummary = formatCategorySummary(workOrders);

  drivers.push(
    `${property.county} County is in ${countyWeather.riskLevel} tornado risk at the selected timeline point.`
  );
  drivers.push(
    `County weather shows ${countyWeather.windSpeedMph} mph wind speed and ${countyWeather.tornadoProbability} tornado probability.`
  );

  if (property.roofAgeYears >= 15) {
    drivers.push(`Roof age is ${property.roofAgeYears} years, which increases storm vulnerability.`);
  }
  if (property.hvacAvgAgeYears >= 10) {
    drivers.push(`Average HVAC age is ${property.hvacAvgAgeYears} years.`);
  }
  if (property.exteriorCondition !== "Good") {
    drivers.push(`Exterior condition is ${property.exteriorCondition}.`);
  }
  if (property.treeCanopyRisk !== "Low") {
    drivers.push(`Tree canopy risk is ${property.treeCanopyRisk}.`);
  }
  if (property.floodZoneExposure === "High") {
    drivers.push("Flood zone exposure is High.");
  }
  if (workOrderSummary) {
    drivers.push(`Recent work order patterns include ${workOrderSummary}.`);
  }
  if (repeatCount > 0) {
    drivers.push(`${repeatCount} recent work orders are marked as repeat issues.`);
  }
  if (capexItems.length > 0) {
    const capexSummary = capexItems.map((item) => `${item.item} (${item.priority})`).join(", ");
    drivers.push(`CapEx plan includes ${capexSummary}.`);
  }
  if ((leaseExposure?.atRiskResidentCount || 0) >= 150) {
    drivers.push(`Estimated resident exposure is ${leaseExposure.atRiskResidentCount} people.`);
  }

  return drivers.slice(0, 8);
}

function buildRecommendedActions(riskLevel) {
  if (riskLevel === "Critical") {
    return [
      "Send pre-storm notification to residents.",
      "Pre-book contractor capacity for likely post-storm inspections.",
      "Prepare post-storm inspection draft work orders.",
      "Escalate this property in the VP portfolio impact summary.",
    ];
  }

  if (riskLevel === "High") {
    return [
      "Send resident preparedness notification.",
      "Schedule post-storm inspection readiness.",
      "Check contractor availability for the highest-risk maintenance categories.",
    ];
  }

  if (riskLevel === "Medium") {
    return [
      "Monitor weather timeline changes.",
      "Prepare resident notification draft.",
      "Review recent maintenance history before the storm clears.",
    ];
  }

  return ["Continue monitoring.", "No immediate operational action required."];
}

function hasCapexKeyword(capexItems, keyword) {
  const lowerKeyword = keyword.toLowerCase();
  return capexItems.some((item) => item.item.toLowerCase().includes(lowerKeyword));
}

function buildDraftWorkOrders(property, workOrders, capexItems, riskLevel) {
  const priority = priorityFromRiskLevel(riskLevel);
  const timing = riskLevel === "Critical" ? "Within 24 hours after storm clearance" : "Within 48 hours after storm clearance";
  const drafts = [];

  if (
    property.roofAgeYears >= 15 ||
    hasCategory(workOrders, ["Roof Leak"]) ||
    hasCapexKeyword(capexItems, "roof")
  ) {
    drafts.push({
      title: "Post-storm roof inspection",
      category: "Roof Inspection",
      priority,
      reason: "Roof age, roof-related work order history, or roof CapEx plan indicates elevated storm vulnerability.",
      recommendedTiming: timing,
      requiresUserConfirmation: true,
    });
  }

  if (
    property.exteriorCondition !== "Good" ||
    hasCategory(workOrders, ["Water Intrusion", "Window Seal", "Exterior Siding"]) ||
    hasCapexKeyword(capexItems, "exterior") ||
    hasCapexKeyword(capexItems, "envelope")
  ) {
    drafts.push({
      title: "Post-storm water intrusion and exterior envelope inspection",
      category: "Exterior Envelope",
      priority,
      reason: "Exterior condition, water intrusion history, or envelope-related CapEx indicates possible storm exposure.",
      recommendedTiming: timing,
      requiresUserConfirmation: true,
    });
  }

  if (property.hvacAvgAgeYears >= 10 || hasCategory(workOrders, ["HVAC Exterior Unit"])) {
    drafts.push({
      title: "Post-storm exterior HVAC unit inspection",
      category: "HVAC Exterior Unit",
      priority,
      reason: "HVAC age or prior exterior HVAC work orders indicate potential equipment exposure.",
      recommendedTiming: timing,
      requiresUserConfirmation: true,
    });
  }

  if (property.treeCanopyRisk === "High" || hasCategory(workOrders, ["Tree Limb Removal"])) {
    drafts.push({
      title: "Tree limb and debris inspection",
      category: "Tree and Debris",
      priority,
      reason: "Tree canopy risk or prior tree limb work orders indicate debris exposure.",
      recommendedTiming: timing,
      requiresUserConfirmation: true,
    });
  }

  if (drafts.length === 0) {
    drafts.push({
      title: "Post-storm exterior safety inspection",
      category: "Exterior Envelope",
      priority,
      reason: "Property is in an affected county at the selected storm timeline point.",
      recommendedTiming: timing,
      requiresUserConfirmation: true,
    });
  }

  return drafts.slice(0, 3);
}

function mapCategoryToServiceType(category) {
  const mapping = {
    "Roof Inspection": "Roofing",
    "Exterior Envelope": "Exterior Envelope",
    "HVAC Exterior Unit": "HVAC",
    "Tree and Debris": "Tree and Debris",
    "Water Intrusion": "Exterior Envelope",
  };
  return mapping[category] || category;
}

function buildContractorRecommendations(property, draftWorkOrders, contractors) {
  const neededServiceTypes = [...new Set(draftWorkOrders.map((draft) => mapCategoryToServiceType(draft.category)))];
  const recommendations = [];

  for (const serviceType of neededServiceTypes) {
    const match = contractors
      .filter(
        (contractor) =>
          contractor.serviceType === serviceType &&
          contractor.serviceCounties.includes(property.county)
      )
      .sort((a, b) => a.availableWithinHours - b.availableWithinHours || b.rating - a.rating)[0];

    if (match && !recommendations.some((item) => item.contractorId === match.contractorId)) {
      recommendations.push({
        contractorId: match.contractorId,
        name: match.name,
        serviceType: match.serviceType,
        reason: `Serves ${property.county} County and matches ${serviceType} response needs.`,
        availableWithinHours: match.availableWithinHours,
      });
    }
  }

  return recommendations.slice(0, 3);
}

function estimateRepairExposure(riskLevel, draftWorkOrders, capexItems) {
  const baseByLevel = {
    Critical: 45000,
    High: 25000,
    Medium: 12000,
    Low: 3000,
  };
  const base = baseByLevel[riskLevel] || 0;
  const capexExposure = Math.min(
    capexItems.reduce((sum, item) => sum + (item.estimatedCost || 0), 0) * 0.15,
    30000
  );
  const workOrderExposure = draftWorkOrders.length * 3000;

  return Math.round((base + capexExposure + workOrderExposure) / 1000) * 1000;
}

function buildLlmContext(property, countyWeather, workOrders, capexItems, draftWorkOrders, repairExposure) {
  const allowedClaims = [
    `The property is in ${property.county} County during a ${countyWeather.riskLevel} tornado risk timeline point.`,
    `The county weather event includes ${countyWeather.windSpeedMph} mph wind speed and ${countyWeather.tornadoProbability} tornado probability.`,
    `The property roof age is ${property.roofAgeYears} years.`,
    `The property exterior condition is ${property.exteriorCondition}.`,
    "Any AI-generated work order requires user confirmation.",
  ];

  if (workOrders.length > 0) {
    allowedClaims.push(`Historical work orders include ${formatCategorySummary(workOrders)}.`);
  }

  if (capexItems.length > 0) {
    allowedClaims.push(`CapEx plan includes ${capexItems.map((item) => item.item).join(", ")}.`);
  }

  return {
    summaryInput: `${property.name} is in ${property.county} County during ${countyWeather.riskLevel} tornado risk. County weather shows ${countyWeather.windSpeedMph} mph wind speed and ${countyWeather.tornadoProbability} tornado probability. The property has a ${property.roofAgeYears}-year roof, ${property.exteriorCondition} exterior condition, and ${workOrders.length} recent work orders. Estimated repair exposure is ${repairExposure}.`,
    allowedClaims,
    disallowedClaims: [
      "Do not claim confirmed physical damage.",
      "Do not claim residents have been injured.",
      "Do not claim evacuation is mandatory.",
      "Do not claim insurance coverage or reimbursement.",
    ],
    dataLimitations: [
      "No live radar feed is connected in this mock dataset.",
      "No confirmed post-storm inspection results are available yet.",
      "Repair exposure is an estimate for demo purposes.",
    ],
    recommendedPromptFiles: [
      "llm_prompts/property_risk_explanation_prompt.md",
      "llm_prompts/tenant_notification_prompt.md",
      "llm_prompts/work_order_draft_prompt.md",
      "llm_prompts/contractor_recommendation_prompt.md",
    ],
  };
}

function buildPropertyResult(property, countyWeather, workOrders, capexItems, leaseExposure, contractors) {
  const scoreBreakdown = {
    weatherRisk: calculateWeatherRisk(countyWeather),
    assetVulnerability: calculateAssetVulnerability(property),
    maintenanceRisk: calculateMaintenanceRisk(workOrders),
    businessImpact: calculateBusinessImpact(property, leaseExposure),
    weights: WEIGHTS,
    formula:
      "round(weatherRisk * 0.40 + assetVulnerability * 0.25 + maintenanceRisk * 0.25 + businessImpact * 0.10)",
  };

  const riskScore = calculateRiskScore(scoreBreakdown);
  const riskLevel = riskLevelFromScore(riskScore);
  const recommendedDraftWorkOrders = buildDraftWorkOrders(property, workOrders, capexItems, riskLevel);
  const recommendedContractors = buildContractorRecommendations(property, recommendedDraftWorkOrders, contractors);
  const estimatedRepairExposure = estimateRepairExposure(riskLevel, recommendedDraftWorkOrders, capexItems);

  return {
    propertyId: property.propertyId,
    name: property.name,
    market: property.market,
    city: property.city,
    county: property.county,
    lat: property.lat,
    lng: property.lng,
    assetType: property.assetType,
    units: property.units,
    isAffected: true,
    riskScore,
    riskLevel,
    estimatedRepairExposure,
    scoreBreakdown,
    riskDrivers: buildRiskDrivers(property, countyWeather, workOrders, capexItems, leaseExposure),
    recommendedActions: buildRecommendedActions(riskLevel),
    recommendedDraftWorkOrders,
    recommendedContractors,
    llmContext: buildLlmContext(
      property,
      countyWeather,
      workOrders,
      capexItems,
      recommendedDraftWorkOrders,
      estimatedRepairExposure
    ),
  };
}

function summarizePortfolio(allProperties, affectedResults, leaseExposureByProperty) {
  const countByLevel = affectedResults.reduce((acc, result) => {
    acc[result.riskLevel] = (acc[result.riskLevel] || 0) + 1;
    return acc;
  }, {});

  const marketScores = affectedResults.reduce((acc, result) => {
    if (!acc[result.market]) {
      acc[result.market] = { market: result.market, count: 0, totalScore: 0 };
    }
    acc[result.market].count += 1;
    acc[result.market].totalScore += result.riskScore;
    return acc;
  }, {});

  const topAffectedMarkets = Object.values(marketScores)
    .sort((a, b) => b.totalScore - a.totalScore || b.count - a.count)
    .slice(0, 3)
    .map((item) => item.market);

  const residentExposure = affectedResults.reduce((sum, result) => {
    return sum + (leaseExposureByProperty.get(result.propertyId)?.atRiskResidentCount || 0);
  }, 0);

  return {
    totalProperties: allProperties.length,
    affectedProperties: affectedResults.length,
    criticalRiskProperties: countByLevel.Critical || 0,
    highRiskProperties: countByLevel.High || 0,
    mediumRiskProperties: countByLevel.Medium || 0,
    lowRiskProperties: countByLevel.Low || 0,
    estimatedRepairExposure: affectedResults.reduce((sum, result) => sum + result.estimatedRepairExposure, 0),
    residentExposure,
    topAffectedMarkets,
  };
}

function buildExecutiveContext(weatherEvent, timelinePoint, portfolioSummary, propertyResults) {
  const topProperties = [...propertyResults]
    .sort((a, b) => b.riskScore - a.riskScore)
    .slice(0, 3)
    .map((property) => `${property.name} (${property.riskLevel}, ${property.riskScore})`);

  const workOrderThemes = [
    ...new Set(propertyResults.flatMap((property) => property.recommendedDraftWorkOrders.map((draft) => draft.category))),
  ];

  return {
    weatherEvent: weatherEvent.name,
    analysisTime: timelinePoint.time,
    scenarioStage: timelinePoint.stage,
    affectedProperties: portfolioSummary.affectedProperties,
    criticalRiskProperties: portfolioSummary.criticalRiskProperties,
    highRiskProperties: portfolioSummary.highRiskProperties,
    estimatedRepairExposure: portfolioSummary.estimatedRepairExposure,
    residentExposure: portfolioSummary.residentExposure,
    topConcerns: [
      `${portfolioSummary.topAffectedMarkets.join(", ")} are the main affected markets at this timeline point.`,
      `Highest-risk properties: ${topProperties.join("; ")}.`,
      `${workOrderThemes.join(", ")} are the main post-storm work order themes.`,
    ],
    recommendedNextActions: [
      "Send resident notifications for affected properties.",
      "Pre-book contractors for Critical and High risk assets.",
      "Prepare draft inspection work orders for user confirmation after storm clearance.",
    ],
  };
}

function analyzeRisk() {
  const weatherData = readJson("weather_events.json");
  const propertiesData = readJson("properties.json");
  const workOrdersData = readJson("work_orders.json");
  const capexData = readJson("capex_plan.json");
  const leaseData = readJson("lease_exposure.json");
  const contractorsData = readJson("contractors.json");

  const timelinePoint = weatherData.timeline.find((point) => point.time === ANALYSIS_TIME);
  if (!timelinePoint) {
    throw new Error(`No weather timeline point found for ${ANALYSIS_TIME}`);
  }

  const weatherByCounty = new Map(
    timelinePoint.affectedCounties.map((countyWeather) => [countyWeather.county, countyWeather])
  );
  const workOrdersByProperty = groupBy(workOrdersData.workOrders, "propertyId");
  const capexByProperty = groupBy(capexData.capexItems, "propertyId");
  const leaseExposureByProperty = new Map(
    leaseData.leaseExposure.map((leaseExposure) => [leaseExposure.propertyId, leaseExposure])
  );

  const affectedResults = propertiesData.properties
    .filter((property) => weatherByCounty.has(property.county))
    .map((property) =>
      buildPropertyResult(
        property,
        weatherByCounty.get(property.county),
        workOrdersByProperty.get(property.propertyId) || [],
        capexByProperty.get(property.propertyId) || [],
        leaseExposureByProperty.get(property.propertyId),
        contractorsData.contractors
      )
    )
    .sort((a, b) => b.riskScore - a.riskScore || a.propertyId.localeCompare(b.propertyId));

  const portfolioSummary = summarizePortfolio(
    propertiesData.properties,
    affectedResults,
    leaseExposureByProperty
  );

  return {
    eventId: weatherData.event.eventId,
    analysisTime: timelinePoint.time,
    scenarioStage: timelinePoint.stage,
    portfolioSummary,
    properties: affectedResults,
    llmInputs: {
      executiveSummaryContext: buildExecutiveContext(
        weatherData.event,
        timelinePoint,
        portfolioSummary,
        affectedResults
      ),
      recommendedPromptFiles: [
        "llm_prompts/executive_summary_prompt.md",
        "llm_prompts/vp_email_prompt.md",
      ],
    },
    dataSources: [
      "mock_data/weather_events.json",
      "mock_data/properties.json",
      "mock_data/work_orders.json",
      "mock_data/capex_plan.json",
      "mock_data/lease_exposure.json",
      "mock_data/contractors.json",
    ],
    metadata: {
      dataType: "synthetic_demo_data",
      engineVersion: "0.1",
      generatedFor: "hackathon_demo",
      importantNote:
        "This output is for demo purposes. It is not official weather, emergency, insurance, or property condition data.",
    },
  };
}

function groupBy(items, key) {
  const grouped = new Map();
  for (const item of items) {
    const value = item[key];
    if (!grouped.has(value)) grouped.set(value, []);
    grouped.get(value).push(item);
  }
  return grouped;
}

function main() {
  const result = analyzeRisk();
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  fs.writeFileSync(OUTPUT_FILE, `${JSON.stringify(result, null, 2)}\n`, "utf8");
  console.log(`Risk analysis written to ${path.relative(PROJECT_ROOT, OUTPUT_FILE)}`);
}

if (require.main === module) {
  main();
}

module.exports = {
  analyzeRisk,
  calculateWeatherRisk,
  calculateAssetVulnerability,
  calculateMaintenanceRisk,
  calculateBusinessImpact,
  calculateRiskScore,
  riskLevelFromScore,
};

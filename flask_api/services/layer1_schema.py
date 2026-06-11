"""Layer 1 Portfolio Intelligence output contract.

This module defines the shape of a single Layer 1 metric result so that other
layers (the AI Copilot context builder, the frontend, and future Phase B
calculations) can rely on a stable, inspectable structure.

The contract is intentionally plain Python (no third-party schema library) so it
stays importable with stdlib only and easy to validate in tests.

Layer 1 result fields (per property, per metric):
    propertyId        str   - the property this result describes
    metric            str   - the metric name, e.g. "assetHealthScore"
    score             int   - 0-100 integer score
    band              str   - qualitative band derived from score
    drivers           list  - short strings explaining what hurt/helped the score
    confidence        str   - "High" | "Medium" | "Low"
    dataQualityNotes  list  - strings describing missing/sparse data; [] if complete
"""

# ---------------------------------------------------------------------------
# Allowed enumerations
# ---------------------------------------------------------------------------

ASSET_HEALTH_METRIC = "assetHealthScore"

# Score bands for assetHealthScore (from design doc section 6).
# Each entry is (inclusive_min_score, band_label); evaluated high to low.
ASSET_HEALTH_BANDS = (
    (80, "Strong"),
    (60, "Stable"),
    (40, "Concerning"),
    (0, "Poor"),
)

VALID_BANDS = tuple(label for _, label in ASSET_HEALTH_BANDS)

CONFIDENCE_LEVELS = ("High", "Medium", "Low")

# Required keys every Layer 1 result must expose, mapped to their expected type.
REQUIRED_FIELDS = {
    "propertyId": str,
    "metric": str,
    "score": int,
    "band": str,
    "drivers": list,
    "confidence": str,
    "dataQualityNotes": list,
}


def band_for_score(score):
    """Return the qualitative band label for a 0-100 score."""
    for minimum, label in ASSET_HEALTH_BANDS:
        if score >= minimum:
            return label
    # Scores are clamped to >= 0 upstream, so this is a defensive fallback.
    return "Poor"


def make_layer1_result(
    property_id,
    metric,
    score,
    drivers=None,
    confidence="High",
    data_quality_notes=None,
):
    """Build a Layer 1 result dict that conforms to the contract.

    Centralizing construction here guarantees band is always consistent with
    score and that every result carries all required keys with correct types.
    """
    score = int(score)
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}, got {confidence!r}")

    return {
        "propertyId": str(property_id),
        "metric": str(metric),
        "score": score,
        "band": band_for_score(score),
        "drivers": list(drivers or []),
        "confidence": confidence,
        "dataQualityNotes": list(data_quality_notes or []),
    }


def validate_layer1_result(result):
    """Validate a single Layer 1 result against the contract.

    Returns a list of human-readable problems; an empty list means valid.
    Kept as a pure function so tests and callers can assert on it directly.
    """
    problems = []

    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in result:
            problems.append(f"missing required field: {field}")
            continue
        if not isinstance(result[field], expected_type):
            problems.append(
                f"field {field} should be {expected_type.__name__}, "
                f"got {type(result[field]).__name__}"
            )

    # Note: bool is a subclass of int in Python; reject it for the score field.
    if isinstance(result.get("score"), bool):
        problems.append("field score should be int, got bool")

    score = result.get("score")
    if isinstance(score, int) and not isinstance(score, bool):
        if not (0 <= score <= 100):
            problems.append(f"score out of range 0-100: {score}")
        if result.get("band") != band_for_score(score):
            problems.append(
                f"band {result.get('band')!r} does not match score {score} "
                f"(expected {band_for_score(score)!r})"
            )

    if result.get("band") not in VALID_BANDS:
        problems.append(f"band {result.get('band')!r} not in {VALID_BANDS}")

    if result.get("confidence") not in CONFIDENCE_LEVELS:
        problems.append(f"confidence {result.get('confidence')!r} not in {CONFIDENCE_LEVELS}")

    for note in result.get("dataQualityNotes", []) or []:
        if not isinstance(note, str):
            problems.append("dataQualityNotes must contain only strings")
            break

    for driver in result.get("drivers", []) or []:
        if not isinstance(driver, str):
            problems.append("drivers must contain only strings")
            break

    return problems

"""Analysis scope resolution for the Layer 1 calculation engines.

The calculation engines must not silently process "all historical data
forever". Instead, every Layer 1 run happens inside an explicit analysis
scope:

    {
        "portfolioId": "FL-DEMO",
        "analysisYear": 2026,
        "stormEventId": "TOR-FL-2026-0612"
    }

This module resolves caller-supplied scope inputs (query parameters or the AI
Copilot ``scenario`` object) into a fully-populated scope, and provides the
shared selection helpers the engines use:

  * work orders   -> rolling lookback window ending at the analysis year
  * storm event   -> selected by stormEventId, falling back to the demo storm
  * valuations    -> records valid for (dated no later than) the analysis year

Everything here is deterministic and read-only; gaps are reported as notes,
never guessed. Defaults are chosen so that a scope-less call selects exactly
the data the engines used before scoping existed (the demo dataset).
"""

from datetime import date

from services.data_loader import load_json


DEFAULT_PORTFOLIO_ID = "FL-DEMO"
DEFAULT_ANALYSIS_YEAR = 2026

# The demo dataset is a single portfolio; anything else cannot be selected.
KNOWN_PORTFOLIO_IDS = frozenset({DEFAULT_PORTFOLIO_ID})

# Rolling work-order lookback ending at the analysis year. 24 months keeps the
# full demo history (2025-11 .. 2026-05) in scope for the default 2026 year
# while still excluding genuinely stale records for earlier analysis years.
WORK_ORDER_LOOKBACK_MONTHS = 24


def resolve_analysis_scope(portfolio_id=None, analysis_year=None, storm_event_id=None):
    """Resolve caller inputs into a full analysis scope dict.

    Raises ValueError when analysisYear is present but not a usable year.
    Unknown portfolio/storm ids do NOT raise: the demo dataset is served with
    an explanatory note instead (see scope["notes"]).
    """
    notes = []

    if portfolio_id is None or str(portfolio_id).strip() == "":
        portfolio_id = DEFAULT_PORTFOLIO_ID
    else:
        portfolio_id = str(portfolio_id).strip()
        if portfolio_id not in KNOWN_PORTFOLIO_IDS:
            notes.append(
                f"Unknown portfolioId '{portfolio_id}'; demo dataset only contains "
                f"'{DEFAULT_PORTFOLIO_ID}', serving that portfolio instead"
            )

    if analysis_year is None or str(analysis_year).strip() == "":
        analysis_year = DEFAULT_ANALYSIS_YEAR
    else:
        try:
            analysis_year = int(analysis_year)
        except (TypeError, ValueError):
            raise ValueError(f"analysisYear must be an integer year, got {analysis_year!r}")
        if not 1900 <= analysis_year <= 2100:
            raise ValueError(f"analysisYear {analysis_year} is outside the supported range 1900-2100")

    if storm_event_id is not None:
        storm_event_id = str(storm_event_id).strip() or None

    return {
        "portfolioId": portfolio_id,
        "analysisYear": analysis_year,
        "stormEventId": storm_event_id,  # None = use the current demo storm
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Storm event selection
# ---------------------------------------------------------------------------

def select_storm_event(scope, storm_data=None):
    """Select the storm event for the scope.

    Args:
        scope: resolved scope dict from resolve_analysis_scope.
        storm_data: optional pre-loaded storm_path.json dict (used by callers
            that read from a custom data_dir). When None, the default
            mock_data/storm_path.json is loaded.

    Returns (path_points, storm_meta, notes). When the requested stormEventId
    does not match any known event, the current demo storm is used and a note
    explains the fallback. Missing storm data degrades to ([], {}).
    """
    notes = []
    if storm_data is None:
        try:
            storm_data = load_json("storm_path.json")
        except (FileNotFoundError, ValueError):
            return [], {}, ["Storm path data unavailable; storm impact not computable"]

    available_id = storm_data.get("stormEventId")
    requested_id = scope.get("stormEventId")
    if requested_id and requested_id != available_id:
        notes.append(
            f"Requested stormEventId '{requested_id}' not found; falling back to "
            f"current demo storm '{available_id}'"
        )

    points = [
        (p["latitude"], p["longitude"])
        for p in storm_data.get("projectedPath", [])
        if p.get("latitude") is not None and p.get("longitude") is not None
    ]
    return points, storm_data, notes


# ---------------------------------------------------------------------------
# Work-order window
# ---------------------------------------------------------------------------

def _months_back(day, months):
    """Return ``day`` shifted back by ``months``, clamping the day-of-month."""
    total = day.year * 12 + (day.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    clamped_day = min(day.day, _days_in_month(year, month))
    return date(year, month, clamped_day)


def _days_in_month(year, month):
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def work_order_window(scope):
    """Rolling lookback window for in-scope work orders, as ISO date strings.

    The window ends on the last day of the analysis year and reaches back
    WORK_ORDER_LOOKBACK_MONTHS. Work orders outside it are stale (or from the
    future relative to the analysis year) and are excluded from scoring.
    """
    end = date(scope["analysisYear"], 12, 31)
    start = _months_back(end, WORK_ORDER_LOOKBACK_MONTHS)
    return start.isoformat(), end.isoformat()


def filter_work_orders(work_orders, scope):
    """Split work orders into (in_scope, excluded) for the scope's window.

    Comparison uses ISO date strings (lexicographic == chronological).
    Undated work orders stay in scope; they cannot be aged out reliably.
    """
    start, end = work_order_window(scope)
    in_scope, excluded = [], []
    for wo in work_orders:
        created = wo.get("createdDate")
        if created is None or start <= created <= end:
            in_scope.append(wo)
        else:
            excluded.append(wo)
    return in_scope, excluded


# ---------------------------------------------------------------------------
# Valuation validity
# ---------------------------------------------------------------------------

def filter_valuations(valuations, scope):
    """Split valuations into (valid, excluded) for the analysis year.

    A valuation is valid when its lastValuationDate is no later than the end
    of the analysis year (an older valuation remains the best available
    record). Valuations dated after the analysis year did not exist yet in
    that scope and are excluded. Undated valuations are kept, since excluding
    them would silently drop the only available record.
    """
    end_of_year = f"{scope['analysisYear']}-12-31"
    valid, excluded = [], []
    for valuation in valuations:
        valued_on = valuation.get("lastValuationDate")
        if valued_on is None or valued_on <= end_of_year:
            valid.append(valuation)
        else:
            excluded.append(valuation)
    return valid, excluded


# ---------------------------------------------------------------------------
# Insurance policy applicability
# ---------------------------------------------------------------------------

def filter_policies(policies, scope):
    """Split insurance policies into (in_force, excluded) for the analysis year.

    A policy is in force when its [policyStartDate, policyEndDate] period
    overlaps the analysis year at all. Policies entirely outside the year did
    not apply in that scope and are excluded. Policies missing either date are
    kept (the only available record should degrade confidence, not vanish).
    """
    year_start = f"{scope['analysisYear']}-01-01"
    year_end = f"{scope['analysisYear']}-12-31"
    in_force, excluded = [], []
    for policy in policies:
        start = policy.get("policyStartDate")
        end = policy.get("policyEndDate")
        if start is None or end is None:
            in_force.append(policy)
        elif start <= year_end and end >= year_start:
            in_force.append(policy)
        else:
            excluded.append(policy)
    return in_force, excluded


__all__ = [
    "DEFAULT_ANALYSIS_YEAR",
    "DEFAULT_PORTFOLIO_ID",
    "WORK_ORDER_LOOKBACK_MONTHS",
    "filter_policies",
    "filter_valuations",
    "filter_work_orders",
    "resolve_analysis_scope",
    "select_storm_event",
    "work_order_window",
]

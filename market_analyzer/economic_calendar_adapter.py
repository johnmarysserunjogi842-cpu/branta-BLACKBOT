"""
Institution-Grade Economic Calendar Adapter (Internal Infrastructure)

Role:
- Convert RAW provider events into canonical NewsEvent objects
- Enforce institutional trading rules
- Deterministic, UTC-safe, backtest-safe

Design Principles:
- PURE function (no I/O, no globals mutated)
- Deterministic under backtesting
- Fail-closed (unknown = drop)
- O(n) linear scan, hot-path optimized
- Zero side effects

IMPORTANT:
- This module is INTERNAL infrastructure
- Must ONLY be called by NewsFilter (via a fetcher)
- Never called directly by Orchestrator or CLI
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
import logging

from market_analyzer.calendar_common import NewsEvent

# ==================================================
# LOGGING (INHERITS ROOT CONFIG)
# ==================================================

logger = logging.getLogger(__name__)

# ==================================================
# CONSTANTS (IMMUTABLE)
# ==================================================

UTC = timezone.utc

# ForexFactory publishes already-UTC-like timestamps in practice,
# but offset kept explicit for future-proofing.
FF_TIMEZONE_OFFSET = timedelta(hours=0)

HIGH_IMPACT = "HIGH"

# ==================================================
# EVENT CLASSIFICATION TABLE (PRECOMPUTED)
# ==================================================
# Institution rule:
# - First match wins
# - Unknown category = DROP
# - Flat dict for O(1) iteration, no regex, no allocations

_KEYWORD_TO_CATEGORY: dict[str, str] = {
    # LABOR / EMPLOYMENT
    "non-farm": "NFP",
    "employment change": "NFP",
    "unemployment rate": "NFP",
    "jobless": "NFP",

    # INFLATION
    "cpi": "CPI",
    "consumer price": "CPI",
    "inflation": "CPI",

    # CONSUMPTION
    "pce": "PCE",
    "personal consumption": "PCE",

    # GROWTH
    "gdp": "GDP",
    "gross domestic": "GDP",

    # RATES / POLICY
    "interest rate": "RATES",
    "rate decision": "RATES",
    "policy rate": "RATES",

    # CENTRAL BANKS
    "fomc": "CENTRAL_BANK",
    "ecb": "CENTRAL_BANK",
    "boe": "CENTRAL_BANK",
    "boj": "CENTRAL_BANK",
    "central bank": "CENTRAL_BANK",
    "monetary policy": "CENTRAL_BANK",
}

# ==================================================
# PUBLIC API (STABLE CONTRACT)
# ==================================================

def adapt_forexfactory_events(
    raw_events: List[Dict],
    *,
    now_utc: Optional[datetime] = None,
) -> List[NewsEvent]:
    """
    Institutional adapter.

    Guarantees:
    - NewsEvent schema unchanged
    - UTC-aware timestamps
    - HIGH-impact events only
    - Canonical macro category
    - Deterministic behavior under backtesting
    - Safe numeric coercion

    Parameters:
        raw_events: list of RAW provider dicts
        now_utc: explicit UTC time (REQUIRED for backtests)

    Returns:
        List[NewsEvent]
    """

    if not raw_events:
        return []

    # Deterministic clock
    now = now_utc or datetime.now(UTC)

    out: List[NewsEvent] = []

    for raw in raw_events:
        try:
            # ---------------------------------
            # HARD FILTERS (FAST FAIL)
            # ---------------------------------
            if raw.get("impact") != HIGH_IMPACT:
                continue

            event_id = raw.get("event_id")
            currency = raw.get("currency")
            title = raw.get("event")

            if not event_id or not currency or not title:
                continue

            # ---------------------------------
            # CATEGORY CLASSIFICATION
            # ---------------------------------
            category = _classify_event(title)
            if category is None:
                continue  # institution rule: unknown = non-tradable

            # ---------------------------------
            # TIMESTAMP NORMALIZATION
            # ---------------------------------
            timestamp = _build_timestamp(
                raw.get("date"),
                raw.get("time"),
                now,
            )
            if timestamp is None:
                continue

            # ---------------------------------
            # EMIT CANONICAL EVENT
            # ---------------------------------
            out.append(
                NewsEvent(
                    event_id=event_id,
                    currency=currency,
                    title=title,
                    impact=HIGH_IMPACT,
                    category=category,
                    actual=_to_float(raw.get("actual")),
                    forecast=_to_float(raw.get("forecast")),
                    timestamp=timestamp,
                )
            )

        except Exception as exc:
            # Adapter must NEVER crash the system
            logger.warning("Economic adapter dropped event: %s", exc)
            continue

    return out

# ==================================================
# INTERNALS (HOT PATH SAFE)
# ==================================================

def _classify_event(title: str) -> Optional[str]:
    """
    Deterministic keyword classification.
    No regex. No allocations beyond .lower().
    """
    t = title.lower()
    for keyword, category in _KEYWORD_TO_CATEGORY.items():
        if keyword in t:
            return category
    return None


def _build_timestamp(
    date_str: Optional[str],
    time_str: Optional[str],
    now_utc: datetime,
) -> Optional[datetime]:
    """
    Build UTC-aware timestamp.

    Rules:
    - If date/time missing → use now_utc (deterministic fallback)
    - If parsing fails → drop event
    """
    if not date_str or not time_str:
        return now_utc

    try:
        dt = datetime.strptime(
            f"{date_str} {time_str}",
            "%Y-%m-%d %H:%M",
        )
        return dt.replace(tzinfo=UTC) - FF_TIMEZONE_OFFSET
    except Exception:
        return None


def _to_float(value) -> Optional[float]:
    """
    Fast numeric coercion.

    Accepted:
    - "1.2"
    - "1,200"
    - "2.5%"
    - None

    Rejected:
    - Garbage → None
    """
    if value is None:
        return None

    try:
        return float(
            str(value)
            .replace(",", "")
            .replace("%", "")
            .strip()
        )
    except Exception:
        return None

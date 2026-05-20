"""
market_analyzer/calendar_common.py

Institutional Economic Calendar Shared Domain Layer
===================================================

Purpose
-------
Centralized shared contracts and utilities for:

- economic calendar adapters
- news filtering
- event normalization
- impact classification
- provider interoperability
- validation
- canonical event schemas

This module exists specifically to eliminate circular dependencies
between:

    economic_calendar_adapter.py
    news_filter.py

Design Goals
------------
- zero side effects
- import-safe
- async-safe
- PyInstaller-safe
- deterministic
- low allocation
- scalable
- strongly typed
- future-proof
- institutional-grade

IMPORTANT
---------
This module MUST NOT contain:

- Telegram logic
- websocket ownership
- asyncio task orchestration
- broker logic
- UI logic
- strategy execution
- network ownership

This is a PURE shared domain layer.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    Dict,
    Final,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

# ============================================================
# CONSTANTS
# ============================================================

HIGH_IMPACT: Final[str] = "high"
MEDIUM_IMPACT: Final[str] = "medium"
LOW_IMPACT: Final[str] = "low"

VALID_IMPACTS: Final[Set[str]] = {
    HIGH_IMPACT,
    MEDIUM_IMPACT,
    LOW_IMPACT,
}

MAJOR_CURRENCIES: Final[Set[str]] = {
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "AUD",
    "NZD",
    "CAD",
    "CHF",
    "CNY",
}

MAJOR_CATEGORIES: Final[Set[str]] = {
    "interest_rate",
    "inflation",
    "employment",
    "gdp",
    "manufacturing",
    "central_bank",
    "consumer_sentiment",
    "retail_sales",
    "trade_balance",
    "housing",
    "services",
    "bond_auction",
    "speech",
}

# ============================================================
# CATEGORY ENUMS
# ============================================================


class EventImpact(str, Enum):
    HIGH = HIGH_IMPACT
    MEDIUM = MEDIUM_IMPACT
    LOW = LOW_IMPACT


class EventCategory(str, Enum):
    INTEREST_RATE = "interest_rate"
    INFLATION = "inflation"
    EMPLOYMENT = "employment"
    GDP = "gdp"
    MANUFACTURING = "manufacturing"
    CENTRAL_BANK = "central_bank"
    CONSUMER_SENTIMENT = "consumer_sentiment"
    RETAIL_SALES = "retail_sales"
    TRADE_BALANCE = "trade_balance"
    HOUSING = "housing"
    SERVICES = "services"
    BOND_AUCTION = "bond_auction"
    SPEECH = "speech"
    OTHER = "other"


class DecisionBias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


# ============================================================
# REGEX CACHE
# ============================================================

_NUMBER_CLEAN_RE: Final[re.Pattern[str]] = re.compile(
    r"[^\d\.\-]"
)

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")

# ============================================================
# DATACLASSES
# ============================================================


@dataclass(slots=True)
class NewsEvent:
    """
    Canonical economic event schema.

    All providers/adapters MUST normalize into this structure.
    """

    title: str
    currency: str
    impact: str

    timestamp: datetime

    category: str = EventCategory.OTHER.value

    actual: Optional[float] = None
    forecast: Optional[float] = None
    previous: Optional[float] = None

    source: str = "unknown"

    event_id: Optional[str] = None

    raw_payload: Optional[Dict[str, Any]] = None

    tags: Set[str] = field(default_factory=set)

    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_high_impact(self) -> bool:
        return self.impact == HIGH_IMPACT

    def is_major_currency(self) -> bool:
        return self.currency in MAJOR_CURRENCIES

    def snapshot(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "currency": self.currency,
            "impact": self.impact,
            "timestamp": self.timestamp.isoformat(),
            "category": self.category,
            "actual": self.actual,
            "forecast": self.forecast,
            "previous": self.previous,
            "source": self.source,
            "event_id": self.event_id,
            "tags": sorted(self.tags),
        }


@dataclass(slots=True)
class NewsDecision:
    """
    Canonical filtering/risk decision.

    Produced by:
        news_filter.py
    """

    approved: bool

    bias: str = DecisionBias.UNKNOWN.value

    confidence: float = 0.0

    reason: str = ""

    affected_symbols: List[str] = field(default_factory=list)

    risk_score: float = 0.0

    volatility_expected: bool = False

    metadata: Dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "bias": self.bias,
            "confidence": self.confidence,
            "reason": self.reason,
            "affected_symbols": self.affected_symbols,
            "risk_score": self.risk_score,
            "volatility_expected": self.volatility_expected,
        }


# ============================================================
# NORMALIZATION UTILITIES
# ============================================================


def normalize_whitespace(value: str) -> str:
    """
    Normalize repeated whitespace safely.
    """

    return _WHITESPACE_RE.sub(" ", value).strip()


def normalize_currency(currency: str) -> str:
    """
    Normalize currency codes safely.

    Example:
        usd -> USD
    """

    return normalize_whitespace(currency).upper()


def normalize_impact(value: str) -> str:
    """
    Normalize impact labels.

    Defaults safely to LOW.
    """

    normalized = normalize_whitespace(value).lower()

    if normalized not in VALID_IMPACTS:
        return LOW_IMPACT

    return normalized


def normalize_category(category: str) -> str:
    """
    Normalize event category safely.
    """

    normalized = normalize_whitespace(category).lower()

    if normalized in MAJOR_CATEGORIES:
        return normalized

    return EventCategory.OTHER.value


# ============================================================
# SAFE NUMERIC PARSING
# ============================================================


def safe_float(value: Any) -> Optional[float]:
    """
    Safe institutional float parsing.

    Handles:
    - commas
    - percentages
    - empty values
    - malformed provider data
    """

    if value is None:
        return None

    if isinstance(value, (float, int)):
        if math.isnan(value):
            return None

        return float(value)

    if not isinstance(value, str):
        return None

    cleaned = _NUMBER_CLEAN_RE.sub("", value.strip())

    if not cleaned:
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None


# ============================================================
# TIMESTAMP UTILITIES
# ============================================================


def utc_now() -> datetime:
    """
    Centralized UTC timestamp generation.
    """

    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """
    Ensure timezone-aware UTC datetime.
    """

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


# ============================================================
# VALIDATION
# ============================================================


def validate_event(event: NewsEvent) -> Tuple[bool, Optional[str]]:
    """
    Institutional validation.

    Fast and deterministic.
    """

    if not event.title:
        return False, "missing_title"

    if not event.currency:
        return False, "missing_currency"

    if event.impact not in VALID_IMPACTS:
        return False, "invalid_impact"

    if not isinstance(event.timestamp, datetime):
        return False, "invalid_timestamp"

    return True, None


# ============================================================
# FILTER HELPERS
# ============================================================


def filter_high_impact(
    events: Iterable[NewsEvent],
) -> List[NewsEvent]:
    """
    Return only high-impact events.
    """

    return [
        event
        for event in events
        if event.impact == HIGH_IMPACT
    ]


def filter_major_currency_events(
    events: Iterable[NewsEvent],
) -> List[NewsEvent]:
    """
    Return events affecting major currencies only.
    """

    return [
        event
        for event in events
        if event.currency in MAJOR_CURRENCIES
    ]


# ============================================================
# SYMBOL IMPACT UTILITIES
# ============================================================


def infer_affected_symbols(
    currency: str,
) -> List[str]:
    """
    Infer likely affected forex symbols.

    Lightweight heuristic.
    """

    currency = normalize_currency(currency)

    mapping = {
        "USD": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
        "EUR": ["EURUSD", "EURJPY", "EURGBP"],
        "GBP": ["GBPUSD", "EURGBP", "GBPJPY"],
        "JPY": ["USDJPY", "EURJPY", "GBPJPY"],
        "AUD": ["AUDUSD", "AUDJPY"],
        "CAD": ["USDCAD"],
        "CHF": ["USDCHF"],
        "NZD": ["NZDUSD"],
    }

    return mapping.get(currency, [])


# ============================================================
# BULK VALIDATION
# ============================================================


def validate_events(
    events: Sequence[NewsEvent],
) -> List[NewsEvent]:
    """
    Bulk validation helper.

    Invalid events are discarded safely.
    """

    valid_events: List[NewsEvent] = []

    for event in events:
        valid, _ = validate_event(event)

        if valid:
            valid_events.append(event)

    return valid_events


# ============================================================
# IMMUTABLE EMPTY OBJECTS
# ============================================================

EMPTY_TAGS: Final[Set[str]] = frozenset()

EMPTY_METADATA: Final[Mapping[str, Any]] = {}

# ============================================================
# PUBLIC EXPORTS
# ============================================================

__all__ = [
    # Constants
    "HIGH_IMPACT",
    "MEDIUM_IMPACT",
    "LOW_IMPACT",
    "VALID_IMPACTS",
    "MAJOR_CURRENCIES",
    "MAJOR_CATEGORIES",
    # Enums
    "EventImpact",
    "EventCategory",
    "DecisionBias",
    # Dataclasses
    "NewsEvent",
    "NewsDecision",
    # Utilities
    "normalize_whitespace",
    "normalize_currency",
    "normalize_impact",
    "normalize_category",
    "safe_float",
    "utc_now",
    "ensure_utc",
    "validate_event",
    "validate_events",
    "filter_high_impact",
    "filter_major_currency_events",
    "infer_affected_symbols",
    # Immutable objects
    "EMPTY_TAGS",
    "EMPTY_METADATA",
]

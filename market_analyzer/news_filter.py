#market_analyzer /news_filter.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from collections import defaultdict

# ==================================================


from market_analyzer.calendar_common import (
    NewsEvent,
    NewsDecision,
    EventImpact,
    EventCategory,
    normalize_currency,
    infer_affected_symbols,
)
# ==================================================
# DATA STRUCTURES
# ==================================================

@dataclass(frozen=True)
class NewsEvent:
    event_id: str
    currency: str
    title: str
    impact: str              # "HIGH" only allowed
    category: str            # CPI, NFP, RATES, GDP, CENTRAL_BANK
    actual: Optional[float]
    forecast: Optional[float]
    timestamp: datetime


@dataclass(frozen=True)
class NewsDecision:
    mode: str
    confidence: float
    reason: str
    affected_symbols: Dict[str, Dict]
    expiry_ts: datetime


# ==================================================
# NEWS FILTER (INSTITUTION GRADE)
# ==================================================

class NewsFilter:
    """
    Institutional-Grade News Aggression Controller

    Owns:
    - News availability
    - Lazy fetching
    - Adaptation
    - Internal caching

    Produces:
    - NewsDecision OR None (silent by design)
    """

    # --------------------------------------------------
    # CONFIGURATION
    # --------------------------------------------------

    MAJOR_CATEGORIES = {
        "CPI",
        "PCE",
        "NFP",
        "RATES",
        "GDP",
        "CENTRAL_BANK",
    }

    CONFIDENCE_MIN = 0.45

    DECAY_WINDOWS = {
        "CPI": 90,
        "PCE": 90,
        "NFP": 120,
        "GDP": 60,
        "RATES": 180,
        "CENTRAL_BANK": 240,
    }

    CURRENCY_IMPACT_MAP = {
        "USD": {
            "EURUSD": -0.7,
            "GBPUSD": -0.6,
            "USDJPY": 0.8,
            "XAUUSD": -0.9,
            "NAS100": -0.6,
        },
        "EUR": {
            "EURUSD": 0.8,
            "EURJPY": 0.6,
        },
        "GBP": {
            "GBPUSD": 0.8,
        },
    }

    POST_NEWS_WINDOW = 60
    TREND_CONFIRMATION_THRESHOLD = 0.005

    CACHE_TTL_SECONDS = 60  # in-memory cache only

    # --------------------------------------------------
    # INIT
    # --------------------------------------------------

    def __init__(self, fetcher):
        """
        fetcher must expose:
            get_events(now_utc: datetime) -> List[dict]
        """
        self._fetcher = fetcher
        self._cached_events: List[NewsEvent] = []
        self._last_fetch_ts: Optional[datetime] = None

    # --------------------------------------------------
    # CORE API
    # --------------------------------------------------

    def evaluate(
        self,
        now_utc: datetime,
        market_state: dict,
        cross_asset_state: dict,
        price_data: Dict[str, float],
        active_events: Optional[List[NewsEvent]] = None,
    ) -> Optional[NewsDecision]:

        # ----------------------------------------------
        # LAZY FETCH + ADAPT (INSTITUTION SAFE)
        # ----------------------------------------------

        if active_events is None:
            active_events = self._get_events(now_utc)

        if not active_events:
            return None

        # ----------------------------------------------
        # EXISTING JUDGMENT LOGIC (UNCHANGED)
        # ----------------------------------------------

        event = self._select_relevant_event(now_utc, active_events)

        if event is None:
            return None

        severity = self._severity_score(event)
        if severity < 0.4:
            return None

        surprise = self._surprise_score(event)
        volatility_q = self._volatility_quality(market_state)
        liquidity_q = self._liquidity_quality(market_state)
        alignment = self._cross_asset_alignment(event, cross_asset_state)

        if not self._post_news_trend_confirmation(event, price_data, now_utc):
            return None

        confidence = self._confidence(
            severity,
            surprise,
            volatility_q,
            liquidity_q,
            alignment,
        )

        if confidence < self.CONFIDENCE_MIN:
            return None

        mode = self._resolve_mode(
            severity,
            surprise,
            volatility_q,
            liquidity_q,
            alignment,
        )

        affected = self._affected_symbols(event)

        expiry = now_utc + timedelta(
            minutes=self.DECAY_WINDOWS.get(event.category, 60)
        )

        return NewsDecision(
            mode=mode,
            confidence=round(confidence, 2),
            reason=self._reason(event, mode),
            affected_symbols=affected,
            expiry_ts=expiry,
        )

    # ==================================================
    # FETCH + CACHE LAYER (NEW)
    # ==================================================

    def _get_events(self, now_utc: datetime) -> List[NewsEvent]:
        """
        Cache-first, lazy-fetching, crash-proof.
        """

        if (
            self._cached_events
            and self._last_fetch_ts
            and (now_utc - self._last_fetch_ts).total_seconds() < self.CACHE_TTL_SECONDS
        ):
            return self._cached_events

        try:
            raw = self._fetcher.get_events(now_utc)
            events = adapt_forexfactory_events(raw, now_utc=now_utc)

            if not events:
                return []

            self._cached_events = events
            self._last_fetch_ts = now_utc
            return events

        except Exception:
            # Institutional rule: never break execution
            return []

    # ==================================================
    # INTERNAL LOGIC (UNCHANGED BELOW)
    # ==================================================

    def _select_relevant_event(self, now: datetime, events: List[NewsEvent]) -> Optional[NewsEvent]:
        currency_bucket = {}

        for ev in events:
            if ev.impact != "HIGH":
                continue
            if ev.category not in self.MAJOR_CATEGORIES:
                continue
            if ev.timestamp is None or now < ev.timestamp:
                continue

            age_seconds = (now - ev.timestamp).total_seconds()
            if age_seconds > 300:
                continue

            severity = self._severity_score(ev)
            surprise = self._surprise_score(ev)

            base_score = severity * surprise
            if base_score <= 0:
                continue

            decay = max(0.2, 1 - (age_seconds / 300))
            score = base_score * decay

            cur = ev.currency
            bucket = currency_bucket.setdefault(
                cur,
                {"total": 0.0, "strongest_event": ev, "strongest_score": 0.0},
            )

            bucket["total"] += score
            if score > bucket["strongest_score"]:
                bucket["strongest_score"] = score
                bucket["strongest_event"] = ev

        if not currency_bucket:
            return None

        return max(currency_bucket.values(), key=lambda x: x["total"])["strongest_event"]

    # ---- everything below here is unchanged ----
    # --------------------------------------------------  

    def _severity_score(self, event: NewsEvent) -> float:  
        base = {  
            "RATES": 1.0,  
            "CENTRAL_BANK": 0.95,  
            "CPI": 0.85,  
            "PCE": 0.8,  
            "NFP": 0.85,  
            "GDP": 0.65,  
        }.get(event.category, 0.0)  

        return base  

    # --------------------------------------------------  

    def _surprise_score(self, event) -> float:
        """
        Measures expectation violation, not numeric magnitude.
        """

        # Binary reaction instruments
        if event.category in {"RATES", "CENTRAL_BANK"}:
            return 1.0 if event.actual != event.forecast else 0.2

        # Numeric releases
        if event.actual is None or event.forecast is None:
            return 0.0

        try:
            diff = abs(event.actual - event.forecast)
            return min(diff / max(abs(event.forecast), 1e-6), 1.0)
        except Exception:
            return 0.0

    # --------------------------------------------------  

    def _liquidity_quality(self, market_state: dict) -> float:
        """
        Estimate market liquidity quality.
        1.0 → highly liquid
        <1.0 → less liquid / riskier
        """
        spread = market_state.get("spread_multiplier", 1.0)

        if spread > 2.0:
           return 0.3  # very poor liquidity
        if spread > 1.4:
           return 0.5  # suboptimal
        return 0.8      # good liquidity

    # --------------------------------------------------  

    

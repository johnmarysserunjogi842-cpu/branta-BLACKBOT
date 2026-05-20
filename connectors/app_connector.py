from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Any, Optional, List, Mapping, Iterable
from dataclasses import dataclass

from market_analyzer.market_state_cache import MarketStateCache
from performance.metrics import MetricsTracker
from connectors.feedback import FeedbackEngine
from connectors.account_connector import AccountConnector

logger = logging.getLogger("app_connector")
logger.setLevel(logging.INFO)


# ============================================================
# BOT STATE
# ============================================================

class BotState:
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


# ============================================================
# UI DATA MODELS
# ============================================================

@dataclass(frozen=True)
class PositionSnap:
    symbol: str
    side: str
    size: float
    open_ts: float
    unrealized_pnl: Optional[float] = None


@dataclass(frozen=True)
class AlertSnap:
    symbol: str
    reason: str
    ts: float


# ============================================================
# CONSTANTS
# ============================================================

SNAPSHOT_TTL = 1.0
MAX_ALERTS = 5
MAX_POSITIONS_IN_UI = 200


# ============================================================
# CONNECTOR CORE
# ============================================================

class AppConnector:
    """
    Institutional UI ↔ Trading System bridge (v2)
    Clean, thread-safe, UI-ready event pipeline.
    """

    def __init__(
        self,
        market_cache: MarketStateCache,
        metrics: MetricsTracker,
        feedback: FeedbackEngine,
        account: AccountConnector,
        signal_pipeline: Optional[Any] = None,
        telegram_sender: Optional[Any] = None,
    ):
        self.market_cache = market_cache
        self.metrics = metrics
        self.feedback = feedback
        self.account = account
        self.signal_pipeline = signal_pipeline

        # UI binding (IMPORTANT)
        self.app = None
        self.ui_scheduler = None

        # STATE
        self._state = BotState.PAUSED
        self._state_lock = asyncio.Lock()
        self._snap_lock = asyncio.Lock()

        # CACHE
        self._last_snapshot: Optional[Dict[str, Any]] = None
        self._snapshot_ts = 0.0
        self._start_of_day_balance = None

        # EVENT STREAM
        self._event_stream: List[Dict[str, Any]] = []

    # ============================================================
    # UI ATTACHMENT (FIXED)
    # ============================================================

    def attach_ui(self, app):
        """
        Called AFTER UI is created.
        Binds scheduler + UI safely.
        """
        self.app = app
        self.ui_scheduler = getattr(app, "ui_scheduler", None)

        logger.info("UI attached successfully (AppConnector v2 ready)")

    # ============================================================
    # TRADE EVENT EMITTER (FIXED)
    # ============================================================

    def emit_trade_event(self, event: dict):
        """
        Push event to internal stream + UI safely
        """
        self._event_stream.append(event)

        try:
            if self.ui_scheduler:
                self.ui_scheduler.dispatch(
                    lambda: self.ui_scheduler.on_trade_event(event)
                )
        except Exception:
            logger.exception("UI dispatch failed")

    # ============================================================
    # MARKET SESSION STATE
    # ============================================================

    def _refresh_session_modes(self) -> Mapping[str, str]:
        cache: Dict[str, str] = {}

        try:
            for sym in self.market_cache.symbols():
                state = self.market_cache.get_symbol_state(sym)
                cache[sym] = state.get("session", {}).get("mode", "UNCLEAR")
        except Exception:
            pass

        return cache

    # ============================================================
    # ORDERS → POSITIONS
    # ============================================================

    def _iter_orders(self) -> Iterable[Any]:
        try:
            return list(self.feedback._orders.values())
        except Exception:
            return []

    def _normalize_positions(self) -> List[PositionSnap]:
        snaps: List[PositionSnap] = []

        for s in self._iter_orders():
            try:
                if getattr(s, "closed", False):
                    continue

                snaps.append(
                    PositionSnap(
                        symbol=s.symbol,
                        side=s.side,
                        size=float(getattr(s, "size", 0.0)),
                        open_ts=float(getattr(s, "sent_ts", time.time())),
                        unrealized_pnl=getattr(s, "unrealized_pnl", None),
                    )
                )
            except Exception:
                continue

        return snaps[:MAX_POSITIONS_IN_UI]

    # ============================================================
    # ALERTS
    # ============================================================

    def _normalize_alerts(self) -> List[AlertSnap]:
        alerts: List[AlertSnap] = []

        for s in self._iter_orders():
            try:
                if getattr(s, "reject_reason", None) or getattr(s, "closed", False):
                    alerts.append(
                        AlertSnap(
                            symbol=s.symbol,
                            reason=getattr(s, "reject_reason", "closed"),
                            ts=getattr(s, "sent_ts", time.time()),
                        )
                    )
            except Exception:
                continue

        return alerts[:MAX_ALERTS]

    # ============================================================
    # DASHBOARD SNAPSHOT (UI FEED)
    # ============================================================

    async def get_dashboard_snapshot(self, force_refresh: bool = False) -> Dict[str, Any]:

        now = time.time()

        if (
            not force_refresh
            and self._last_snapshot
            and now - self._snapshot_ts < SNAPSHOT_TTL
        ):
            return self._last_snapshot

        async with self._snap_lock:
            account_state = self.account.get_account_state()

            if account_state is None:
                return {
                    "bot_state": self._state,
                    "error": "Account unavailable",
                    "ts": now,
                }

            if self._start_of_day_balance is None:
                self._start_of_day_balance = account_state.balance

            pnl = account_state.balance - self._start_of_day_balance

            open_positions = self._normalize_positions()

            snapshot = {
                "bot_state": self._state,
                "balance": account_state.balance,
                "equity": account_state.equity,
                "pnl": pnl,
                "total_positions": len(open_positions),
                "positions": [p.__dict__ for p in open_positions],
                "alerts": [a.__dict__ for a in self._normalize_alerts()],
                "session_modes": dict(self._refresh_session_modes()),
                "ts": now,
            }

            self._last_snapshot = snapshot
            self._snapshot_ts = now
            return snapshot

    # ============================================================
    # BOT CONTROL
    # ============================================================

    async def start_bot(self):
        async with self._state_lock:
            self._state = BotState.RUNNING

    async def pause_bot(self):
        async with self._state_lock:
            self._state = BotState.PAUSED

    @property
    def state(self) -> str:
        return self._state
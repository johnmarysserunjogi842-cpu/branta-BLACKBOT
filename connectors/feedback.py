# connectors/feedback.py 
from __future__ import annotations

import time
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List
from enum import Enum

from performance.metrics import MetricsTracker, TradeRecord

# ============================================================
# LOGGING (COLD PATH ONLY)
# ============================================================

logger = logging.getLogger("feedback")
logger.setLevel(logging.INFO)

# ============================================================
# EVENTS
# ============================================================

class FeedbackEvent(Enum):
    ORDER_SENT = "ORDER_SENT"
    ORDER_ACK = "ORDER_ACK"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    POSITION_CLOSED = "POSITION_CLOSED"
    POSITION_MODIFIED = "POSITION_MODIFIED"
    EXECUTION_ANOMALY = "EXECUTION_ANOMALY"

# ============================================================
# EXECUTION SNAPSHOT
# ============================================================

@dataclass(slots=True)
class ExecutionSnapshot:
    order_id: str
    symbol: str
    side: str                 # BUY / SELL
    intent_id: str
    sent_ts: float
    ack_ts: Optional[float] = None
    fill_ts: Optional[float] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    volume: Optional[float] = None
    slippage: Optional[float] = None
    reject_reason: Optional[str] = None
    closed: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)

# ============================================================
# FEEDBACK ENGINE
# ============================================================

class FeedbackEngine:
    """
    Institution-grade execution feedback engine.

    Responsibilities:
    - Track order lifecycle
    - Detect execution anomalies
    - Compute execution quality
    - Feed MetricsTracker
    - Emit tuning signals upstream
    """

    def __init__(
        self,
        metrics: MetricsTracker,
        event_sink: Optional[asyncio.Queue] = None,
    ) -> None:
        self._metrics = metrics
        self._event_sink = event_sink

        # order_id -> ExecutionSnapshot
        self._orders: Dict[str, ExecutionSnapshot] = {}

        # intent_id -> order_id (idempotency)
        self._intent_map: Dict[str, str] = {}

        # lightweight async lock (rare contention)
        self._lock = asyncio.Lock()

    # ============================================================
    # ORDER LIFECYCLE
    # ============================================================

    async def on_order_sent(
        self,
        order_id: str,
        intent_id: str,
        symbol: str,
        side: str,
        volume: float,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        async with self._lock:
            if intent_id in self._intent_map:
                logger.warning("Duplicate intent detected: %s", intent_id)
                await self._emit(
                    FeedbackEvent.EXECUTION_ANOMALY,
                    {"type": "DUPLICATE_INTENT", "intent_id": intent_id},
                )
                return

            snap = ExecutionSnapshot(
                order_id=order_id,
                intent_id=intent_id,
                symbol=symbol,
                side=side,
                volume=volume,
                sent_ts=time.time(),
                meta=meta or {},
            )

            self._orders[order_id] = snap
            self._intent_map[intent_id] = order_id

        await self._emit(FeedbackEvent.ORDER_SENT, snap)

    async def on_order_ack(self, order_id: str) -> None:
        async with self._lock:
            snap = self._orders.get(order_id)
            if not snap:
                return
            snap.ack_ts = time.time()

        await self._emit(FeedbackEvent.ORDER_ACK, snap)

    async def on_order_fill(
        self,
        order_id: str,
        price: float,
        slippage: float,
    ) -> None:
        async with self._lock:
            snap = self._orders.get(order_id)
            if not snap:
                return

            snap.fill_ts = time.time()
            snap.entry_price = price
            snap.slippage = slippage

        await self._emit(FeedbackEvent.ORDER_FILLED, snap)

    async def on_order_rejected(
        self,
        order_id: str,
        reason: str,
    ) -> None:
        async with self._lock:
            snap = self._orders.get(order_id)
            if not snap:
                return

            snap.reject_reason = reason

        await self._emit(
            FeedbackEvent.ORDER_REJECTED,
            {"order_id": order_id, "reason": reason},
        )

    # ============================================================
    # POSITION CLOSE
    # ============================================================

    async def on_position_closed(
        self,
        order_id: str,
        exit_price: float,
        pnl_r: float,
        reason: str,
    ) -> None:
        async with self._lock:
            snap = self._orders.get(order_id)
            if not snap or snap.closed:
                return

            snap.exit_price = exit_price
            snap.closed = True

            trade = TradeRecord(
                symbol=snap.symbol,
                bias=snap.side,
                entry_ts=snap.fill_ts or snap.sent_ts,
                exit_ts=time.time(),
                entry_price=snap.entry_price,
                exit_price=exit_price,
                pnl_r=pnl_r,
                position_size=snap.volume,
                execution_mode=snap.meta.get("execution_mode"),
                reason=reason,
                meta={
                    "slippage": snap.slippage,
                    "latency_ack": (
                        snap.ack_ts - snap.sent_ts
                        if snap.ack_ts else None
                    ),
                    "latency_fill": (
                        snap.fill_ts - snap.sent_ts
                        if snap.fill_ts else None
                    ),
                },
            )

        # Metrics update is OUTSIDE lock
        self._metrics.record_trade(trade)

        await self._emit(FeedbackEvent.POSITION_CLOSED, trade)

    # ============================================================
    # ANOMALY DETECTION
    # ============================================================

    async def detect_stuck_orders(self, timeout: float = 5.0) -> None:
        """
        Called periodically by supervisor (NOT a loop here).
        """
        now = time.time()
        stuck: List[str] = []

        async with self._lock:
            for oid, snap in self._orders.items():
                if snap.ack_ts is None and now - snap.sent_ts > timeout:
                    stuck.append(oid)

        for oid in stuck:
            await self._emit(
                FeedbackEvent.EXECUTION_ANOMALY,
                {"type": "STUCK_ORDER", "order_id": oid},
            )

    # ============================================================
    # STRATEGY FEEDBACK SIGNALS
    # ============================================================

    def get_execution_quality(self, symbol: str) -> Dict[str, float]:
        """
        Lightweight snapshot for strategies.
        """
        metrics = self._metrics.get_symbol_metrics(symbol)
        if not metrics:
            return {}

        return {
            "win_rate": metrics.win_rate,
            "avg_confidence": metrics.average_confidence,
            "drawdown_r": metrics.max_drawdown_r,
            "cumulative_r": metrics.cumulative_pnl_r,
        }

    # ============================================================
    # EVENT EMISSION
    # ============================================================

    async def _emit(self, event: FeedbackEvent, payload: Any) -> None:
        if not self._event_sink:
            return

        try:
            self._event_sink.put_nowait(
                {"event": event.value, "payload": payload}
            )
        except asyncio.QueueFull:
            logger.warning("Feedback event dropped (queue full)")

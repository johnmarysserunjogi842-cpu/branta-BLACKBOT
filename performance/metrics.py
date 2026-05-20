# performance/metrics.py
from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Deque
from collections import deque

# ============================================================
# DATA MODELS
# ============================================================

@dataclass(slots=True)
class TradeRecord:
    symbol: str
    bias: str                       # BUY / SELL
    entry_ts: float
    exit_ts: Optional[float] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    pnl_r: Optional[float] = None
    position_size: Optional[float] = None
    execution_mode: Optional[str] = None
    reason: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SymbolMetrics:
    symbol: str

    # --- counters ---
    total_trades: int = 0
    wins: int = 0

    # --- PnL ---
    cumulative_pnl_r: float = 0.0
    peak_pnl_r: float = 0.0
    max_drawdown_r: float = 0.0

    # --- execution quality ---
    avg_slippage: float = 0.0
    avg_latency_ms: float = 0.0

    # --- confidence ---
    average_confidence: float = 0.0

    # --- timestamps ---
    last_trade_ts: Optional[float] = None

    # --- rolling window (cold path use) ---
    recent_trades: Deque[TradeRecord] = field(
        default_factory=lambda: deque(maxlen=100)
    )

    @property
    def win_rate(self) -> float:
        return self.wins / max(1, self.total_trades)


# ============================================================
# METRICS TRACKER (INSTITUTION GRADE)
# ============================================================

class MetricsTracker:
    """
    Institution-grade performance tracker.

    Hot-path:
    - O(1) trade updates
    - No iteration over trade history

    Cold-path:
    - Rolling analysis
    - Strategy feedback
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._symbols: Dict[str, SymbolMetrics] = {}

        # global
        self._global_trades = 0
        self._global_wins = 0
        self._global_pnl_r = 0.0

    # ============================================================
    # HOT PATH: RECORD TRADE
    # ============================================================

    def record_trade(self, trade: TradeRecord) -> None:
        if trade.pnl_r is None:
            return

        with self._lock:
            m = self._symbols.get(trade.symbol)
            if not m:
                m = SymbolMetrics(symbol=trade.symbol)
                self._symbols[trade.symbol] = m

            # --- counters ---
            m.total_trades += 1
            self._global_trades += 1

            if trade.pnl_r > 0:
                m.wins += 1
                self._global_wins += 1

            # --- PnL ---
            m.cumulative_pnl_r += trade.pnl_r
            self._global_pnl_r += trade.pnl_r

            if m.cumulative_pnl_r > m.peak_pnl_r:
                m.peak_pnl_r = m.cumulative_pnl_r

            drawdown = m.peak_pnl_r - m.cumulative_pnl_r
            if drawdown > m.max_drawdown_r:
                m.max_drawdown_r = drawdown

            # --- execution quality ---
            slip = trade.meta.get("slippage")
            if slip is not None:
                m.avg_slippage += (slip - m.avg_slippage) / m.total_trades

            latency = trade.meta.get("latency_fill")
            if latency is not None:
                m.avg_latency_ms += (
                    latency * 1000 - m.avg_latency_ms
                ) / m.total_trades

            # --- confidence ---
            conf = trade.meta.get("final_confidence")
            if conf is not None:
                m.average_confidence += (
                    conf - m.average_confidence
                ) / m.total_trades

            # --- timestamps ---
            m.last_trade_ts = trade.exit_ts or trade.entry_ts

            # --- cold-path window ---
            m.recent_trades.append(trade)

    # ============================================================
    # READ APIs (SAFE)
    # ============================================================

    def get_symbol_metrics(self, symbol: str) -> Optional[SymbolMetrics]:
        with self._lock:
            return self._symbols.get(symbol)

    def get_all_metrics(self) -> Dict[str, SymbolMetrics]:
        with self._lock:
            return dict(self._symbols)

    def get_global_snapshot(self) -> Dict[str, float]:
        with self._lock:
            return {
                "global_pnl_r": self._global_pnl_r,
                "global_win_rate": self._global_wins / max(1, self._global_trades),
                "total_trades": self._global_trades,
            }

    # ============================================================
    # STRATEGY FEEDBACK HELPERS
    # ============================================================

    def is_symbol_degrading(
        self,
        symbol: str,
        max_dd: float,
        min_win_rate: float,
    ) -> bool:
        m = self.get_symbol_metrics(symbol)
        if not m:
            return False

        return (
            m.max_drawdown_r >= max_dd
            or m.win_rate <= min_win_rate
        )

    # ============================================================
    # RESET
    # ============================================================

    def reset_symbol(self, symbol: str) -> None:
        with self._lock:
            self._symbols.pop(symbol, None)

    def reset_all(self) -> None:
        with self._lock:
            self._symbols.clear()
            self._global_trades = 0
            self._global_wins = 0
            self._global_pnl_r = 0.0

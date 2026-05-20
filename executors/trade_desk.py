# trade_desk.py

from __future__ import annotations

import threading
import time
from typing import Dict, Any, List


class TradeDesk:
    """
    =========================================================
    🚀 BLACKBOT v4.1 INSTITUTIONAL TRADE DESK
    =========================================================

    Central trade memory + execution state layer.

    Responsibilities:
    ✔ MT5 execution feedback
    ✔ live trade feed
    ✔ pnl tracking
    ✔ open positions registry
    ✔ risk monitoring
    ✔ execution audit log
    ✔ trade replay memory
    ✔ thread-safe synchronization
    =========================================================
    """

    # =====================================================
    # INIT
    # =====================================================
    def __init__(self):

        self._lock = threading.RLock()

        # =================================================
        # TRADE STORAGE
        # =================================================
        self.trades: List[Dict[str, Any]] = []

        self.open_positions: Dict[int, Dict[str, Any]] = {}

        self.trade_replay: List[Dict[str, Any]] = []

        # =================================================
        # EVENT STREAM
        # =================================================
        self.events: List[Dict[str, Any]] = []

        # =================================================
        # EXECUTION STATS
        # =================================================
        self.total_trades = 0
        self.wins = 0
        self.losses = 0

        # =================================================
        # RISK METRICS
        # =================================================
        self.daily_pnl = 0.0
        self.max_drawdown = 0.0
        self.peak_equity = 0.0

    # =====================================================
    # EVENT EMITTER
    # =====================================================
    def emit(self, event_type: str, data: Dict[str, Any]):

        with self._lock:

            event = {
                "type": event_type,
                "data": data,
                "timestamp": time.time()
            }

            self.events.append(event)

            # memory cap
            if len(self.events) > 1000:
                self.events.pop(0)

    # =====================================================
    # REGISTER TRADE
    # =====================================================
    def register_trade(self, decision) -> int:

        with self._lock:

            trade_id = self.total_trades + 1

            trade = {
                "id": trade_id,

                # core
                "symbol": decision.symbol,
                "bias": decision.bias,

                # intelligence
                "confidence": getattr(
                    decision,
                    "final_confidence",
                    0.0
                ),

                "mode": getattr(
                    decision,
                    "aggression_mode",
                    "NORMAL"
                ),

                "risk": getattr(
                    decision,
                    "risk_multiplier",
                    1.0
                ),

                "allowed": getattr(
                    decision,
                    "allow_trade",
                    True
                ),

                "reason": getattr(
                    decision,
                    "reason",
                    ""
                ),

                # lifecycle
                "status": "OPEN",
                "timestamp": time.time(),

                # execution
                "entry_price": None,
                "exit_price": None,

                # pnl
                "pnl": 0.0,

                # mt5
                "ticket": None,
                "execution_status": "PENDING",
            }

            self.trades.append(trade)

            self.open_positions[trade_id] = trade

            self.trade_replay.append(dict(trade))

            self.total_trades += 1

            self.emit("TRADE_OPENED", trade)

            return trade_id

    # =====================================================
    # EXECUTION FEEDBACK (MT5)
    # =====================================================
    def mark_executed(
        self,
        trade_id: int,
        ticket: int,
        entry_price: float
    ):

        with self._lock:

            trade = self.open_positions.get(trade_id)

            if not trade:
                return

            trade["ticket"] = ticket
            trade["entry_price"] = entry_price
            trade["execution_status"] = "EXECUTED"

            self.emit("TRADE_EXECUTED", trade)

    # =====================================================
    # LIVE PNL UPDATE
    # =====================================================
    def update_trade(
        self,
        trade_id: int,
        pnl: float
    ):

        with self._lock:

            trade = self.open_positions.get(trade_id)

            if not trade:
                return

            trade["pnl"] = pnl

            # =========================
            # RISK ENGINE
            # =========================
            self.daily_pnl += pnl

            self.peak_equity = max(
                self.peak_equity,
                self.daily_pnl
            )

            drawdown = (
                self.peak_equity - self.daily_pnl
            )

            self.max_drawdown = max(
                self.max_drawdown,
                drawdown
            )

            self.emit("TRADE_UPDATED", trade)

    # =====================================================
    # CLOSE TRADE
    # =====================================================
    def close_trade(
        self,
        trade_id: int,
        pnl: float,
        exit_price: float = 0.0
    ):

        with self._lock:

            trade = self.open_positions.get(trade_id)

            if not trade:
                return

            trade["status"] = "CLOSED"

            trade["pnl"] = pnl

            trade["exit_price"] = exit_price

            trade["closed_timestamp"] = time.time()

            trade["execution_status"] = "CLOSED"

            self.daily_pnl += pnl

            # stats
            if pnl >= 0:
                self.wins += 1
            else:
                self.losses += 1

            # remove open position
            self.open_positions.pop(trade_id, None)

            self.emit("TRADE_CLOSED", trade)

    # =====================================================
    # RISK SHUTOFF
    # =====================================================
    def risk_shutoff(self) -> bool:

        if self.max_drawdown > 5.0:
            return True

        if self.daily_pnl < -3.0:
            return True

        return False

    # =====================================================
    # LIVE FEED
    # =====================================================
    def get_live_feed(self, limit=30):

        with self._lock:
            return self.events[-limit:]

    # =====================================================
    # OPEN POSITIONS
    # =====================================================
    def get_open_positions(self):

        with self._lock:
            return list(self.open_positions.values())

    # =====================================================
    # RECENT TRADES
    # =====================================================
    def get_recent_trades(self, limit=20):

        with self._lock:
            return list(self.trades[-limit:])

    # =====================================================
    # TRADE REPLAY SYSTEM
    # =====================================================
    def get_trade_replay(self, limit=100):

        with self._lock:
            return list(self.trade_replay[-limit:])

    # =====================================================
    # EXECUTION AUDIT LOG
    # =====================================================
    def get_audit_log(self, limit=100):

        with self._lock:
            return list(self.events[-limit:])

    # =====================================================
    # DASHBOARD SNAPSHOT
    # =====================================================
    def snapshot(self):

        with self._lock:

            total = max(self.total_trades, 1)

            win_rate = (
                self.wins / total
            ) * 100

            return {

                # trades
                "recent_trades":
                    self.trades[-20:],

                "open_positions":
                    list(
                        self.open_positions.values()
                    ),

                # event feed
                "events":
                    self.events[-30:],

                # stats
                "total_trades":
                    self.total_trades,

                "wins":
                    self.wins,

                "losses":
                    self.losses,

                "win_rate":
                    round(win_rate, 2),

                # risk
                "daily_pnl":
                    round(self.daily_pnl, 2),

                "max_drawdown":
                    round(self.max_drawdown, 2),

                # state
                "risk_shutdown":
                    self.risk_shutoff(),
            }
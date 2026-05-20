from __future__ import annotations

import time
import math
from dataclasses import dataclass, field
from typing import Dict, Any


# ============================================================
# RISK STATE
# ============================================================

@dataclass
class RiskState:
    equity: float = 0.0
    balance: float = 0.0

    daily_pnl: float = 0.0
    peak_equity: float = 0.0

    open_exposure: Dict[str, float] = field(default_factory=dict)
    symbol_risk: Dict[str, float] = field(default_factory=dict)

    last_reset_ts: float = field(default_factory=time.time)


# ============================================================
# RISK CONFIG
# ============================================================

@dataclass
class RiskConfig:
    max_daily_drawdown_pct: float = 5.0
    max_total_exposure: float = 0.25
    max_symbol_exposure: float = 0.10

    max_leverage: float = 10.0
    max_correlation_exposure: float = 0.35

    volatility_cutoff: float = 0.02
    emergency_drawdown_pct: float = 10.0


# ============================================================
# RESULT OBJECT
# ============================================================

@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""
    adjusted_volume: float = 0.0
    emergency: bool = False


# ============================================================
# INSTITUTIONAL RISK CORE
# ============================================================

class RiskCore:

    def __init__(self, config: RiskConfig):
        self.config = config
        self.state = RiskState()

        self._symbol_volatility: Dict[str, float] = {}
        self._correlation_map: Dict[str, Dict[str, float]] = {}

        self._emergency_shutdown = False

    # ========================================================
    # UPDATE PORTFOLIO STATE
    # ========================================================

    def update_account(self, equity: float, balance: float, daily_pnl: float):
        self.state.equity = equity
        self.state.balance = balance
        self.state.daily_pnl = daily_pnl

        if equity > self.state.peak_equity:
            self.state.peak_equity = equity

    # ========================================================
    # UPDATE EXPOSURE
    # ========================================================

    def update_exposure(self, symbol: str, exposure: float):
        self.state.open_exposure[symbol] = exposure

    # ========================================================
    # VOLATILITY FEED
    # ========================================================

    def update_volatility(self, symbol: str, volatility: float):
        self._symbol_volatility[symbol] = volatility

    # ========================================================
    # CORE RISK CHECK
    # ========================================================

    def validate_trade(
        self,
        symbol: str,
        volume: float,
        confidence: float,
        equity: float,
        volatility: float = 0.0,
        correlation_exposure: float = 0.0,
    ) -> RiskDecision:

        # ---------------------------
        # EMERGENCY STOP FIRST
        # ---------------------------
        dd_pct = self._drawdown_pct(equity)

        if dd_pct >= self.config.emergency_drawdown_pct:
            self._emergency_shutdown = True
            return RiskDecision(False, "EMERGENCY_DRAWDOWN_TRIGGERED", emergency=True)

        if self._emergency_shutdown:
            return RiskDecision(False, "SYSTEM_IN_EMERGENCY_LOCK", emergency=True)

        # ---------------------------
        # DAILY DRAWDOWN LIMIT
        # ---------------------------
        if dd_pct >= self.config.max_daily_drawdown_pct:
            return RiskDecision(False, "MAX_DRAWDOWN_REACHED")

        # ---------------------------
        # SYMBOL EXPOSURE LIMIT
        # ---------------------------
        current_symbol_exp = self.state.open_exposure.get(symbol, 0.0)
        if current_symbol_exp + volume > self.config.max_symbol_exposure:
            return RiskDecision(False, "SYMBOL_EXPOSURE_LIMIT")

        # ---------------------------
        # TOTAL EXPOSURE LIMIT
        # ---------------------------
        total_exposure = sum(abs(x) for x in self.state.open_exposure.values())
        if total_exposure > self.config.max_total_exposure:
            return RiskDecision(False, "TOTAL_EXPOSURE_LIMIT")

        # ---------------------------
        # LEVERAGE CHECK
        # ---------------------------
        leverage = (total_exposure / max(equity, 1e-9))
        if leverage > self.config.max_leverage:
            return RiskDecision(False, "LEVERAGE_LIMIT")

        # ---------------------------
        # VOLATILITY PROTECTION
        # ---------------------------
        if volatility > self.config.volatility_cutoff:
            return RiskDecision(False, "VOLATILITY_TOO_HIGH")

        # ---------------------------
        # CORRELATION RISK
        # ---------------------------
        if correlation_exposure > self.config.max_correlation_exposure:
            return RiskDecision(False, "CORRELATION_RISK")

        # ---------------------------
        # CONFIDENCE SCALING (INSTITUTIONAL SIZING)
        # ---------------------------
        adjusted_volume = volume * confidence

        # safety clamp
        adjusted_volume = max(0.0, min(adjusted_volume, self.config.max_symbol_exposure))

        return RiskDecision(
            allowed=True,
            reason="OK",
            adjusted_volume=adjusted_volume
        )

    # ========================================================
    # DRAWDOWN CALC
    # ========================================================

    def _drawdown_pct(self, equity: float) -> float:
        if self.state.peak_equity <= 0:
            return 0.0

        dd = (self.state.peak_equity - equity) / self.state.peak_equity
        return dd * 100

    # ========================================================
    # RESET SYSTEM (DAILY)
    # ========================================================

    def reset_daily(self):
        self.state.daily_pnl = 0.0
        self.state.last_reset_ts = time.time()
        self._emergency_shutdown = False
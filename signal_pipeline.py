from __future__ import annotations
import hashlib
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List


# ============================================================
# DATA MODELS
# ============================================================

@dataclass(frozen=True)
class StrategySignal:
    """
    Output from StrategyManager (NO aggression inside).
    """
    symbol: str
    bias: str                 # "BUY" | "SELL" | "NONE"
    confidence: float         # 0..1
    regime: str               # "TRENDING" | "RANGING" | "UNCLEAR"
    contributors: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    ts: float = field(default_factory=lambda: time.time())


@dataclass(frozen=True)
class AggressionContext:
    """
    Output from SessionManager OR NewsFilter.
    """
    mode: str                 # "STANDBY" | "NORMAL" | "ATTACK" | "MAX_EFFICIENCY" | "DEFENSIVE" | ...
    confidence: float         # 0..1
    reason: str
    session: Optional[str] = None
    expiry_ts: Optional[float] = None


@dataclass(frozen=True)
class ExecutionDecision:
    """
    Output from SignalPipeline -> TradeExecutor.
    """
    symbol: str
    allow_trade: bool
    bias: str
    final_confidence: float
    aggression_mode: str
    risk_multiplier: float
    cooldown_seconds: float
    max_positions: int
    allow_stack: bool
    reason: str
    meta: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=lambda: time.time())


# ============================================================
# MODE PROFILE
# ============================================================

@dataclass(frozen=True)
class ModeProfile:
    """
    Defines how aggressive the bot behaves.
    """
    name: str

    min_confidence: float
    min_final_confidence: float

    max_trades_per_window: int
    trade_window_seconds: float
    cooldown_seconds: float

    risk_multiplier: float
    allow_stack: bool
    max_positions_per_symbol: int

    max_loss_streak: int
    lockout_seconds_on_loss_streak: float

    require_liquidity_quality: bool = True
    require_efficiency_quality: bool = False

    stack_cooldown_seconds: float = 25.0
    min_stack_final_confidence: float = 0.56
    max_stack_spread_multiplier: float = 1.60
    max_stack_atr_ratio: float = 1.80
    min_stack_efficiency_ratio: float = 0.28

    require_profit_for_stack: bool = True
    min_unrealized_pnl_r_to_stack: float = 0.12


# ============================================================
# RATE LIMITER
# ============================================================

class RollingWindowLimiter:
    """
    Very fast rolling limiter: trades per window.
    """
    __slots__ = ("window_seconds", "max_count", "_events")

    def __init__(self, window_seconds: float, max_count: int):
        self.window_seconds = float(window_seconds)
        self.max_count = int(max_count)
        self._events: List[float] = []

    def allow(self, now: float) -> bool:
        cutoff = now - self.window_seconds

        ev = self._events
        i = 0
        while i < len(ev) and ev[i] < cutoff:
            i += 1
        if i > 0:
            del ev[:i]

        return len(ev) < self.max_count

    def commit(self, now: float) -> None:
        self._events.append(now)


# ============================================================
# SIGNAL PIPELINE CORE
# ============================================================

class SignalPipeline:

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.cfg = config or {}
        self._micro_balance_threshold = float(self.cfg.get("micro_balance_threshold", 20.0))

        self.modes: Dict[str, ModeProfile] = self._default_modes()

        self._lock = threading.RLock()

        self._limiters: Dict[str, RollingWindowLimiter] = {}
        self._global_limiters: Dict[str, RollingWindowLimiter] = {}

        self._cooldown_until: Dict[str, float] = {}
        self._stack_cooldown_until: Dict[str, float] = {}
        self._loss_streak: Dict[str, int] = {}
        self._lockout_until: Dict[str, float] = {}
        self._recent_confidence: Dict[str, List[float]] = {}

        self._open_positions: Dict[str, int] = {}

    # ============================================================
    # PUBLIC API
    # ============================================================

    def update_open_positions(self, symbol: str, count: int) -> None:
        with self._lock:
            self._open_positions[symbol] = int(count)

    def on_trade_result(self, symbol: str, pnl_r: float) -> None:
        with self._lock:
            if pnl_r < 0:
                self._loss_streak[symbol] = self._loss_streak.get(symbol, 0) + 1
            else:
                self._loss_streak[symbol] = 0

    # ============================================================
    # MAIN DECISION
    # ============================================================

    def evaluate(
        self,
        signal: StrategySignal,
        session_ctx: AggressionContext,
        news_ctx_by_symbol: Optional[Dict[str, AggressionContext]] = None,
        market_state: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> ExecutionDecision:

        t = now or time.time()
        news_ctx_by_symbol = news_ctx_by_symbol or {}
        market_state = market_state or {}

        final_ctx = self._resolve_aggression_context(
            signal.symbol, session_ctx, news_ctx_by_symbol, t
        )

        profile = self.modes.get(final_ctx.mode, self.modes["NORMAL"])

        balance = float(market_state.get("account_balance", 0.0))
        spread = float(market_state.get("spread_multiplier", 1.0))
        atr = float(market_state.get("atr_ratio", 1.0))
        eff = float(market_state.get("efficiency_ratio", 0.5))

        if 0 < balance < self._micro_balance_threshold:
            profile = self.modes["MICRO_HF" if spread > 1.3 else "MICRO_ATTACK"]

        if signal.bias == "NONE" or signal.confidence <= 0:
            return self._deny(signal, final_ctx, "No actionable bias", profile)

        if signal.regime == "UNCLEAR":
            return self._deny(signal, final_ctx, "Regime unclear", profile)

        final_conf = self._final_confidence(signal, final_ctx, market_state)

        if final_conf < profile.min_final_confidence:
            return self._deny(signal, final_ctx, "Confidence too low", profile)

        if self._in_cooldown(signal.symbol, t):
            return self._deny(signal, final_ctx, "Cooldown active", profile)

        open_pos = self._open_positions.get(signal.symbol, 0)

        allow_stack = self._stacking_allowed(
            signal, profile, final_ctx, market_state, final_conf, open_pos, t
        )

        limiter = self._get_limiter(signal.symbol, profile)

        with self._lock:
            global_limiter = self._get_global_limiter(profile)

            if not global_limiter.allow(t):
                return self._deny(signal, final_ctx, "Global limit", profile)

            if not limiter.allow(t):
                return self._deny(signal, final_ctx, "Rate limit", profile)

            limiter.commit(t)
            global_limiter.commit(t)

        self._set_cooldown(signal.symbol, profile.cooldown_seconds, t)

        return ExecutionDecision(
            symbol=signal.symbol,
            allow_trade=True,
            bias=signal.bias,
            final_confidence=round(final_conf, 4),
            aggression_mode=profile.name,
            risk_multiplier=profile.risk_multiplier,
            cooldown_seconds=profile.cooldown_seconds,
            max_positions=profile.max_positions_per_symbol,
            allow_stack=allow_stack,
            reason="Approved",
            meta={"open_positions": open_pos},
        )

    # ============================================================
    # STACKING LOGIC (UNCHANGED STRENGTH)
    # ============================================================

    def _stacking_allowed(
        self,
        signal: StrategySignal,
        profile: ModeProfile,
        final_ctx: AggressionContext,
        market_state: Dict[str, Any],
        final_confidence: float,
        open_positions: int,
        now: float,
    ) -> bool:

        if open_positions <= 0:
            return False

        if not profile.allow_stack:
            return False

        if signal.regime == "UNCLEAR":
            return False

        if final_confidence < profile.min_stack_final_confidence:
            return False

        if self._in_stack_cooldown(signal.symbol, now):
            return False

        return True

    # ============================================================
    # AGGRESSION MERGE
    # ============================================================

    def _resolve_aggression_context(
        self,
        symbol: str,
        session_ctx: AggressionContext,
        news_ctx_by_symbol: Dict[str, AggressionContext],
        now: float,
    ) -> AggressionContext:

        news_ctx = news_ctx_by_symbol.get(symbol)
        if not news_ctx:
            return session_ctx

        if news_ctx.expiry_ts and now > news_ctx.expiry_ts:
            return session_ctx

        return news_ctx

    # ============================================================
    # CONFIDENCE
    # ============================================================

    def _final_confidence(self, signal, ctx, market_state):
        raw = signal.confidence
        a = ctx.confidence
        conf = raw * (0.7 + 0.3 * a)

        if market_state.get("atr_ratio", 1.0) > 2.0:
            conf *= 0.8

        if market_state.get("spread_multiplier", 1.0) > 1.8:
            conf *= 0.8

        return max(0.0, min(conf, 1.0))

    # ============================================================
    # LIMITERS / COOLDOWN
    # ============================================================

    def _get_limiter(self, symbol: str, profile: ModeProfile):
        lim = self._limiters.get(symbol)
        if not lim:
            lim = RollingWindowLimiter(profile.trade_window_seconds, profile.max_trades_per_window)
            self._limiters[symbol] = lim
        return lim

    def _get_global_limiter(self, profile: ModeProfile):
        lim = self._global_limiters.get(profile.name)
        if not lim:
            lim = RollingWindowLimiter(60, 30)
            self._global_limiters[profile.name] = lim
        return lim

    def _set_cooldown(self, symbol: str, sec: float, now: float):
        self._cooldown_until[symbol] = now + sec

    def _in_cooldown(self, symbol: str, now: float) -> bool:
        return now < self._cooldown_until.get(symbol, 0)

    def _in_stack_cooldown(self, symbol: str, now: float) -> bool:
        return now < self._stack_cooldown_until.get(symbol, 0)

    # ============================================================
    # EDGE SAFETY (KEPT STRONG)
    # ============================================================

    def _deny(self, signal, ctx, reason, profile):
        return ExecutionDecision(
            symbol=signal.symbol,
            allow_trade=False,
            bias=signal.bias,
            final_confidence=signal.confidence,
            aggression_mode=profile.name,
            risk_multiplier=0.0,
            cooldown_seconds=profile.cooldown_seconds,
            max_positions=profile.max_positions_per_symbol,
            allow_stack=False,
            reason=reason,
        )

    # ============================================================
    # DEFAULT MODES (FIXED STRUCTURE ONLY)
    # ============================================================

    def _default_modes(self) -> Dict[str, ModeProfile]:
        return {
            "NORMAL": ModeProfile(
                name="NORMAL",
                min_confidence=0.48,
                min_final_confidence=0.50,
                max_trades_per_window=4,
                trade_window_seconds=300,
                cooldown_seconds=45,
                risk_multiplier=1.0,
                allow_stack=False,
                max_positions_per_symbol=1,
                max_loss_streak=3,
                lockout_seconds_on_loss_streak=480,
            ),

            "ATTACK": ModeProfile(
                name="ATTACK",
                min_confidence=0.44,
                min_final_confidence=0.46,
                max_trades_per_window=7,
                trade_window_seconds=300,
                cooldown_seconds=20,
                risk_multiplier=1.35,
                allow_stack=True,
                max_positions_per_symbol=2,
                max_loss_streak=3,
                lockout_seconds_on_loss_streak=360,
            ),

            "MAX_EFFICIENCY": ModeProfile(
                name="MAX_EFFICIENCY",
                min_confidence=0.40,
                min_final_confidence=0.42,
                max_trades_per_window=10,
                trade_window_seconds=300,
                cooldown_seconds=10,
                risk_multiplier=1.6,
                allow_stack=True,
                max_positions_per_symbol=3,
                max_loss_streak=3,
                lockout_seconds_on_loss_streak=300,
            ),
        }
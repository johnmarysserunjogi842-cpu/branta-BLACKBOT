#Strategies/momentum_ignition.py
from __future__ import annotations

import time
import math
from typing import Dict, Any, Optional, Set

from strategy_manager.strategy_loader import BaseStrategy


class MomentumIgnition(BaseStrategy):
    """
    Institution-grade Momentum Ignition Strategy.

    Purpose:
    Detects the FIRST committed institutional impulse after volatility expansion.
    Fires once, early, and then goes silent.

    Design:
    - Ultra-low latency
    - Deterministic
    - One-shot per ignition
    - Regime-gated
    - Volatility + volume enforced
    """

    # -----------------------------
    # STRATEGY IDENTITY
    # -----------------------------
    name: str = "MomentumIgnition"

    # Only allowed during true expansion
    allowed_regimes: Set[str] = {"VOLATILITY_EXPANSION"}

    # Uses only execution timeframe
    required_timeframes: Set[str] = set()

    # Required per-timeframe features
    # (validated by StrategyLoader)
    required_features: Dict[str, Set[str]] = {
        # entry timeframe is dynamic → validated at runtime
    }

    # Required symbol-level state
    required_symbol_state: Set[str] = {"regime", "tf_policy", "session"}

    # -----------------------------
    # INTERNAL CONSTANTS
    # -----------------------------
    _MIN_ATR_RATIO = 1.20
    _MIN_VOLUME_RATIO = 1.50
    _MIN_MOMENTUM_Z = 2.0
    _MAX_SPREAD_MULT = 2.5

    _COOLDOWN_SEC = 90           # one-shot protection
    _DECAY_HALF_LIFE_SEC = 45.0  # confidence decay

    # -----------------------------
    # INTERNAL STATE (PER SYMBOL)
    # -----------------------------
    def __init__(self) -> None:
        self._last_fire_ts: Dict[str, float] = {}
        self._last_bar_ts: Dict[str, int] = {}

    # -----------------------------
    # PUBLIC ENTRY POINT
    # -----------------------------
    def evaluate(self, symbol: str, cache) -> Dict[str, Any]:
        now = time.time()

        # -------------------------
        # SYMBOL STATE
        # -------------------------
        sym_state = cache.get_symbol_state(symbol) or {}
        regime = (sym_state.get("regime") or {}).get("final_regime")
        tf_policy = sym_state.get("tf_policy") or {}
        session = sym_state.get("session") or {}

        if regime != "VOLATILITY_EXPANSION":
            return self._hold("Regime not allowed")

        entry_tf = tf_policy.get("entry_timeframe")
        if not entry_tf:
            return self._hold("Missing entry timeframe")

        tf_features = cache.get_features_by_tf(symbol) or {}
        feat = tf_features.get(entry_tf)
        if not feat:
            return self._hold("Missing TF features")

        # -------------------------
        # STALE / ONE-SHOT GATE
        # -------------------------
        bar_ts = int(feat.get("bar_ts", 0))
        last_bar = self._last_bar_ts.get(symbol)
        if last_bar is not None and bar_ts <= last_bar:
            return self._hold("No new bar")

        self._last_bar_ts[symbol] = bar_ts

        last_fire = self._last_fire_ts.get(symbol, 0.0)
        if now - last_fire < self._COOLDOWN_SEC:
            return self._hold("Ignition cooldown")

        # -------------------------
        # FEATURE EXTRACTION (FAST)
        # -------------------------
        momentum_spike = bool(feat.get("momentum_spike"))
        momentum_z = float(feat.get("momentum_zscore", 0.0))
        impulse = float(feat.get("impulse_strength", 0.0))

        volume_ratio = float(feat.get("volume_ratio", 0.0))
        delta_imbalance = float(feat.get("delta_imbalance", 0.0))

        atr_ratio = float(feat.get("atr_ratio", 0.0))
        range_expansion = bool(feat.get("range_expansion"))
        spread_mult = float(feat.get("spread_expansion", 1.0))

        liq_taken = bool(feat.get("liquidity_pool_taken"))
        no_resistance = bool(feat.get("no_immediate_resistance", True))

        direction = int(feat.get("direction", 0))  # +1 buy / -1 sell

        # -------------------------
        # HARD GATES (ABSOLUTE)
        # -------------------------
        if not momentum_spike:
            return self._hold("No momentum spike")

        if momentum_z < self._MIN_MOMENTUM_Z:
            return self._hold("Weak momentum")

        if volume_ratio < self._MIN_VOLUME_RATIO:
            return self._hold("No volume commitment")

        if atr_ratio < self._MIN_ATR_RATIO or not range_expansion:
            return self._hold("No volatility expansion")

        if spread_mult > self._MAX_SPREAD_MULT:
            return self._hold("Execution cost too high")

        if not liq_taken or not no_resistance:
            return self._hold("Unsafe structure")

        if direction == 0:
            return self._hold("No direction")

        # -------------------------
        # CONFIDENCE MODEL
        # -------------------------
        base = (
            min(1.0, momentum_z / 4.0)
            * min(1.0, volume_ratio / 3.0)
            * min(1.0, atr_ratio / 2.0)
            * min(1.0, abs(delta_imbalance))
        )

        # Time decay (half-life)
        decay = math.exp(
            -math.log(2.0) * (now - bar_ts) / self._DECAY_HALF_LIFE_SEC
        )

        confidence = max(0.0, min(1.0, base * decay))

        if confidence < 0.55:
            return self._hold("Confidence decayed")

        # -------------------------
        # FIRE (ONE-SHOT)
        # -------------------------
        self._last_fire_ts[symbol] = now

        action = "BUY" if direction > 0 else "SELL"

        return {
            "action": action,
            "confidence": confidence,
            "reason": "Momentum ignition with volatility expansion",
            "meta": {
                "entry_timeframe": entry_tf,
                "momentum_z": momentum_z,
                "volume_ratio": volume_ratio,
                "atr_ratio": atr_ratio,
                "spread_mult": spread_mult,
                "impulse_strength": impulse,
                "delta_imbalance": delta_imbalance,
                "ts": now,
            },
        }

    # -----------------------------
    # HOLD FACTORY (FAST)
    # -----------------------------
    @staticmethod
    def _hold(reason: str) -> Dict[str, Any]:
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reason": reason,
            "meta": {},
  }

#Strategies/mean_reversion.py
from __future__ import annotations

from typing import Dict, Any, Set
from strategy_manager.strategy_loader import BaseStrategy


class MeanReversionStrategy(BaseStrategy):
    """
    Institution-grade Mean Reversion Strategy (RANGING only)

    Purpose:
    - Capture oscillation around fair value in low-trend environments
    - Provide conservative confirmation votes, not leadership
    - NEVER fight trends or volatility expansion

    Design principles:
    - Regime-gated
    - Deterministic
    - Stateless
    - Ultra-fast (O(1))
    - Safe-failure (returns HOLD)
    """

    # ---------- STATIC CONTRACT ----------

    name: str = "MeanReversion"

    allowed_regimes: Set[str] = {"RANGING"}

    # Entry is always done on execution timeframe (from tf_policy)
    required_timeframes: Set[str] = set()

    # Features are expected on entry timeframe
    required_features: Dict[str, Set[str]] = {
        # enforced dynamically once entry_tf is known
    }

    required_symbol_state: Set[str] = {"regime", "tf_policy", "session"}

    # ---------- TUNABLE HARD LIMITS (SAFE CAPS) ----------

    MAX_CONFIDENCE = 0.55        # never dominate voting
    MIN_DISTANCE_Z = 0.8         # minimum deviation from value
    MAX_DISTANCE_Z = 3.5         # avoid panic extremes
    MAX_MOMENTUM = 0.35          # ensures momentum decay
    MIN_RANGE_QUALITY = 0.6      # avoids sloppy ranges

    # ---------- CORE EVALUATION ----------

    def evaluate(self, symbol: str, cache) -> Dict[str, Any]:
        """
        Produces a single conservative vote.

        Never throws.
        Never blocks.
        Never mutates shared state.
        """

        try:
            # ---------- SYMBOL CONTEXT ----------
            sym_state = cache.get_symbol_state(symbol) or {}
            regime = (sym_state.get("regime") or {}).get("final_regime")

            if regime != "RANGING":
                return self._hold("regime_not_ranging")

            session = sym_state.get("session") or {}
            if session.get("mode") in {"NO_TRADE", "PAUSE", "HALT"}:
                return self._hold("session_blocked")

            tf_policy = sym_state.get("tf_policy") or {}
            entry_tf = tf_policy.get("entry_timeframe")

            if not entry_tf:
                return self._hold("missing_entry_tf")

            # ---------- FEATURE ACCESS ----------
            tf_features = cache.get_features_by_tf(symbol).get(entry_tf)
            if not tf_features:
                return self._hold("missing_tf_features")

            # Required inputs (hard-gated)
            distance_z = tf_features.get("distance_from_value")
            momentum = tf_features.get("momentum")
            range_quality = tf_features.get("range_quality")
            value_slope = tf_features.get("value_slope")

            if (
                distance_z is None
                or momentum is None
                or range_quality is None
                or value_slope is None
            ):
                return self._hold("missing_required_features")

            # ---------- HARD SAFETY FILTERS ----------
            if range_quality < self.MIN_RANGE_QUALITY:
                return self._hold("weak_range")

            if abs(value_slope) > 0.15:
                # Fair value moving → not a range
                return self._hold("value_not_stable")

            if abs(momentum) > self.MAX_MOMENTUM:
                return self._hold("momentum_too_strong")

            abs_dist = abs(distance_z)
            if abs_dist < self.MIN_DISTANCE_Z:
                return self._hold("too_close_to_value")

            if abs_dist > self.MAX_DISTANCE_Z:
                return self._hold("panic_extension")

            # ---------- DIRECTION ----------
            if distance_z < 0:
                action = "BUY"
            else:
                action = "SELL"

            # ---------- CONFIDENCE (CAPPED + SMOOTH) ----------
            confidence = min(
                self.MAX_CONFIDENCE,
                0.35 + (abs_dist - self.MIN_DISTANCE_Z) * 0.1
            )

            return {
                "action": action,
                "confidence": round(confidence, 4),
                "reason": "range_mean_reversion",
                "meta": {
                    "entry_tf": entry_tf,
                    "distance_z": round(distance_z, 3),
                    "momentum": round(momentum, 3),
                    "range_quality": round(range_quality, 3),
                },
            }

        except Exception:
            # Absolute safety: never crash the engine
            return self._hold("exception_safe_hold")

    # ---------- INTERNAL HELPERS ----------

    @staticmethod
    def _hold(reason: str) -> Dict[str, Any]:
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reason": reason,
  }

#Strategies/break_and_retest.py
from __future__ import annotations

from typing import Dict, Any, Set
from strategy_manager.base_strategy import BaseStrategy


class BreakAndRetestStrategy(BaseStrategy):
    """
    Institution-grade Break & Retest strategy.

    PURPOSE
    -------
    Confirms market acceptance after a structural break.
    Acts as a CONFIRMATION strategy, not a leader.

    DESIGN PRINCIPLES
    -----------------
    - Stateless
    - O(1) execution
    - Feature-driven (no candle math)
    - Regime-gated (loader + voter)
    - Deterministic output
    """

    # ---------------------------------------------------------
    # STRATEGY IDENTITY
    # ---------------------------------------------------------
    name: str = "BreakAndRetest"

    # ---------------------------------------------------------
    # REGIME PERMISSION
    # ---------------------------------------------------------
    allowed_regimes: Set[str] = {
        "TRENDING",
        "VOLATILITY_EXPANSION",
    }

    # ---------------------------------------------------------
    # TIMEFRAME REQUIREMENTS
    # ---------------------------------------------------------
    # Uses ONLY the entry timeframe selected by TimeframeEngine
    required_timeframes: Set[str] = set()

    # ---------------------------------------------------------
    # FEATURE REQUIREMENTS (ENTRY TF)
    # ---------------------------------------------------------
    # These are PRE-COMPUTED upstream
    required_features: Dict[str, Set[str]] = {
        "__ENTRY__": {
            "key_level_broken",        # bool
            "break_direction",         # "UP" | "DOWN"
            "retest_detected",         # bool
            "acceptance_score",        # float 0..1
            "rejection_wick_ratio",    # float
            "volume_confirmation",     # bool
        }
    }

    # ---------------------------------------------------------
    # SYMBOL STATE REQUIREMENTS
    # ---------------------------------------------------------
    required_symbol_state: Set[str] = {
        "regime",
        "tf_policy",
        "session",
    }

    # ---------------------------------------------------------
    # CONFIDENCE BOUNDS (INSTITUTIONAL CAPS)
    # ---------------------------------------------------------
    _MIN_CONF: float = 0.30
    _MAX_CONF: float = 0.60

    # ---------------------------------------------------------
    # MAIN EVALUATION
    # ---------------------------------------------------------
    def evaluate(self, symbol: str, cache) -> Dict[str, Any]:
        """
        Evaluate Break & Retest condition for ONE symbol.

        Returns a normalized vote payload.
        """

        # ---------- Symbol State ----------
        sym_state = cache.get_symbol_state(symbol) or {}
        tf_policy = sym_state.get("tf_policy") or {}
        session = sym_state.get("session") or {}

        entry_tf = tf_policy.get("entry_timeframe")
        if not entry_tf:
            return self._hold("missing_entry_tf")

        # Session soft gate (hard gate enforced later)
        if session.get("mode") in {"NO_TRADE", "PAUSE", "HALT"}:
            return self._hold("session_blocked")

        # ---------- Features ----------
        tf_features = cache.get_features_by_tf(symbol) or {}
        feats = tf_features.get(entry_tf)

        if not feats:
            return self._hold("no_features")

        # ---------- Core Conditions ----------
        if not feats.get("key_level_broken"):
            return self._hold("no_break")

        if not feats.get("retest_detected"):
            return self._hold("no_retest")

        # Acceptance quality (institutional filter)
        acceptance = self._safe_float(feats.get("acceptance_score"))
        if acceptance < 0.50:
            return self._hold("weak_acceptance")

        # Rejection control (filters fake retests)
        rejection_ratio = self._safe_float(feats.get("rejection_wick_ratio"))
        if rejection_ratio > 0.60:
            return self._hold("rejected_retest")

        # Volume participation (confirms acceptance)
        if not feats.get("volume_confirmation", False):
            return self._hold("no_volume_confirmation")

        # ---------- Direction ----------
        direction = feats.get("break_direction")
        if direction == "UP":
            action = "BUY"
        elif direction == "DOWN":
            action = "SELL"
        else:
            return self._hold("invalid_direction")

        # ---------- Confidence ----------
        # Acceptance drives confidence, capped institutionally
        confidence = self._clamp(
            self._MIN_CONF + (acceptance * 0.30),
            self._MIN_CONF,
            self._MAX_CONF,
        )

        # ---------- Output ----------
        return {
            "action": action,
            "confidence": confidence,
            "reason": "Break accepted on retest",
            "meta": {
                "entry_timeframe": entry_tf,
                "acceptance_score": round(acceptance, 3),
                "rejection_wick_ratio": round(rejection_ratio, 3),
            },
        }

    # ---------------------------------------------------------
    # INTERNAL HELPERS (ULTRA-LIGHT)
    # ---------------------------------------------------------
    @staticmethod
    def _safe_float(v: Any) -> float:
        try:
            return float(v)
        except Exception:
            return 0.0

    @staticmethod
    def _clamp(v: float, lo: float, hi: float) -> float:
        if v < lo:
            return lo
        if v > hi:
            return hi
        return v

    @staticmethod
    def _hold(reason: str) -> Dict[str, Any]:
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reason": reason,
            "meta": {},
  }

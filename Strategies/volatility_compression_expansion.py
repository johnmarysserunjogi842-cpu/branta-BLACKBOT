#Strategies/volatility_compression_expansion.py
from __future__ import annotations

from typing import Dict, Any, Optional
from strategy_manager.base_strategy import BaseStrategy


# ============================================================
# VOLATILITY COMPRESSION → EXPANSION STRATEGY
# ============================================================

class VolatilityCompressionExpansion(BaseStrategy):
    """
    Institution-grade volatility compression → expansion strategy.

    PURPOSE
    -------
    Detects resolution of prolonged volatility compression
    into a directional expansion.

    DESIGN PRINCIPLES
    -----------------
    - Regime-gated (VOLATILITY_EXPANSION only)
    - Stateless & deterministic
    - Reads pre-computed features only
    - Extremely low latency (O(1))
    - Safe HOLD default on uncertainty
    """

    # -------------------------
    # STRATEGY IDENTITY
    # -------------------------
    name: str = "VolatilityCompressionExpansion"

    # -------------------------
    # REGIME CONTROL
    # -------------------------
    allowed_regimes = {"VOLATILITY_EXPANSION"}

    # -------------------------
    # REQUIRED TIMEFRAMES
    # -------------------------
    # Entry TF is selected dynamically via tf_policy,
    # but validation requires at least one TF present.
    required_timeframes = set()

    # -------------------------
    # REQUIRED FEATURES (PER TF)
    # -------------------------
    # These MUST be emitted by upstream feature extractors.
    required_features = {
        # Entry timeframe features
        # (tf_policy.entry_timeframe)
        "atr_ratio": {
            "atr_ratio",
            "volatility_percentile",
            "compression_bars",
            "range_width",
            "momentum_delta",
            "volume_ratio",
        }
    }

    # -------------------------
    # REQUIRED SYMBOL STATE
    # -------------------------
    required_symbol_state = {"regime", "tf_policy", "session"}

    # -------------------------
    # INTERNAL THRESHOLDS
    # -------------------------
    # Tuned conservatively for institutional safety.
    MIN_COMPRESSION_BARS = 8
    MAX_VOLATILITY_PERCENTILE = 35.0
    MIN_ATR_EXPANSION_RATIO = 1.4
    MIN_VOLUME_RATIO = 1.2
    MIN_MOMENTUM_ABS = 0.05

    MAX_CONFIDENCE = 0.72


    # ============================================================
    # MAIN ENTRY POINT
    # ============================================================
    def evaluate(self, symbol: str, cache) -> Dict[str, Any]:
        """
        Evaluate compression → expansion for one symbol.

        Returns a normalized vote payload:
        {
            action: BUY | SELL | HOLD
            confidence: float (0.0 → 1.0)
            reason: str
            meta: dict
        }
        """

        # -------------------------
        # SYMBOL STATE
        # -------------------------
        sym_state = cache.get_symbol_state(symbol) or {}
        regime = (sym_state.get("regime") or {}).get("final_regime")

        # HARD GATE: regime safety
        if regime != "VOLATILITY_EXPANSION":
            return self._hold("regime_not_allowed")

        session = sym_state.get("session") or {}
        if session.get("mode") in {"NO_TRADE", "PAUSE", "HALT"}:
            return self._hold("session_blocked")

        tf_policy = sym_state.get("tf_policy") or {}
        entry_tf = tf_policy.get("entry_timeframe")
        if not entry_tf:
            return self._hold("missing_entry_tf")

        # -------------------------
        # FEATURES (ENTRY TF)
        # -------------------------
        tf_features = cache.get_features_by_tf(symbol) or {}
        f = tf_features.get(entry_tf)
        if not f:
            return self._hold("missing_tf_features")

        # -------------------------
        # EXTRACT FEATURES (FAST)
        # -------------------------
        try:
            atr_ratio = float(f.get("atr_ratio", 0.0))
            vol_pct = float(f.get("volatility_percentile", 100.0))
            compression_bars = int(f.get("compression_bars", 0))
            range_width = float(f.get("range_width", 0.0))
            momentum = float(f.get("momentum_delta", 0.0))
            volume_ratio = float(f.get("volume_ratio", 0.0))
        except Exception:
            return self._hold("feature_cast_error")

        # -------------------------
        # PHASE 1: COMPRESSION CHECK
        # -------------------------
        if compression_bars < self.MIN_COMPRESSION_BARS:
            return self._hold("compression_too_short")

        if vol_pct > self.MAX_VOLATILITY_PERCENTILE:
            return self._hold("volatility_not_compressed")

        if range_width <= 0.0:
            return self._hold("invalid_range_width")

        # -------------------------
        # PHASE 2: EXPANSION CHECK
        # -------------------------
        if atr_ratio < self.MIN_ATR_EXPANSION_RATIO:
            return self._hold("atr_not_expanding")

        if volume_ratio < self.MIN_VOLUME_RATIO:
            return self._hold("volume_not_confirming")

        if abs(momentum) < self.MIN_MOMENTUM_ABS:
            return self._hold("momentum_too_weak")

        # -------------------------
        # DIRECTION SELECTION
        # -------------------------
        if momentum > 0:
            action = "BUY"
        elif momentum < 0:
            action = "SELL"
        else:
            return self._hold("no_direction")

        # -------------------------
        # CONFIDENCE MODEL
        # -------------------------
        confidence = self._compute_confidence(
            compression_bars=compression_bars,
            atr_ratio=atr_ratio,
            volume_ratio=volume_ratio,
            momentum=momentum,
        )

        if confidence <= 0.0:
            return self._hold("confidence_zero")

        # -------------------------
        # FINAL PAYLOAD
        # -------------------------
        return {
            "action": action,
            "confidence": confidence,
            "reason": "Volatility compression resolved with expansion",
            "meta": {
                "entry_timeframe": entry_tf,
                "compression_bars": compression_bars,
                "atr_expansion_ratio": round(atr_ratio, 3),
                "volume_ratio": round(volume_ratio, 3),
                "momentum_delta": round(momentum, 4),
            },
        }

    # ============================================================
    # CONFIDENCE ENGINE (DETERMINISTIC)
    # ============================================================
    def _compute_confidence(
        self,
        *,
        compression_bars: int,
        atr_ratio: float,
        volume_ratio: float,
        momentum: float,
    ) -> float:
        """
        Deterministic confidence scoring.
        No ML. No randomness. No state.
        """

        score = 0.0

        # Compression quality (time spent coiling)
        score += min(0.25, compression_bars / 40.0)

        # Expansion strength
        score += min(0.25, (atr_ratio - 1.0) / 2.0)

        # Participation
        score += min(0.20, (volume_ratio - 1.0) / 1.5)

        # Directional intent
        score += min(0.20, abs(momentum))

        # Safety cap
        return round(min(score, self.MAX_CONFIDENCE), 4)

    # ============================================================
    # HOLD FACTORY (SAFE DEFAULT)
    # ============================================================
    @staticmethod
    def _hold(reason: str) -> Dict[str, Any]:
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reason": reason,
            "meta": {},
  }

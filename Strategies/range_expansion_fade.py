#Strategies/range_expansion_fade.py
from __future__ import annotations

from typing import Dict, Any, Optional
from dataclasses import dataclass

# ============================================================
# STRATEGY CONTRACT IMPORT
# ============================================================

from strategy_manager.base_strategy import BaseStrategy
from market_analyzer.market_state_cache import MarketStateCache


# ============================================================
# STRATEGY CONFIG (STATIC, IMMUTABLE)
# ============================================================

MAX_CONFIDENCE: float = 0.45

REQUIRED_TF_FEATURES = (
    "range_high",
    "range_low",
    "failed_breakout",
    "volatility_spike_without_followthrough",
    "price",
)


# ============================================================
# RANGE EXPANSION FADE STRATEGY
# ============================================================

class RangeExpansionFade(BaseStrategy):
    """
    Institution-grade mean reversion strategy.

    Exploits false breakouts in confirmed RANGING regimes.
    Produces low-to-medium confidence fade votes only.
    """

    # --------------------------------------------------------
    # STRATEGY IDENTITY
    # --------------------------------------------------------

    name: str = "RangeExpansionFade"
    supported_regimes = {"RANGING"}

    # --------------------------------------------------------
    # CORE ENTRY POINT
    # --------------------------------------------------------

    def evaluate(
        self,
        symbol: str,
        market_state: MarketStateCache,
    ) -> Dict[str, Any]:
        """
        Evaluate strategy for ONE symbol.
        Stateless, deterministic, O(1).
        """

        # ====================================================
        # SYMBOL-LEVEL GATES (HARD FAILS)
        # ====================================================

        symbol_state = market_state.get_symbol_state(symbol)
        if not symbol_state:
            return self._hold("Missing symbol state")

        if symbol_state.session.mode != "ACTIVE":
            return self._hold("Session inactive")

        if symbol_state.regime.final_regime != "RANGING":
            return self._hold("Wrong regime")

        entry_tf = symbol_state.tf_policy.entry_timeframe
        tf_state = market_state.get_tf_state(symbol, entry_tf)

        if not tf_state:
            return self._hold("Missing timeframe state")

        # ====================================================
        # FEATURE EXTRACTION (LOCK-FREE READS)
        # ====================================================

        features = tf_state.features
        if not features:
            return self._hold("Missing features")

        for key in REQUIRED_TF_FEATURES:
            if key not in features:
                return self._hold(f"Missing feature: {key}")

        range_high = features["range_high"]
        range_low = features["range_low"]
        price = features["price"]
        failed_breakout = features["failed_breakout"]
        vol_spike = features["volatility_spike_without_followthrough"]

        # ====================================================
        # FALSE BREAKOUT DETECTION
        # ====================================================

        if not failed_breakout or not vol_spike:
            return self._hold("No false breakout")

        # ====================================================
        # DIRECTION DECISION
        # ====================================================

        if price > range_high:
            action = "SELL"
            boundary_distance = price - range_high

        elif price < range_low:
            action = "BUY"
            boundary_distance = range_low - price

        else:
            return self._hold("Price inside range")

        # ====================================================
        # CONFIDENCE SCALING (SAFE & CAPPED)
        # ====================================================

        range_width = max(range_high - range_low, 1e-9)
        distance_ratio = min(boundary_distance / range_width, 1.0)

        confidence = min(
            0.25 + (distance_ratio * 0.20),
            MAX_CONFIDENCE,
        )

        # ====================================================
        # FINAL VOTE (AUDITABLE)
        # ====================================================

        return {
            "action": action,
            "confidence": confidence,
            "reason": "Failed range breakout with unsustained volatility",
            "meta": {
                "price": price,
                "range_high": range_high,
                "range_low": range_low,
                "distance_ratio": distance_ratio,
                "volatility_spike": vol_spike,
                "failed_breakout": failed_breakout,
                "timeframe": entry_tf,
            },
        }

    # --------------------------------------------------------
    # HOLD HELPER (FAST PATH)
    # --------------------------------------------------------

    @staticmethod
    def _hold(reason: str) -> Dict[str, Any]:
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reason": reason,
            "meta": {},
  }

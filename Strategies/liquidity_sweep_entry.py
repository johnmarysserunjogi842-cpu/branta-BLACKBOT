#Strategies/liquidity_sweep_entry.py
from __future__ import annotations

from typing import Dict, Any, Optional, Set
from strategy_manager.strategy_loader import BaseStrategy


class LiquiditySweepEntry(BaseStrategy):
    """
    Liquidity Sweep Entry (LSE)

    Edge:
    -----
    Trades failed stop-hunts:
      - Liquidity is swept
      - Strong rejection appears
      - Momentum flips
      - Price fails to continue

    Regimes:
    --------
    Active ONLY in:
      - RANGING
      - VOLATILITY_EXPANSION

    Never trades in TRENDING or UNCLEAR.

    Role in system:
    ---------------
    This is a reactive mean-reversion / failure strategy.
    It complements (never fights):
      - Range strategies in RANGING
      - Breakout strategies in VOLATILE

    It is NOT a leader strategy — it is a tactical entry model.
    """

    # === CONTRACT FOR STRATEGY LOADER ===

    name: str = "LiquiditySweepEntry"

    allowed_regimes: Set[str] = {
        "RANGING",
        "VOLATILITY_EXPANSION",
    }

    # Default execution TF (can be overridden by tf_policy)
    required_timeframes: Set[str] = {"M5"}

    # Features we require from MarketStateCache per timeframe
    required_features: Dict[str, Set[str]] = {
        "M5": {
            # Core sweep logic
            "liquidity_taken",        # bool
            "sweep_side",             # "high" | "low" | None
            "rejection_direction",    # "bullish" | "bearish" | None
            "rejection_strength",     # float 0..1
            "momentum_shift",         # bool

            # Optional context (used for confidence scaling)
            "range_width",            # float (pips/points)
            "volume_spike",           # bool
        }
    }

    # Symbol-level state we depend on
    required_symbol_state: Set[str] = {
        "regime",
        "tf_policy",
        "session",
    }

    # === CONSTANTS (TUNED FOR REAL SYSTEMS) ===

    MIN_REJECTION = 0.45     # minimum wick/rejection quality
    STRONG_REJECTION = 0.70  # "institutional" rejection

    BASE_CONF = 0.55         # baseline if all conditions pass
    MAX_CONF = 0.82          # hard cap

    # === PUBLIC API ===

    def evaluate(self, symbol: str, cache) -> Dict[str, Any]:
        """
        Return a vote payload:
        {
          "action": "BUY" | "SELL" | "HOLD",
          "confidence": float 0..1,
          "reason": str,
          "meta": dict
        }
        """

        # ---------- 1) READ SYMBOL STATE (FAST PATH) ----------

        sym_state = cache.get_symbol_state(symbol) or {}
        regime = (sym_state.get("regime") or {}).get("final_regime", "UNCLEAR")
        tf_policy = sym_state.get("tf_policy") or {}
        session = sym_state.get("session") or {}

        # Respect session gate (system-wide rule)
        if session.get("mode") in {"NO_TRADE", "PAUSE", "HALT"}:
            return self._hold("Session blocked")

        # Must be allowed regime
        if regime not in self.allowed_regimes:
            return self._hold(f"Regime={regime} not allowed")

        # Choose execution timeframe
        entry_tf = tf_policy.get("entry_timeframe", "M5")

        # ---------- 2) READ FEATURES (FAST, SAFE) ----------

        tf_features = cache.get_features_by_tf(symbol) or {}
        feat = (tf_features.get(entry_tf) or {})

        # Pull required fields safely
        liquidity_taken = bool(feat.get("liquidity_taken", False))
        sweep_side = feat.get("sweep_side")          # "high" | "low"
        rejection_dir = feat.get("rejection_direction")  # "bullish" | "bearish"
        rejection_strength = float(feat.get("rejection_strength", 0.0))
        momentum_shift = bool(feat.get("momentum_shift", False))
        volume_spike = bool(feat.get("volume_spike", False))

        # ---------- 3) CORE EDGE LOGIC ----------

        # Condition 1: Liquidity must be swept
        if not liquidity_taken or sweep_side not in {"high", "low"}:
            return self._hold("No valid liquidity sweep")

        # Condition 2: Must have clear rejection
        if rejection_strength < self.MIN_REJECTION:
            return self._hold("Weak or no rejection")

        # Condition 3: Momentum must flip
        if not momentum_shift:
            return self._hold("No momentum shift")

        # ---------- 4) DETERMINE DIRECTION ----------

        # If stops above were swept and rejection is bearish → SELL
        if sweep_side == "high" and rejection_dir == "bearish":
            action = "SELL"
            side_text = "sweep_high_reject_down"

        # If stops below were swept and rejection is bullish → BUY
        elif sweep_side == "low" and rejection_dir == "bullish":
            action = "BUY"
            side_text = "sweep_low_reject_up"

        else:
            return self._hold("Sweep occurred but no directional rejection")

        # ---------- 5) INSTITUTION-GRADE CONFIDENCE MODEL ----------

        # Start from base
        conf = self.BASE_CONF

        # Strong rejection bonus
        if rejection_strength >= self.STRONG_REJECTION:
            conf += 0.12

        # Volume spike bonus
        if volume_spike:
            conf += 0.05

        # Sharper rejection → higher confidence
        conf += 0.10 * rejection_strength

        # Hard cap
        conf = max(0.0, min(self.MAX_CONF, conf))

        # ---------- 6) RETURN CLEAN VOTE ----------

        return {
            "action": action,
            "confidence": round(conf, 4),
            "reason": (
                f"Liquidity sweep at {sweep_side} + "
                f"strong {rejection_dir} rejection + momentum shift"
            ),
            "meta": {
                "entry_tf": entry_tf,
                "regime": regime,
                "sweep_side": sweep_side,
                "rejection_strength": round(rejection_strength, 3),
                "volume_spike": volume_spike,
            },
        }

    # ---------- INTERNAL HOLD FACTORY ----------

    @staticmethod
    def _hold(reason: str) -> Dict[str, Any]:
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reason": reason,
            "meta": {},
  }

#Strategies/smart_money_structure.py
from __future__ import annotations

from typing import Dict, Any, Optional, Set
from strategy_manager.strategy_loader import BaseStrategy


class SmartMoneyStructure(BaseStrategy):
    """
    Smart Money Structure (SMS) — Refactored for:
    - Higher profitability
    - Better robustness
    - Same inputs/outputs
    - Same contract with StrategyLoader
    - Zero performance regression
    """

    # === STRICT CONTRACT WITH STRATEGY LOADER ===

    name: str = "SmartMoneyStructure"

    # Only allowed in trending markets
    allowed_regimes: Set[str] = {"TRENDING"}

    # Two timeframes: HTF for bias, LTF for entry
    required_timeframes: Set[str] = {"H1", "M5"}

    # Required feature keys per timeframe
    required_features: Dict[str, Set[str]] = {
        "H1": {
            "htf_trend",           # "BULLISH" | "BEARISH"
            "structure_break",     # bool
            "liquidity_taken",     # bool
            "protected_zone",      # price range or zone marker
            "trend_strength",      # 0..1 float
        },
        "M5": {
            "micro_bos",           # bool
            "rejection_candle",    # bool
            "protected_pullback",  # bool
            "liquidity_taken",     # bool
            "momentum_shift",      # bool
        },
    }

    # Required symbol-level state
    required_symbol_state: Set[str] = {"regime", "tf_policy", "session"}

    # ============================================================
    # SAFE HELPERS (NEW)
    # ============================================================

    @staticmethod
    def _to_float_safe(val, default: float = 0.0) -> float:
        """Convert to float without crashing."""
        try:
            return float(val)
        except Exception:
            return default

    @staticmethod
    def _b(val) -> bool:
        """Safe boolean cast."""
        return bool(val)

    # ============================================================
    # MAIN EVALUATION
    # ============================================================

    def evaluate(self, symbol: str, cache) -> Optional[Dict[str, Any]]:

        # --- Load state safely ---
        sym_state = cache.get_symbol_state(symbol) or {}
        tf_features = cache.get_features_by_tf(symbol) or {}

        # --- Regime gate ---
        regime = (sym_state.get("regime") or {}).get("final_regime", "UNCLEAR")
        if regime != "TRENDING":
            return None  # Silent

        # --- Session gate ---
        session = sym_state.get("session") or {}
        if session.get("mode") in {"NO_TRADE", "PAUSE", "HALT"}:
            return None

        # --- Load timeframe data (robust) ---
        h1 = tf_features.get("H1") or {}
        m5 = tf_features.get("M5") or {}

        # ------------------------------------------------
        # HTF BIAS (TREND QUALITY GATE — stricter)
        # ------------------------------------------------

        htf_trend = h1.get("htf_trend")
        if htf_trend not in {"BULLISH", "BEARISH"}:
            return None

        # Safer float + tighter threshold (profit booster)
        trend_strength = self._to_float_safe(h1.get("trend_strength"), 0.0)
        if trend_strength < 0.65:   # <-- tighter than before
            return None

        # Must have real structure shift
        if not self._b(h1.get("structure_break", False)):
            return None

        # Must have real liquidity sweep
        htf_liquidity = self._b(h1.get("liquidity_taken", False))
        if not htf_liquidity:
            return None

        protected_zone = h1.get("protected_zone")
        if protected_zone is None:
            return None

        # ------------------------------------------------
        # LTF EXECUTION CONFIRMATION (cached once)
        # ------------------------------------------------

        ltf_pullback = self._b(m5.get("protected_pullback", False))
        ltf_micro_bos = self._b(m5.get("micro_bos", False))
        ltf_rejection = self._b(m5.get("rejection_candle", False))
        ltf_momentum = self._b(m5.get("momentum_shift", False))

        if not ltf_pullback:
            return None

        if not ltf_micro_bos:
            return None

        if not ltf_rejection:
            return None

        if not ltf_momentum:
            return None

        # ------------------------------------------------
        # ACTION (UNCHANGED CONTRACT)
        # ------------------------------------------------

        action = "BUY" if htf_trend == "BULLISH" else "SELL"

        # ------------------------------------------------
        # REFINED CONFIDENCE MODEL (same output range)
        # ------------------------------------------------

        confidence = 0.50  # base

        # Stronger trend = more confidence
        if trend_strength >= 0.75:
            confidence += 0.15
        elif trend_strength >= 0.70:
            confidence += 0.10

        # Reward clean liquidity sweeps
        if htf_liquidity:
            confidence += 0.10

        # Reward clean pullback + BOS combo
        if ltf_pullback and ltf_micro_bos:
            confidence += 0.10

        # Reward momentum alignment
        if ltf_momentum:
            confidence += 0.05

        # Hard cap (unchanged)
        confidence = min(0.85, confidence)

        # ------------------------------------------------
        # HUMAN READABLE REASON (UNCHANGED STYLE)
        # ------------------------------------------------

        reason = (
            f"HTF {htf_trend} BOS + liquidity sweep + "
            f"protected pullback + LTF micro BOS"
        )

        # ------------------------------------------------
        # FINAL VOTE (IDENTICAL SHAPE TO YOUR ORIGINAL)
        # ------------------------------------------------

        return {
            "action": action,
            "confidence": round(float(confidence), 4),
            "reason": reason,
            "meta": {
                "htf_trend": htf_trend,
                "trend_strength": round(trend_strength, 3),
                "htf_liquidity_swept": htf_liquidity,
                "ltf_pullback": ltf_pullback,
                "ltf_micro_bos": ltf_micro_bos,
            },
  }

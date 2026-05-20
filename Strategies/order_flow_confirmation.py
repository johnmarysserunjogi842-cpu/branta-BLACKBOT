# Strategies/order_flow_confirmation.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, Set, Optional
import math
import logging

# ============================================================
# ORDER FLOW CONFIRMATION STRATEGY
# ============================================================
class OrderFlowConfirmation:
    """
    Institution-grade Order Flow Confirmation Strategy.

    Role:
    - CONFIRM other TRENDING strategies
    - Never trades alone
    - Only supports continuation ideas
    - Capped confidence (safe boosting)

    Allowed Regime: TRENDING only
    """
    name: str = "OrderFlowConfirmation"
    allowed_regimes: Set[str] = {"TRENDING"}
    required_timeframes: Set[str] = set()  # Will use entry TF from tf_policy
    required_features: Dict[str, Set[str]] = {
        # Features must exist in MarketStateCache for strategy to function
        "ENTRY_TF": {
            "delta_imbalance",
            "volume_imbalance",
            "absorption_detected",
            "aggressive_buy",
            "aggressive_sell",
            "trend_direction",
            "volume_ratio",
        }
    }
    required_symbol_state: Set[str] = {"regime", "tf_policy", "session"}

    # Safety caps
    MAX_CONFIDENCE: float = 0.45  # Never dominates
    MIN_CONFIDENCE: float = 0.15  # Only meaningful if confirmed

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(self.name)

    def evaluate(self, symbol: str, cache) -> Dict[str, Any]:
        """
        Main evaluation function.
        Reads MarketStateCache and returns vote payload.
        """
        try:
            sym_state = cache.get_symbol_state(symbol)
            if not sym_state:
                return self._hold_payload("No symbol state")

            # --- REGIME & SESSION CHECKS ---
            regime = (sym_state.get("regime") or {}).get("final_regime", "UNCLEAR")
            session_mode = (sym_state.get("session") or {}).get("mode", "ACTIVE")
            if regime != "TRENDING":
                return self._hold_payload(f"Regime not TRENDING ({regime})")
            if session_mode in {"NO_TRADE", "PAUSE", "HALT"}:
                return self._hold_payload(f"Session blocked ({session_mode})")

            # --- ENTRY TIMEFRAME ---
            entry_tf = (sym_state.get("tf_policy") or {}).get("entry_timeframe")
            if not entry_tf:
                return self._hold_payload("No entry timeframe")

            # --- FETCH FEATURES ---
            tf_features = cache.get_features_by_tf(symbol).get(entry_tf, {})
            f = tf_features
            required_keys = self.required_features.get("ENTRY_TF", set())
            if not all(k in f for k in required_keys):
                missing = [k for k in required_keys if k not in f]
                return self._hold_payload(f"Missing features: {missing}")

            # --- ORDER FLOW LOGIC ---
            delta = f.get("delta_imbalance", 0.0)
            volume_ratio = f.get("volume_ratio", 0.0)
            absorption = f.get("absorption_detected", False)
            trend_dir = f.get("trend_direction", "NEUTRAL")

            # Determine candidate action
            if absorption:
                action = "HOLD"
                reason = "Absorption detected, weak move"
            elif delta > 0 and trend_dir.upper() == "BULLISH":
                action = "BUY"
                reason = "Aggressive buy + trend alignment"
            elif delta < 0 and trend_dir.upper() == "BEARISH":
                action = "SELL"
                reason = "Aggressive sell + trend alignment"
            else:
                action = "HOLD"
                reason = "No confirmed aggression or misaligned trend"

            # --- CONFIDENCE SCALING ---
            raw_conf = min(abs(delta) * volume_ratio, 1.0)
            # Clamp to safe bounds
            confidence = max(self.MIN_CONFIDENCE, min(raw_conf, self.MAX_CONFIDENCE))
            if action == "HOLD":
                confidence = 0.0

            return {
                "action": action,
                "confidence": confidence,
                "reason": reason,
                "meta": {
                    "delta_imbalance": delta,
                    "volume_ratio": volume_ratio,
                    "absorption_detected": absorption,
                    "trend_direction": trend_dir,
                },
            }

        except Exception as e:
            self.logger.exception(f"OrderFlowConfirmation crashed for {symbol}: {e}")
            return self._hold_payload(f"Exception: {e}")

    # -------------------
    # INTERNAL: HOLD PAYLOAD
    # -------------------
    def _hold_payload(self, reason: str) -> Dict[str, Any]:
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reason": reason,
            "meta": {},
  }

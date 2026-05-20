# Strategies/htf_bias_ltf_entry.py
from __future__ import annotations
from typing import Dict, Any
import time
import math

COOLDOWN_SEC = 20  # slightly faster than swing strategies


class HTFBiasLTFEntry:
    """
    Institution-grade HTF Bias + LTF Entry strategy.

    ROLE:
    - HTF defines directional permission (bias)
    - LTF confirms execution timing
    - Never trades against HTF intent
    - Deterministic, stateless, cache-driven

    EXPECTED REGIME:
    - TRENDING only (enforced upstream)
    """

    def __init__(self):
        self._last_vote_ts: Dict[str, float] = {}
        self._last_action: Dict[str, str] = {}

    # =========================================================
    # PUBLIC INTERFACE
    # =========================================================
    def generate_vote(self, symbol: str, cache: Any) -> Dict[str, Any]:
        ts = time.time()

        # -----------------------------------------------------
        # 1) Symbol state (regime, tf policy, session)
        # -----------------------------------------------------
        sym_state = cache.get_symbol_state(symbol)
        if not sym_state:
            return self._hold(symbol, "Missing symbol_state")

        regime = sym_state.get("regime", {}).get("final_regime", "UNCLEAR")
        tf_policy = sym_state.get("tf_policy", {})
        session_mode = sym_state.get("session", {}).get("mode", "UNKNOWN")

        htf = tf_policy.get("bias_timeframe")
        ltf = tf_policy.get("entry_timeframe")

        if regime != "TRENDING":
            return self._hold(symbol, f"Regime {regime} not allowed")

        if not htf or not ltf:
            return self._hold(symbol, "Missing HTF/LTF policy")

        # -----------------------------------------------------
        # 2) Cooldown
        # -----------------------------------------------------
        last_ts = self._last_vote_ts.get(symbol, 0.0)
        if ts - last_ts < COOLDOWN_SEC:
            return self._hold(symbol, "Cooldown active")

        # -----------------------------------------------------
        # 3) Feature retrieval
        # -----------------------------------------------------
        tf_features = cache.get_features_by_tf(symbol)
        if not tf_features:
            return self._hold(symbol, "Missing TF features")

        htf_feat = tf_features.get(htf)
        ltf_feat = tf_features.get(ltf)

        if not htf_feat or not ltf_feat:
            return self._hold(symbol, "Missing HTF or LTF features")

        # -----------------------------------------------------
        # 4) HTF Bias (directional permission)
        # -----------------------------------------------------
        htf_trend_slope = float(htf_feat.get("trend_slope", 0.0))
        htf_trend_strength = float(htf_feat.get("trend_strength", 0.0))
        htf_efficiency = float(htf_feat.get("efficiency_ratio", 0.0))
        htf_momentum = float(htf_feat.get("momentum", 0.0))

        # Strong, directional bias only
        if htf_trend_strength < 0.55 or htf_efficiency < 0.45:
            return self._hold(symbol, "HTF bias weak")

        if htf_trend_slope > 0 and htf_momentum >= 0:
            htf_bias = 1
        elif htf_trend_slope < 0 and htf_momentum <= 0:
            htf_bias = -1
        else:
            return self._hold(symbol, "HTF bias mixed")

        # -----------------------------------------------------
        # 5) LTF Execution Filters
        # -----------------------------------------------------
        ltf_momentum = float(ltf_feat.get("momentum", 0.0))
        ltf_momentum_slope = float(ltf_feat.get("momentum_slope", 0.0))
        ltf_momentum_acc = float(ltf_feat.get("momentum_acceleration", 0.0))
        ltf_spike = bool(ltf_feat.get("momentum_spike_flag", False))
        ltf_exhaust = bool(ltf_feat.get("momentum_exhaustion_flag", False))

        ltf_vol_exp = float(ltf_feat.get("volatility_expansion", 0.0))
        ltf_range_exp = float(ltf_feat.get("candle_range_expansion", 0.0))

        ltf_volume_pressure = float(ltf_feat.get("volume_pressure", 0.0))
        ltf_volume_spike = float(ltf_feat.get("volume_spike_ratio", 0.0))

        ltf_structure = float(ltf_feat.get("structure_clarity", 0.0))
        ltf_wick = float(ltf_feat.get("wick_dominance", 0.0))
        ltf_equal_hl = bool(ltf_feat.get("equal_highs_lows", False))

        # Directional alignment
        if htf_bias == 1 and ltf_momentum <= 0:
            return self._hold(symbol, "LTF counter momentum")
        if htf_bias == -1 and ltf_momentum >= 0:
            return self._hold(symbol, "LTF counter momentum")

        # Execution quality gates
        if ltf_exhaust:
            return self._hold(symbol, "Momentum exhausted")

        if ltf_structure < 0.2:
            return self._hold(symbol, "Poor structure clarity")

        if ltf_vol_exp < 1.0 and ltf_range_exp < 1.0:
            return self._hold(symbol, "No expansion")

        # Liquidity sanity (avoid fake breaks)
        if ltf_wick > 2.5 and ltf_equal_hl:
            return self._hold(symbol, "Liquidity trap risk")

        # -----------------------------------------------------
        # 6) Confidence Construction (bounded, stable)
        # -----------------------------------------------------
        conf = 0.5

        conf += 0.15 * min(1.0, htf_trend_strength)
        conf += 0.10 * min(1.0, abs(htf_trend_slope) * 100)
        conf += 0.10 * min(1.0, abs(ltf_momentum_slope) * 50)

        if ltf_spike:
            conf += 0.05
        if ltf_volume_spike > 1.2:
            conf += 0.05
        if htf_bias * ltf_volume_pressure > 0:
            conf += 0.05

        conf = float(min(max(conf, 0.0), 1.0))

        # -----------------------------------------------------
        # 7) Action
        # -----------------------------------------------------
        action = "BUY" if htf_bias > 0 else "SELL"

        self._last_vote_ts[symbol] = ts
        self._last_action[symbol] = action

        return {
            "action": action,
            "confidence": conf,
            "reason": "HTF bias aligned with LTF execution",
            "meta": {
                "timestamp": ts,
                "htf_bias": "BULLISH" if htf_bias > 0 else "BEARISH",
                "htf_trend_strength": htf_trend_strength,
                "htf_efficiency": htf_efficiency,
                "ltf_structure": ltf_structure,
                "ltf_volatility_expansion": ltf_vol_exp,
                "session_mode": session_mode,
            },
        }

    # =========================================================
    # INTERNAL HOLD
    # =========================================================
    def _hold(self, symbol: str, reason: str) -> Dict[str, Any]:
        ts = time.time()
        self._last_vote_ts[symbol] = ts
        self._last_action[symbol] = "HOLD"
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reason": reason,
            "meta": {"timestamp": ts, "symbol": symbol},
      }

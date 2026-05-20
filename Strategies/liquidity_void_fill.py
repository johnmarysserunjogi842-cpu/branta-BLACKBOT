# Strategies/liquidity_void_fill.py
from __future__ import annotations
from typing import Dict, Any
import time
import math

COOLDOWN_SEC = 30  # per-symbol cooldown to prevent overtrading


class LiquidityVoidFill:
    """
    Institution-grade Liquidity Void Fill strategy.
    Exploits market inefficiencies / voids after displacement candles.
    Outputs a deterministic vote for StrategyVoter.
    """

    def __init__(self):
        # per-symbol last trade timestamp (BUY/SELL only)
        self._last_trade_ts: Dict[str, float] = {}
        self._last_action: Dict[str, str] = {}

    # ------------------------------
    # PUBLIC INTERFACE
    # ------------------------------
    def generate_vote(self, symbol: str, cache: Any) -> Dict[str, Any]:
        ts = time.time()

        # 1️⃣ Retrieve symbol state
        sym_state = cache.get_symbol_state(symbol)
        required_keys = ["regime", "tf_policy", "session"]
        for k in required_keys:
            if k not in sym_state:
                return self._hold_vote(symbol, reason=f"Missing symbol_state key: {k}")

        regime = sym_state["regime"].get("final_regime", "UNCLEAR")
        entry_tf = sym_state["tf_policy"].get("entry_timeframe", None)
        session_mode = sym_state["session"].get("mode", "UNKNOWN")

        # 2️⃣ Regime gating
        if regime not in ("VOLATILITY_EXPANSION", "TRENDING"):
            return self._hold_vote(symbol, reason=f"Regime {regime} not allowed")

        # 3️⃣ Timeframe features
        tf_features = cache.get_features_by_tf(symbol)
        if entry_tf not in tf_features:
            return self._hold_vote(symbol, reason=f"Missing TF features for {entry_tf}")
        feat = tf_features[entry_tf]

        # 4️⃣ Required feature keys
        required_feat_keys = [
            "displacement_flag", "displacement_strength", "displacement_direction",
            "inefficiency_flag", "inefficiency_score",
            "fvg_active_flag", "fvg_nearest_distance_atr",
            "momentum", "momentum_slope",
            "trend_slope", "trend_strength",
            "range_width", "atr", "volatility_expansion",
            "closes"
        ]
        for k in required_feat_keys:
            if k not in feat:
                return self._hold_vote(symbol, reason=f"Missing feature key: {k}")

        # 5️⃣ Cooldown check (ONLY for trades)
        last_trade_ts = self._last_trade_ts.get(symbol, 0.0)
        if ts - last_trade_ts < COOLDOWN_SEC:
            return self._hold_vote(symbol, reason="Cooldown active")

        displacement_flag = bool(feat["displacement_flag"])
        displacement_strength = float(feat["displacement_strength"])
        displacement_dir = int(feat["displacement_direction"])

        ineff_flag = bool(feat["inefficiency_flag"])
        ineff_score = float(feat["inefficiency_score"])

        fvg_active = bool(feat["fvg_active_flag"])
        fvg_dist_atr = float(feat["fvg_nearest_distance_atr"])

        momentum = float(feat["momentum"])
        trend_slope = float(feat["trend_slope"])

        range_width = float(feat["range_width"])
        vol_exp = float(feat["volatility_expansion"])

        # ------------------------------
        # 6️⃣ Void validity
        # ------------------------------
        void_valid = (
            displacement_flag
            and ineff_flag
            and range_width > 0.0
            and vol_exp > 1.0
        )

        if not void_valid:
            return self._hold_vote(symbol, reason="Void not valid")

        # Void strength (now uses inefficiency_score)
        void_strength = min(
            1.0,
            (displacement_strength * ineff_score) / (range_width + 1e-9)
        )

        # ------------------------------
        # 7️⃣ Fill logic
        # ------------------------------
        if fvg_active:
            fill_progress = max(0.3, min(0.7, 1.0 - fvg_dist_atr))
        else:
            fill_progress = 1.0  # void already filled

        # ------------------------------
        # 8️⃣ Direction & alignment
        # ------------------------------
        impulse_dir = 1 if displacement_dir > 0 else (-1 if displacement_dir < 0 else 0)
        trend_dir = 1 if trend_slope > 0 else (-1 if trend_slope < 0 else 0)
        momentum_dir = 1 if momentum > 0 else (-1 if momentum < 0 else 0)

        alignment_score = (
            (impulse_dir == trend_dir) * 0.6 +
            (impulse_dir == momentum_dir) * 0.4
        )

        # ------------------------------
        # 9️⃣ Confidence shaping
        # ------------------------------
        base_conf = void_strength * fill_progress
        base_conf *= (0.8 + alignment_score)

        # Penalize late fills
        if fill_progress >= 0.95:
            base_conf *= 0.6

        # Sigmoid normalization
        conf = 1.0 / (1.0 + math.exp(-12.0 * (base_conf - 0.5)))
        conf = float(min(max(conf, 0.0), 1.0))

        # Volatility-adjusted confidence cap
        vol_cap = min(1.0, 1.5 / max(vol_exp, 1e-9))
        conf *= vol_cap

        # ------------------------------
        # 🔟 Choose action
        # ------------------------------
        if impulse_dir > 0:
            action = "BUY"
        elif impulse_dir < 0:
            action = "SELL"
        else:
            action = "HOLD"

        # One-way continuation protection
        last_action = self._last_action.get(symbol)
        if last_action and last_action != action and conf < 0.75:
            return self._hold_vote(symbol, reason="Against prior impulse")

        # ------------------------------
        # 11️⃣ Meta
        # ------------------------------
        meta = {
            "timestamp": ts,
            "void_strength": void_strength,
            "fill_progress": fill_progress,
            "alignment_score": alignment_score,
            "regime": regime,
            "session_mode": session_mode,
            "fvg_active": fvg_active,
            "fvg_dist_atr": fvg_dist_atr,
            "confidence_raw": base_conf,
        }

        # ------------------------------
        # 12️⃣ Update state (ONLY for trades)
        # ------------------------------
        if action in ("BUY", "SELL"):
            self._last_trade_ts[symbol] = ts

        self._last_action[symbol] = action

        return {
            "action": action,
            "confidence": conf,
            "reason": "Liquidity void fill",
            "meta": meta,
        }

    # ------------------------------
    # INTERNAL HOLD HELPER
    # ------------------------------
    def _hold_vote(self, symbol: str, reason: str) -> Dict[str, Any]:
        ts = time.time()
        self._last_action[symbol] = "HOLD"
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reason": reason,
            "meta": {"timestamp": ts, "symbol": symbol},
        }

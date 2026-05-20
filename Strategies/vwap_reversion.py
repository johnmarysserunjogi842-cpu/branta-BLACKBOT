# Strategies/vwap_reversion.py
from __future__ import annotations
from typing import Dict, Any, Set
import math
import time
from strategy_manager.strategy_loader import BaseStrategy

# ============================================================
# VWAP Reversion Strategy (Institution-Grade)
# ------------------------------------------------------------
# Exploits: Institutional fair value reversion
# Regime: RANGING only
# Timeframe: M5 (example, can be customized)
#
# STRICT CONSTRAINTS:
# - DO NOT change input requirements (features/state)
# - DO NOT change output schema
# ============================================================

class VWAPReversion(BaseStrategy):
    # Unique name
    name: str = "VWAPReversion"

    # Allowed regimes
    allowed_regimes: Set[str] = {"RANGING"}

    # Required timeframes
    required_timeframes: Set[str] = {"M5"}

    # Required features per timeframe
    required_features: Dict[str, Set[str]] = {
        "M5": {"vwap", "price", "volume_declining", "range_width"}
    }

    # Required symbol-level state
    required_symbol_state: Set[str] = {"regime", "tf_policy", "session"}

    # ------------------------------------------------------------
    # CONFIDENCE + EXECUTION PARAMETERS (institution-grade)
    # ------------------------------------------------------------
    CONFIDENCE_DISTANCE_SCALE: float = 1.0  # multiplier for distance signal
    CONFIDENCE_VOLUME_WEIGHT: float = 0.5   # bonus weight if volume_declining True

    # --- Reversion bands (normalized units) ---
    # deadzone prevents micro-noise trading around VWAP
    DEADZONE: float = 0.02

    # entry threshold: must exceed deadzone + entry margin
    ENTRY_THRESHOLD: float = 0.06

    # extreme zone: confidence saturates faster
    EXTREME_THRESHOLD: float = 0.45

    # --- safety floors ---
    MIN_RANGE_WIDTH: float = 0.25  # absolute floor
    MIN_EFFECTIVE_DENOM: float = 1e-6

    # --- anti-chop / stability controls ---
    COOLDOWN_SEC: float = 20.0
    FLIP_FLOP_BLOCK_SEC: float = 30.0

    # if range_width is too tiny -> avoid (market too compressed)
    COMPRESSION_AVOID_RATIO: float = 0.15  # relative to vwap distance denom

    # ------------------------------------------------------------
    # INTERNAL STATE (per strategy instance)
    # ------------------------------------------------------------
    # Note: strategies are typically singleton instances inside StrategyManager.
    # We keep minimal state for cooldown + hysteresis.
    _last_action_ts: Dict[str, float] = {}
    _last_action_dir: Dict[str, str] = {}
    _last_signal_strength: Dict[str, float] = {}

    # ============================================================
    # HELPERS
    # ============================================================

    @staticmethod
    def _clamp(x: float, lo: float, hi: float) -> float:
        if x < lo:
            return lo
        if x > hi:
            return hi
        return x

    @staticmethod
    def _is_finite(x: Any) -> bool:
        try:
            return x is not None and math.isfinite(float(x))
        except Exception:
            return False

    @staticmethod
    def _safe_float(x: Any, default: float = 0.0) -> float:
        try:
            v = float(x)
            if not math.isfinite(v):
                return default
            return v
        except Exception:
            return default

    @staticmethod
    def _sigmoid(x: float) -> float:
        # stable sigmoid
        # maps (-inf..+inf) -> (0..1)
        if x >= 0:
            z = math.exp(-x)
            return 1.0 / (1.0 + z)
        else:
            z = math.exp(x)
            return z / (1.0 + z)

    def _cooldown_blocked(self, symbol: str, now: float) -> bool:
        ts = self._last_action_ts.get(symbol)
        if ts is None:
            return False
        return (now - ts) < self.COOLDOWN_SEC

    def _flipflop_blocked(self, symbol: str, action: str, now: float) -> bool:
        last_dir = self._last_action_dir.get(symbol)
        ts = self._last_action_ts.get(symbol)
        if not last_dir or ts is None:
            return False
        if action == "HOLD":
            return False
        # if direction flips too quickly, block
        if last_dir != action and (now - ts) < self.FLIP_FLOP_BLOCK_SEC:
            return True
        return False

    def _record_action(self, symbol: str, action: str, strength: float, now: float) -> None:
        if action in ("BUY", "SELL"):
            self._last_action_ts[symbol] = now
            self._last_action_dir[symbol] = action
            self._last_signal_strength[symbol] = strength

    # ============================================================
    # MAIN EVALUATION
    # ============================================================

    def evaluate(self, symbol: str, cache) -> Dict[str, Any]:
        """
        Return a vote payload for VWAP Reversion.
        Output schema is fixed:
          {action, confidence, reason, meta}
        """
        t0 = time.perf_counter()
        now = time.time()

        # --- Health gates ---
        sym_state = cache.get_symbol_state(symbol) or {}
        tf_features = cache.get_features_by_tf(symbol) or {}

        session = sym_state.get("session") or {}
        regime = (sym_state.get("regime") or {}).get("final_regime") or "UNCLEAR"
        entry_tf = (sym_state.get("tf_policy") or {}).get("entry_timeframe") or "M5"

        # Session block
        if session.get("mode") in {"NO_TRADE", "PAUSE", "HALT"}:
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": "Session blocked",
                "meta": {"latency_ms": int((time.perf_counter() - t0) * 1000)},
            }

        # Only act in allowed regime
        if regime != "RANGING":
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": f"Wrong regime ({regime})",
                "meta": {"latency_ms": int((time.perf_counter() - t0) * 1000)},
            }

        # --- Feature extraction ---
        tf_feat = tf_features.get(entry_tf)
        if not tf_feat:
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": f"Missing TF features ({entry_tf})",
                "meta": {"latency_ms": int((time.perf_counter() - t0) * 1000)},
            }

        vwap = tf_feat.get("vwap")
        price = tf_feat.get("price")
        volume_declining = bool(tf_feat.get("volume_declining", False))
        range_width = self._safe_float(tf_feat.get("range_width", 1.0), default=1.0)

        if not self._is_finite(vwap) or not self._is_finite(price):
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": "Missing/invalid price/VWAP",
                "meta": {"latency_ms": int((time.perf_counter() - t0) * 1000)},
            }

        vwap_f = float(vwap)
        price_f = float(price)

        # guard range_width
        if not math.isfinite(range_width):
            range_width = 1.0

        # absolute floor
        if range_width < self.MIN_RANGE_WIDTH:
            range_width = self.MIN_RANGE_WIDTH

        # ------------------------------------------------------------
        # Institutional-grade normalization
        # ------------------------------------------------------------
        # distance: positive means price above vwap (sell bias), negative means below (buy bias)
        distance = price_f - vwap_f

        # Use half-range scaling (same idea as before), but stable floors
        denom = max(0.5 * range_width, self.MIN_EFFECTIVE_DENOM)

        # compression avoid: if denom too small compared to abs distance, still ok;
        # but if range_width is too tiny overall, skip (micro-chop / illiquid / spread dominated)
        if (0.5 * range_width) < (self.COMPRESSION_AVOID_RATIO * max(abs(distance), 1e-6)):
            # This condition catches cases where range_width is suspiciously tiny relative to distance.
            # It indicates unreliable range_width or market compression.
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": "Compression/unstable range_width",
                "meta": {
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "distance_from_vwap": distance,
                    "range_width": range_width,
                    "entry_tf": entry_tf,
                },
            }

        normalized_distance = distance / denom

        # clamp -1..1 for stability
        normalized_distance = self._clamp(normalized_distance, -1.0, 1.0)

        abs_nd = abs(normalized_distance)

        # ------------------------------------------------------------
        # Deadzone + Entry threshold gating (noise rejection)
        # ------------------------------------------------------------
        # if too close to vwap -> do nothing
        if abs_nd <= self.DEADZONE:
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": f"VWAP deadzone abs_nd={abs_nd:.3f}",
                "meta": {
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "distance_from_vwap": distance,
                    "normalized_distance": normalized_distance,
                    "volume_declining": volume_declining,
                    "range_width": range_width,
                    "entry_tf": entry_tf,
                },
            }

        # must have volume confirmation for reversion (institutional filter)
        if not volume_declining:
            # Still report the signal in meta, but no trade vote.
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": "No volume decline confirmation",
                "meta": {
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "distance_from_vwap": distance,
                    "normalized_distance": normalized_distance,
                    "volume_declining": volume_declining,
                    "range_width": range_width,
                    "entry_tf": entry_tf,
                },
            }

        # Entry threshold: avoid weak signals even if outside deadzone
        if abs_nd < self.ENTRY_THRESHOLD:
            # not strong enough
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": f"Weak reversion signal abs_nd={abs_nd:.3f}",
                "meta": {
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "distance_from_vwap": distance,
                    "normalized_distance": normalized_distance,
                    "volume_declining": volume_declining,
                    "range_width": range_width,
                    "entry_tf": entry_tf,
                },
            }

        # ------------------------------------------------------------
        # Direction decision
        # ------------------------------------------------------------
        # price below vwap => BUY reversion
        if normalized_distance < 0.0:
            action = "BUY"
        else:
            action = "SELL"

        # ------------------------------------------------------------
        # Cooldown + flip-flop protection
        # ------------------------------------------------------------
        if self._cooldown_blocked(symbol, now):
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": "Cooldown active",
                "meta": {
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "distance_from_vwap": distance,
                    "normalized_distance": normalized_distance,
                    "volume_declining": volume_declining,
                    "range_width": range_width,
                    "entry_tf": entry_tf,
                },
            }

        if self._flipflop_blocked(symbol, action, now):
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": "Flip-flop blocked",
                "meta": {
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "distance_from_vwap": distance,
                    "normalized_distance": normalized_distance,
                    "volume_declining": volume_declining,
                    "range_width": range_width,
                    "entry_tf": entry_tf,
                },
            }

        # ------------------------------------------------------------
        # Confidence shaping (institution-grade)
        # ------------------------------------------------------------
        # We shape confidence to be:
        # - low near entry threshold
        # - rises quickly into extreme zone
        # - saturates smoothly to avoid always 1.0
        #
        # Use logistic on "signal strength"
        # Strength normalized: map ENTRY_THRESHOLD..EXTREME_THRESHOLD to 0..1
        strength_raw = (abs_nd - self.ENTRY_THRESHOLD) / max((self.EXTREME_THRESHOLD - self.ENTRY_THRESHOLD), 1e-6)
        strength_raw = self._clamp(strength_raw, 0.0, 1.0)

        # Nonlinear curve: sigmoid around mid
        # scale factor controls steepness
        conf_curve = self._sigmoid((strength_raw - 0.35) * 6.0)

        # distance contribution
        conf_distance = self._clamp(conf_curve * self.CONFIDENCE_DISTANCE_SCALE, 0.0, 1.0)

        # volume bonus (fixed, since only bool available)
        conf_volume = self.CONFIDENCE_VOLUME_WEIGHT

        confidence = self._clamp(conf_distance + conf_volume, 0.0, 1.0)

        # ------------------------------------------------------------
        # Record action for stability system
        # ------------------------------------------------------------
        self._record_action(symbol, action, strength_raw, now)

        # ------------------------------------------------------------
        # Compose reason & meta
        # ------------------------------------------------------------
        reason = (
            f"VWAPReversion: nd={normalized_distance:.3f}, "
            f"abs_nd={abs_nd:.3f}, vol_decl={volume_declining}, "
            f"strength={strength_raw:.2f}"
        )

        meta = {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "distance_from_vwap": distance,
            "normalized_distance": normalized_distance,
            "volume_declining": volume_declining,
            "range_width": range_width,
            "entry_tf": entry_tf,
            "deadzone": self.DEADZONE,
            "entry_threshold": self.ENTRY_THRESHOLD,
            "extreme_threshold": self.EXTREME_THRESHOLD,
            "cooldown_sec": self.COOLDOWN_SEC,
            "flipflop_block_sec": self.FLIP_FLOP_BLOCK_SEC,
            "strength_raw": strength_raw,
        }

        return {"action": action, "confidence": confidence, "reason": reason, "meta": meta}

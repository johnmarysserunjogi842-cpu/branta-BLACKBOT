#Strategies/trend_pullback_volatility.py
from __future__ import annotations

from typing import Dict, Any, Set
from strategy_manager.base_strategy import BaseStrategy


class TrendPullbackVolatility(BaseStrategy):
    """
    Institution-grade Trend Pullback + Volatility Expansion strategy.

    PURPOSE
    -------
    Trade continuation in TRENDING markets only, after:
    - Controlled pullback
    - Volatility compression
    - Volatility expansion in trend direction

    DESIGN PRINCIPLES
    -----------------
    - Context-first (never detects regime)
    - HOLD by default
    - Stateless and deterministic
    - Cache-only reads (O(1))
    - Conservative confidence (never dominates voting)
    """

    # ------------------------------------------------------------------
    # STRATEGY METADATA (STRICT CONTRACT)
    # ------------------------------------------------------------------

    name: str = "TrendPullbackVolatility"

    allowed_regimes: Set[str] = {"TRENDING"}

    # Entry timeframe decided by TimeframeEngine
    required_timeframes: Set[str] = set()

    # Required features on ENTRY timeframe only
    required_features: Dict[str, Set[str]] = {
        # entry timeframe (resolved dynamically)
        "*": {
            "trend_strength",
            "pullback_depth",
            "atr_ratio",
            "volatility_compression",
            "momentum_alignment",
        }
    }

    required_symbol_state: Set[str] = {
        "regime",
        "tf_policy",
        "session",
    }

    # ------------------------------------------------------------------
    # HARD LIMITS (INSTITUTIONAL SAFETY)
    # ------------------------------------------------------------------

    MIN_TREND_STRENGTH = 0.60
    MAX_PULLBACK_DEPTH = 0.55

    ATR_COMPRESSION_MAX = 0.90
    ATR_EXPANSION_MIN = 1.05

    MAX_CONFIDENCE = 0.45  # never dominates voter

    # ------------------------------------------------------------------
    # CORE EVALUATION
    # ------------------------------------------------------------------

    def evaluate(self, symbol: str, cache) -> Dict[str, Any]:
        """
        Produce a single vote for this symbol.

        Returns a HOLD vote unless ALL conditions align.
        Never raises. Never blocks. Never mutates state.
        """

        # -------------------- SYMBOL CONTEXT --------------------

        sym_state = cache.get_symbol_state(symbol) or {}
        regime = (sym_state.get("regime") or {}).get("final_regime")

        if regime != "TRENDING":
            return self._hold("regime_not_trending")

        session = sym_state.get("session") or {}
        if session.get("mode") in {"NO_TRADE", "PAUSE", "HALT"}:
            return self._hold("session_blocked")

        tf_policy = sym_state.get("tf_policy") or {}
        entry_tf = tf_policy.get("entry_timeframe")

        if not entry_tf:
            return self._hold("no_entry_timeframe")

        # -------------------- TIMEFRAME FEATURES --------------------

        tf_features_all = cache.get_features_by_tf(symbol) or {}
        features = tf_features_all.get(entry_tf)

        if not isinstance(features, dict):
            return self._hold("missing_features")

        # -------------------- REQUIRED FEATURES --------------------

        trend_strength = self._num(features.get("trend_strength"))
        pullback_depth = self._num(features.get("pullback_depth"))
        atr_ratio = self._num(features.get("atr_ratio"))
        vol_compress = self._bool(features.get("volatility_compression"))
        momentum_align = self._dir(features.get("momentum_alignment"))

        # -------------------- HARD FILTERS --------------------

        if trend_strength < self.MIN_TREND_STRENGTH:
            return self._hold("weak_trend")

        if pullback_depth <= 0.0 or pullback_depth > self.MAX_PULLBACK_DEPTH:
            return self._hold("invalid_pullback")

        if not vol_compress:
            return self._hold("no_volatility_compression")

        # ATR must be transitioning from compression → expansion
        if atr_ratio < self.ATR_EXPANSION_MIN:
            return self._hold("no_volatility_expansion")

        if momentum_align not in {"BUY", "SELL"}:
            return self._hold("momentum_not_aligned")

        # -------------------- CONFIDENCE MODEL --------------------

        confidence = self._compute_confidence(
            trend_strength=trend_strength,
            pullback_depth=pullback_depth,
            atr_ratio=atr_ratio,
        )

        if confidence <= 0.0:
            return self._hold("confidence_zero")

        # -------------------- FINAL VOTE --------------------

        return {
            "action": momentum_align,
            "confidence": confidence,
            "reason": "trend_pullback_volatility_expansion",
            "meta": {
                "entry_tf": entry_tf,
                "trend_strength": round(trend_strength, 3),
                "pullback_depth": round(pullback_depth, 3),
                "atr_ratio": round(atr_ratio, 3),
            },
        }

    # ------------------------------------------------------------------
    # CONFIDENCE LOGIC (DISCIPLINED, NON-AGGRESSIVE)
    # ------------------------------------------------------------------

    def _compute_confidence(
        self,
        *,
        trend_strength: float,
        pullback_depth: float,
        atr_ratio: float,
    ) -> float:
        """
        Conservative confidence scaling.
        Never exceeds MAX_CONFIDENCE.
        """

        score = 0.0

        # Trend quality (dominant factor)
        score += min(1.0, trend_strength) * 0.40

        # Pullback quality (shallower is better)
        score += max(0.0, 1.0 - pullback_depth) * 0.25

        # Volatility expansion quality
        score += min(1.0, (atr_ratio - 1.0)) * 0.35

        # Cap aggressively
        score = min(score, self.MAX_CONFIDENCE)

        # Normalize
        return round(max(0.0, min(1.0, score)), 4)

    # ------------------------------------------------------------------
    # SAFE HELPERS (ZERO EXCEPTIONS)
    # ------------------------------------------------------------------

    @staticmethod
    def _num(x: Any) -> float:
        try:
            return float(x)
        except Exception:
            return 0.0

    @staticmethod
    def _bool(x: Any) -> bool:
        return bool(x) is True

    @staticmethod
    def _dir(x: Any) -> str:
        if isinstance(x, str):
            x = x.upper()
            if x in {"BUY", "SELL"}:
                return x
        return "HOLD"

    @staticmethod
    def _hold(reason: str) -> Dict[str, Any]:
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reason": reason,
            "meta": {},
  }

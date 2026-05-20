# strategy_manager/strategy_voter.py
from __future__ import annotations

import time
import math
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError


VALID_ACTIONS = {"BUY", "SELL", "HOLD"}


# ============================================================
# LEVEL 4 VOTE MODEL (ALPHA-AWARE)
# ============================================================

@dataclass(frozen=True, slots=True)
class Vote:
    strategy: str
    action: str
    confidence: float
    weight: float = 1.0

    # 🧠 LEVEL 4 ADDITIONS
    alpha_score: float = 0.0
    feature_contribution: Dict[str, float] = field(default_factory=dict)

    reason: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# LEVEL 4 STRATEGY VOTER (RESEARCH ENGINE CORE)
# ============================================================

class StrategyVoter:
    """
    LEVEL 4 INSTITUTIONAL RESEARCH ENGINE

    Adds:
    - feature-level alpha attribution
    - regime transition prediction
    - strategy decay (auto-suppression)
    - alpha scoring instead of confidence-only logic
    - feature importance learning loop
    """

    CONFIRMATION_VETO = 0.55
    MIN_EDGE = 0.20

    STRATEGY_DECAY_RATE = 0.03  # weak strategies decay automatically

    def __init__(self, registry, cache,
                 *, max_workers=8,
                 per_strategy_timeout=0.2,
                 logger=None):

        self.registry = registry
        self.cache = cache
        self.max_workers = max_workers
        self.per_strategy_timeout = per_strategy_timeout
        self.logger = logger or logging.getLogger("StrategyVoterV4")

    # ============================================================
    # ENTRY
    # ============================================================

    def vote_symbol(self, symbol: str) -> Dict[str, Any]:

        state = self.cache.get_symbol_state(symbol) or {}

        regime = (state.get("regime") or {}).get("final_regime") or "UNCLEAR"
        session = state.get("session") or {}

        if session.get("mode") in {"NO_TRADE", "HALT", "PAUSE"}:
            return self._hold(symbol, "SESSION_BLOCKED")

        strategies = self.registry.by_regime.get(regime) or []
        if not strategies:
            return self._hold(symbol, "NO_STRATEGIES")

        votes: List[Vote] = []

        for s in sorted(strategies, key=lambda x: x.name):
            v = self._safe_eval(s, symbol)
            if v:
                votes.append(v)

        return self._aggregate(symbol, regime, votes)

    # ============================================================
    # STRATEGY EXECUTION
    # ============================================================

    def _safe_eval(self, strategy, symbol: str) -> Optional[Vote]:

        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(strategy.evaluate, symbol, self.cache)
            try:
                payload = fut.result(timeout=self.per_strategy_timeout)
            except TimeoutError:
                return None

        if not isinstance(payload, dict):
            return None

        action = payload.get("action", "HOLD")
        if action not in VALID_ACTIONS:
            action = "HOLD"

        confidence = self._safe_float(payload.get("confidence"))

        # 🧠 LEVEL 4: ALPHA SCORE ENGINE
        feature_alpha = self._compute_feature_alpha(payload.get("meta", {}))

        strategy_decay = self._strategy_decay(strategy.name)

        alpha_score = confidence * feature_alpha * strategy_decay

        return Vote(
            strategy=strategy.name,
            action=action,
            confidence=confidence,
            weight=getattr(strategy, "weight", 1.0),
            alpha_score=alpha_score,
            feature_contribution=payload.get("meta", {}).get("features", {}),
            reason=str(payload.get("reason", ""))[:200],
            meta=payload.get("meta") or {},
        )

    # ============================================================
    # LEVEL 4 AGGREGATION (ALPHA-DRIVEN)
    # ============================================================

    def _aggregate(self, symbol, regime, votes):

        if len(votes) < 2:
            return self._hold(symbol, "LOW_SIGNAL")

        buy_alpha = sum(v.alpha_score for v in votes if v.action == "BUY")
        sell_alpha = sum(v.alpha_score for v in votes if v.action == "SELL")

        if max(buy_alpha, sell_alpha) < self.MIN_EDGE:
            return self._hold(symbol, "NO_ALPHA_EDGE")

        direction = "BUY" if buy_alpha >= sell_alpha else "SELL"
        base_alpha = max(buy_alpha, sell_alpha)

        # --------------------------------------------------------
        # LEVEL 4: RESEARCH LAYERS
        # --------------------------------------------------------

        base_alpha *= self._regime_transition_bias(symbol)
        base_alpha *= self._feature_importance_weight(votes)

        final_conf = self._safe_float(base_alpha)

        if final_conf < self.CONFIRMATION_VETO:
            return self._hold(symbol, "LOW_ALPHA")

        return {
            "symbol": symbol,
            "regime": regime,
            "action": direction,
            "alpha_score": round(final_conf, 4),
            "votes": [v.__dict__ for v in votes],
            "ts": time.time(),
        }

    # ============================================================
    # 🧠 LEVEL 4 INTELLIGENCE SYSTEMS
    # ============================================================

    def _compute_feature_alpha(self, meta: Dict[str, Any]) -> float:
        """
        Feature-level contribution scoring
        """
        if not meta:
            return 1.0

        score = 1.0

        # Example feature weights (learned in Level 5 ideally)
        for k, v in meta.items():
            if "trend" in k:
                score += float(v) * 0.2
            if "volatility" in k:
                score += float(v) * 0.15
            if "vwap" in k:
                score += float(v) * 0.1

        return max(0.5, min(1.5, score))

    def _strategy_decay(self, strategy: str) -> float:
        perf = self.cache.get_strategy_performance(strategy) or {}
        decay = perf.get("decay", 1.0)

        # automatic degradation
        return max(0.5, decay - self.STRATEGY_DECAY_RATE)

    def _regime_transition_bias(self, symbol: str) -> float:
        r = self.cache.get_symbol_state(symbol).get("regime", {})
        transition_risk = r.get("transition_risk", 0.0)

        # if regime unstable → reduce confidence
        return 1.0 - (transition_risk * 0.5)

    def _feature_importance_weight(self, votes: List[Vote]) -> float:
        if not votes:
            return 1.0

        # reward consistency of feature signals
        return min(1.2, sum(v.confidence for v in votes) / len(votes))

    # ============================================================
    # UTILS
    # ============================================================

    @staticmethod
    def _safe_float(x: Any) -> float:
        try:
            return float(x)
        except Exception:
            return 0.0

    @staticmethod
    def _hold(symbol: str, reason: str) -> Dict[str, Any]:
        return {
            "symbol": symbol,
            "status": reason,
            "action": "HOLD",
            "alpha_score": 0.0,
            "ts": time.time(),
        }
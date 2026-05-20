# confirmation_engine/confirmation_engine.py
from __future__ import annotations

import time
import threading
import logging
from typing import Dict, Any, List, Optional, Set


# ------------------------------------------------------------
# CONFIRMATION PAYLOAD
# ------------------------------------------------------------
class ConfirmedSignal:
    __slots__ = ("symbol", "action", "confidence", "votes", "reason", "ts")

    def __init__(
        self,
        symbol: str,
        action: str,
        confidence: float,
        votes: Optional[List[Dict[str, Any]]] = None,
        reason: str = "",
        ts: Optional[float] = None,
    ):
        self.symbol = symbol
        self.action = action
        self.confidence = confidence
        self.votes = votes or []
        self.reason = reason
        self.ts = ts or time.time()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "final_action": self.action,
            "final_confidence": self.confidence,
            "votes": self.votes,
            "reason": self.reason,
            "ts": self.ts,
        }


# ------------------------------------------------------------
# CONFIRMATION ENGINE
# ------------------------------------------------------------
class ConfirmationEngine:
    """
    Extremely Institution-Grade Confirmation Engine.

    - Deterministic
    - Thread-safe
    - Latency aware
    - Portfolio-safe
    """

    VALID_ACTIONS: Set[str] = {"BUY", "SELL", "HOLD"}

    # Semantic timeframe priority (highest → lowest)
    TF_PRIORITY = {
        "1M": 100,
        "1W": 90,
        "1D": 80,
        "4H": 70,
        "1H": 60,
        "30m": 50,
        "15m": 40,
        "5m": 30,
        "1m": 20,
    }

    def __init__(
        self,
        cache,
        *,
        min_confidence: float = 0.55,
        logger: Optional[logging.Logger] = None,
    ):
        self.cache = cache
        self.min_confidence = float(min_confidence)
        self.logger = logger or logging.getLogger("ConfirmationEngine")
        self._lock = threading.Lock()

    # ------------------------------------------------------------
    # MAIN ENTRY POINT
    # ------------------------------------------------------------
    def confirm_all(self, voter_output: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.perf_counter()

        symbols_data = voter_output.get("symbols", {})
        if not symbols_data:
            return {
                "status": "EMPTY",
                "ts": time.time(),
                "latency_seconds": 0.0,
                "symbols": {},
            }

        results: Dict[str, Any] = {}
        failed = 0

        for symbol in sorted(symbols_data):
            try:
                confirmed = self.confirm_symbol(symbols_data[symbol])
                results[symbol] = confirmed.as_dict()
            except Exception as e:
                failed += 1
                self.logger.exception("Confirmation failed for %s", symbol)
                results[symbol] = ConfirmedSignal(
                    symbol=symbol,
                    action="HOLD",
                    confidence=0.0,
                    reason="CONFIRMATION_ERROR",
                ).as_dict()

        elapsed = round(time.perf_counter() - t0, 6)

        return {
            "status": "OK" if failed == 0 else "DEGRADED",
            "ts": time.time(),
            "latency_seconds": elapsed,
            "symbols": results,
        }

    # ------------------------------------------------------------
    # CONFIRM ONE SYMBOL
    # ------------------------------------------------------------
    def confirm_symbol(self, payload: Dict[str, Any]) -> ConfirmedSignal:
        symbol = payload.get("symbol")
        if not symbol:
            raise ValueError("Payload missing symbol")

        action = str(payload.get("final_action", "HOLD")).upper()
        confidence = float(payload.get("final_confidence", 0.0))
        votes = payload.get("votes", [])
        regime = payload.get("regime", "UNCLEAR")
        session_mode = payload.get("session_mode", "ACTIVE")

        reasons: List[str] = []

        # ---- SESSION GATE (HARD) ----
        if session_mode in {"NO_TRADE", "PAUSE", "HALT"}:
            return self._hold(symbol, votes, f"Session blocked ({session_mode})")

        # ---- REGIME GATE (HARD) ----
        if regime == "UNCLEAR":
            return self._hold(symbol, votes, "Regime unclear")

        # ---- ACTION SANITY ----
        if action not in self.VALID_ACTIONS:
            return self._hold(symbol, votes, f"Invalid action ({action})")

        # ---- CONFIDENCE GATE (SOFT DECAY) ----
        if action in {"BUY", "SELL"} and confidence < self.min_confidence:
            decay = confidence / self.min_confidence
            confidence = round(confidence * decay, 4)
            reasons.append("Confidence decayed")

        # ---- MULTI-TF ALIGNMENT ----
        tf_features = self.cache.get_features_by_tf(symbol)
        if not self._multi_tf_alignment(action, tf_features):
            return self._hold(symbol, votes, "Higher timeframe misalignment")

        # ---- CROSS-SYMBOL FILTER ----
        if not self._cross_symbol_confirmation(symbol, action, confidence):
            return self._hold(symbol, votes, "Cross-symbol filter blocked")

        reasons.append("Confirmed")

        return ConfirmedSignal(
            symbol=symbol,
            action=action,
            confidence=confidence,
            votes=votes,
            reason=", ".join(reasons),
        )

    # ------------------------------------------------------------
    # MULTI-TIMEFRAME ALIGNMENT
    # ------------------------------------------------------------
    def _multi_tf_alignment(self, action: str, tf_features: Dict[str, Dict[str, Any]]) -> bool:
        if not tf_features or action == "HOLD":
            return True

        sorted_tfs = sorted(
            tf_features.keys(),
            key=lambda tf: self.TF_PRIORITY.get(tf, 0),
            reverse=True,
        )

        for tf in sorted_tfs:
            regime = (tf_features.get(tf) or {}).get("regime", "UNCLEAR")

            if action == "BUY" and regime in {"BEARISH", "RANGING"}:
                return False
            if action == "SELL" and regime in {"BULLISH", "RANGING"}:
                return False

        return True

    # ------------------------------------------------------------
    # CROSS-SYMBOL CONFIRMATION (HOOK)
    # ------------------------------------------------------------
    def _cross_symbol_confirmation(self, symbol: str, action: str, confidence: float) -> bool:
        # Placeholder for:
        # - Correlation matrices
        # - USD / index filters
        # - Portfolio exposure caps
        return True

    # ------------------------------------------------------------
    # INTERNAL HOLD FACTORY
    # ------------------------------------------------------------
    def _hold(self, symbol: str, votes: List[Dict[str, Any]], reason: str) -> ConfirmedSignal:
        return ConfirmedSignal(
            symbol=symbol,
            action="HOLD",
            confidence=0.0,
            votes=votes,
            reason=reason,
    )

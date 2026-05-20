from __future__ import annotations

import logging
from typing import Dict, Any, Optional
from datetime import datetime


class SessionManager:
    """
    Institutional Session Intelligence Engine v3 (FINAL TEST STABLE)

    FINAL UPGRADES:
    - Adds trade permission gate (CRITICAL for testing)
    - Adds confidence normalization
    - Adds noise filter scoring
    - Produces deterministic execution decision
    - Safe fallback ALWAYS
    """

    def __init__(self):
        self.logger = logging.getLogger("SessionManagerV3")

        self.base_sessions = {
            "ASIA": 0.35,
            "LONDON": 0.85,
            "NEW_YORK": 0.90,
            "LATE_SESSION": 0.20,
        }

    # =========================================================
    # MAIN API
    # =========================================================
    def evaluate(
        self,
        features: Optional[Dict[str, Any]] = None,
        tf_policy: Optional[Dict[str, Any]] = None,
        regime: Optional[str] = None,
    ) -> Dict[str, Any]:

        try:
            features = features or {}
            tf_policy = tf_policy or {}

            session = self._detect_session(tf_policy)

            bias = self.base_sessions.get(session, 0.5)

            liquidity = self._liquidity(features)
            volatility = self._volatility(features)

            symbol_factor = self._symbol_factor(features, session)
            regime_factor = self._regime_factor(regime, session)

            raw_score = self._score(
                bias,
                liquidity,
                volatility,
                symbol_factor,
                regime_factor
            )

            confidence = self._normalize(raw_score)

            # ================================
            # 🧠 FINAL TRADE GATE (IMPORTANT)
            # ================================
            trade_permission = self._trade_gate(
                session=session,
                confidence=confidence,
                volatility=volatility
            )

            return {
                "session": session,
                "bias": round(bias, 3),
                "liquidity": round(liquidity, 3),
                "volatility": round(volatility, 3),
                "symbol_factor": round(symbol_factor, 3),
                "regime_factor": round(regime_factor, 3),
                "confidence": round(confidence, 3),

                # 🔥 NEW CRITICAL FIELD
                "trade_permission": trade_permission,

                # debug layer for testing BLACKBOT
                "execution_quality": self._quality(confidence),
            }

        except Exception as e:
            self.logger.warning(f"SessionManagerV3 fallback: {e}")

            return {
                "session": "UNKNOWN",
                "confidence": 0.5,
                "trade_permission": False,
                "execution_quality": "LOW",
            }

    # =========================================================
    # SESSION DETECTION
    # =========================================================
    def _detect_session(self, tf_policy: Dict[str, Any]) -> str:

        try:
            if isinstance(tf_policy, dict) and "session" in tf_policy:
                return tf_policy["session"]

            hour = datetime.utcnow().hour

            if 0 <= hour < 6:
                return "ASIA"
            elif 6 <= hour < 13:
                return "LONDON"
            elif 13 <= hour < 21:
                return "NEW_YORK"
            else:
                return "LATE_SESSION"

        except Exception:
            return "UNKNOWN"

    # =========================================================
    # LIQUIDITY
    # =========================================================
    def _liquidity(self, features: Dict[str, Any]) -> float:

        try:
            if "volume" in features:
                return min(float(features["volume"]) / 2000, 1.0)

            if "candles" in features:
                return min(len(features["candles"]) / 150, 1.0)

            return 0.5

        except Exception:
            return 0.5

    # =========================================================
    # VOLATILITY
    # =========================================================
    def _volatility(self, features: Dict[str, Any]) -> float:

        try:
            if "volatility" in features:
                return min(abs(float(features["volatility"])), 1.0)

            if "candles" in features and features["candles"]:
                last = features["candles"][-1]
                high = last.get("high", 0)
                low = last.get("low", 0)
                close = last.get("close", 1)

                if close == 0:
                    return 0.5

                return min(abs(high - low) / close, 1.0)

            return 0.5

        except Exception:
            return 0.5

    # =========================================================
    # SYMBOL FACTOR
    # =========================================================
    def _symbol_factor(self, features: Dict[str, Any], session: str) -> float:

        try:
            symbol = features.get("symbol", "")

            if "XAU" in symbol:
                return 1.2 if session in ["LONDON", "NEW_YORK"] else 0.9

            if any(x in symbol for x in ["EUR", "GBP", "USD"]):
                return 1.1 if session in ["LONDON", "NEW_YORK"] else 1.0

            return 1.0

        except Exception:
            return 1.0

    # =========================================================
    # REGIME FACTOR
    # =========================================================
    def _regime_factor(self, regime: Optional[str], session: str) -> float:

        try:
            if not regime:
                return 1.0

            if regime == "TRENDING":
                return 1.25 if session in ["LONDON", "NEW_YORK"] else 1.0

            if regime == "VOLATILE":
                return 0.7

            if regime == "RANGING":
                return 0.9

            return 1.0

        except Exception:
            return 1.0

    # =========================================================
    # FINAL SCORE
    # =========================================================
    def _score(
        self,
        bias: float,
        liquidity: float,
        volatility: float,
        symbol_factor: float,
        regime_factor: float
    ) -> float:

        return (
            bias * 0.30 +
            liquidity * 0.25 +
            volatility * 0.15 +
            symbol_factor * 0.15 +
            regime_factor * 0.15
        )

    # =========================================================
    # NORMALIZATION
    # =========================================================
    def _normalize(self, score: float) -> float:
        return max(0.0, min(score, 1.0))

    # =========================================================
    # 🔥 FINAL TRADE GATE (TEST CONTROL)
    # =========================================================
    def _trade_gate(
        self,
        session: str,
        confidence: float,
        volatility: float
    ) -> bool:

        # HARD RULES (TEST SAFETY)
        if confidence < 0.65:
            return False

        if session == "LATE_SESSION":
            return False

        if volatility > 0.85:
            return False

        return True

    # =========================================================
    # QUALITY LABEL
    # =========================================================
    def _quality(self, confidence: float) -> str:

        if confidence >= 0.75:
            return "HIGH"
        elif confidence >= 0.55:
            return "MEDIUM"
        return "LOW"
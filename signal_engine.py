from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

logger = logging.getLogger("signal_engine")
logger.setLevel(logging.INFO)


# ============================================================
# SIGNAL STRUCTURE
# ============================================================

@dataclass
class TradeSignal:
    symbol: str
    direction: str          # BUY | SELL | NONE
    confidence: float       # 0 - 100
    regime: str
    reason: str


# ============================================================
# SIGNAL ENGINE (UPGRADED)
# ============================================================

class SignalEngine:

    def __init__(self, min_confidence: float = 75.0):
        self.min_confidence = min_confidence

        # timeframe weighting (important fix)
        self.tf_weights = {
            "M1": 1,
            "M5": 2,
            "M15": 3,
            "H1": 5,
            "H4": 8,
            "D1": 10,
        }

    # --------------------------------------------------------
    # MAIN ANALYSIS
    # --------------------------------------------------------

    def analyze_symbol(
        self,
        symbol: str,
        market_state: Dict[str, Any],
    ) -> Optional[TradeSignal]:

        tf_data = market_state.get("timeframes", {})
        regime = market_state.get("regime", {}).get("final_regime", "UNKNOWN")

        bullish_score = 0.0
        bearish_score = 0.0
        max_score = 0.0
        reasons: List[str] = []

        # ----------------------------------------------------
        # REGIME FILTER (hard filter, not bonus)
        # ----------------------------------------------------
        if regime not in ["TRENDING", "BREAKOUT"]:
            return None

        reasons.append("REGIME_VALID")

        # ----------------------------------------------------
        # TIMEFRAME ANALYSIS (weighted)
        # ----------------------------------------------------
        for tf, data in tf_data.items():

            weight = self.tf_weights.get(tf, 1)
            max_score += weight * 10

            if data.get("bos_up_flag"):
                bullish_score += weight * 3

            if data.get("bos_down_flag"):
                bearish_score += weight * 3

            if data.get("displacement_flag"):
                bullish_score += weight * 1.5
                bearish_score += weight * 1.5
                reasons.append("DISPLACEMENT")

            if data.get("fvg_active_flag"):
                bullish_score += weight * 1
                bearish_score += weight * 1
                reasons.append("FVG")

            if data.get("liquidity_sweep_low_flag"):
                bullish_score += weight * 2

            if data.get("liquidity_sweep_high_flag"):
                bearish_score += weight * 2

        # ----------------------------------------------------
        # DIRECTION DECISION
        # ----------------------------------------------------

        if bullish_score > bearish_score:
            direction = "BUY"
            raw_score = bullish_score
            reasons.append("BULLISH_DOMINANCE")

        elif bearish_score > bullish_score:
            direction = "SELL"
            raw_score = bearish_score
            reasons.append("BEARISH_DOMINANCE")

        else:
            return None

        # ----------------------------------------------------
        # CONFIDENCE NORMALIZATION
        # ----------------------------------------------------

        confidence = (raw_score / max_score) * 100 if max_score > 0 else 0

        if confidence < self.min_confidence:
            return None

        signal = TradeSignal(
            symbol=symbol,
            direction=direction,
            confidence=round(min(confidence, 100), 2),
            regime=regime,
            reason=" | ".join(reasons),
        )

        logger.info(
            f"SIGNAL {symbol} | {direction} | conf={signal.confidence}"
        )

        return signal
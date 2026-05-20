#market_analyzer /instrument_ranker.py
import numpy as np
from typing import Dict, List


class InstrumentRanker:
    """
    Institutional-grade instrument ranking engine.

    Purpose:
    - Rank instruments by trade opportunity quality
    - Allocate attention & capital
    - Reduce strategy load
    """

    # --------------------------------------------------
    # INIT
    # --------------------------------------------------
    def __init__(self, max_instruments: int = 5):
        """
        max_instruments:
            Maximum instruments allowed to pass to strategies
        """
        self.max_instruments = max_instruments

    # ==================================================
    # PUBLIC API
    # ==================================================

    def rank(
        self,
        instruments: List[Dict],
        session_mode: str,
        timeframe_bias: Dict[str, float]
    ) -> List[Dict]:
        """
        Parameters
        ----------
        instruments : list of dict
            Output from FeatureExtractor + MarketRegime
        session_mode : str
            Provided by SessionManager
        timeframe_bias : dict
            Output from TimeframeEngine

        Returns
        -------
        List of ranked instruments (top N)
        """

        scored = []

        for inst in instruments:
            score, reasons = self._score_instrument(
                inst,
                session_mode,
                timeframe_bias
            )

            if score > 0:
                scored.append({
                    "symbol": inst["symbol"],
                    "score": round(score, 4),
                    "regime": inst["regime"],
                    "reasons": reasons,
                    "features": inst
                })

        # sort by descending score
        scored.sort(key=lambda x: x["score"], reverse=True)

        return scored[: self.max_instruments]

    # ==================================================
    # CORE SCORING LOGIC
    # ==================================================

    def _score_instrument(
        self,
        f: Dict,
        session_mode: str,
        timeframe_bias: Dict[str, float]
    ):
        """
        Composite institutional score
        """

        regime = f["regime"]
        reasons = []

        # -----------------------------
        # BASE QUALITY METRICS
        # -----------------------------
        trend_strength = f["trend_strength"]
        efficiency = f["efficiency_ratio"]
        vol_ratio = f["relative_volatility"]
        momentum = abs(f["momentum"])
        volume_pressure = abs(f["volume_pressure"])

        # -----------------------------
        # REGIME WEIGHTS
        # -----------------------------
        if regime == "TRENDING":
            weights = {
                "trend": 0.35,
                "efficiency": 0.25,
                "momentum": 0.20,
                "volatility": 0.10,
                "volume": 0.10
            }
        elif regime == "RANGING":
            weights = {
                "trend": 0.10,
                "efficiency": 0.35,
                "momentum": 0.15,
                "volatility": 0.20,
                "volume": 0.20
            }
        elif regime == "VOLATILE":
            weights = {
                "trend": 0.15,
                "efficiency": 0.10,
                "momentum": 0.30,
                "volatility": 0.35,
                "volume": 0.10
            }
        else:
            return 0.0, ["UNCLEAR regime"]

        # -----------------------------
        # SESSION ADJUSTMENT
        # -----------------------------
        session_multiplier = self._session_multiplier(session_mode)
        if session_multiplier < 0.5:
            reasons.append(f"Session suppression: {session_mode}")

        # -----------------------------
        # TIMEFRAME ALIGNMENT
        # -----------------------------
        tf_score = timeframe_bias.get(f["symbol"], 0.5)
        reasons.append(f"TF alignment: {round(tf_score,2)}")

        # -----------------------------
        # SCORE CONSTRUCTION
        # -----------------------------
        raw_score = (
            trend_strength * weights["trend"] +
            efficiency * weights["efficiency"] +
            momentum * weights["momentum"] +
            vol_ratio * weights["volatility"] +
            volume_pressure * weights["volume"]
        )

        final_score = raw_score * session_multiplier * tf_score

        # -----------------------------
        # EXPLAINABILITY
        # -----------------------------
        if trend_strength > 0.6:
            reasons.append("Strong structure")
        if efficiency > 0.5:
            reasons.append("High efficiency")
        if vol_ratio > 1.2:
            reasons.append("Elevated volatility")
        if volume_pressure > 0.2:
            reasons.append("Participation imbalance")

        return final_score, reasons

    # ==================================================
    # SESSION LOGIC
    # ==================================================

    def _session_multiplier(self, mode: str) -> float:
        """
        Controls capital intensity, NOT signals
        """
        return {
            "MAINTENANCE": 0.0,
            "STANDBY": 0.2,
            "DEFENSIVE": 0.5,
            "CONSERVATIVE": 0.7,
            "NORMAL": 1.0,
            "PREPARE": 1.1,
            "ATTACK": 1.3,
            "MAX_EFFICIENCY": 1.5
        }.get(mode, 0.8)

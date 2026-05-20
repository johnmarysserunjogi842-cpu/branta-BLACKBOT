#market_analyzer /market_regime.py
from typing import Dict, List
from collections import defaultdict


class MarketRegimeEngine:
    """
    Institution-grade market regime decision engine.

    - Consumes ONLY extracted features (no indicators, no candles)
    - Multi-timeframe weighted voting
    - Stability & confidence filtering
    - Deterministic and stateless
    """

    # --------------------------------------------------
    # CONFIGURATION (TUNABLE, BUT SAFE DEFAULTS)
    # --------------------------------------------------

    TIMEFRAME_WEIGHTS = {
        "H1": 0.40,
        "M15": 0.30,
        "M5": 0.20,
        "M1": 0.10,
    }

    TREND_THRESHOLD = 0.60
    RANGE_THRESHOLD = 0.60
    VOLATILITY_THRESHOLD = 1.30

    MIN_CONFIDENCE = 0.45
    STRONG_CONFIDENCE = 0.75

    # --------------------------------------------------
    # CORE API
    # --------------------------------------------------

    def evaluate(self, features_by_tf: Dict[str, Dict]) -> Dict:
        """
        Main entry point.

        features_by_tf = {
            "M5":  { extracted features },
            "M15": { extracted features },
            "H1":  { extracted features }
        }
        """

        votes = defaultdict(float)
        diagnostics = {}

        for tf, features in features_by_tf.items():
            weight = self.TIMEFRAME_WEIGHTS.get(tf, 0.0)
            if weight == 0.0:
                continue

            regime, confidence = self._classify_timeframe(features)
            votes[regime] += weight * confidence

            diagnostics[tf] = {
                "regime": regime,
                "confidence": confidence,
                "weight": weight
            }

        final_regime, final_confidence = self._resolve_votes(votes)
        dominant_tf = self._dominant_timeframe(diagnostics)

        stability = self._stability_score(votes)

        return {
            "final_regime": final_regime,
            "confidence": round(final_confidence, 3),
            "stability": round(stability, 3),
            "dominant_timeframe": dominant_tf,
            "timeframe_diagnostics": diagnostics
        }

    # --------------------------------------------------
    # TIMEFRAME CLASSIFICATION
    # --------------------------------------------------

    def _classify_timeframe(self, f: Dict) -> (str, float):
        """
        Converts raw features into a regime vote + confidence.
        """

        trend_score = f["trend_strength"] * f["efficiency_ratio"]
        chop_score = 1.0 - f["efficiency_ratio"]
        vol_score = f["relative_volatility"]

        # TRENDING
        if trend_score >= self.TREND_THRESHOLD:
            confidence = min(1.0, trend_score)
            return "TRENDING", confidence

        # RANGING
        if chop_score >= self.RANGE_THRESHOLD and vol_score < 1.0:
            confidence = min(1.0, chop_score)
            return "RANGING", confidence

        # VOLATILE
        if vol_score >= self.VOLATILITY_THRESHOLD:
            confidence = min(1.0, (vol_score - 1.0))
            return "VOLATILE", confidence

        return "UNCLEAR", 0.30

    # --------------------------------------------------
    # VOTE RESOLUTION
    # --------------------------------------------------

    def _resolve_votes(self, votes: Dict[str, float]) -> (str, float):
        if not votes:
            return "UNCLEAR", 0.0

        sorted_votes = sorted(votes.items(), key=lambda x: x[1], reverse=True)
        winner, score = sorted_votes[0]

        if score < self.MIN_CONFIDENCE:
            return "UNCLEAR", score

        return winner, score

    # --------------------------------------------------
    # DOMINANT TIMEFRAME
    # --------------------------------------------------

    def _dominant_timeframe(self, diagnostics: Dict) -> str:
        best_tf = None
        best_score = 0.0

        for tf, data in diagnostics.items():
            score = data["confidence"] * data["weight"]
            if score > best_score:
                best_score = score
                best_tf = tf

        return best_tf

    # --------------------------------------------------
    # STABILITY METRIC
    # --------------------------------------------------

    def _stability_score(self, votes: Dict[str, float]) -> float:
        """
        Measures regime dominance vs noise.
        High = stable regime
        Low  = mixed or choppy environment
        """
        if not votes:
            return 0.0

        total = sum(votes.values())
        if total == 0:
            return 0.0

        dominant = max(votes.values())
        return dominant / total

from __future__ import annotations

import logging
import time
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("SELF_HEALING_SUPERVISOR")


# ============================================================
# COMPONENT HEALTH STATE
# ============================================================

@dataclass
class ComponentState:
    name: str
    failures: int = 0
    successes: int = 0
    last_failure: float = 0.0
    last_success: float = 0.0
    isolated: bool = False
    recovery_attempts: int = 0
    trust_score: float = 1.0  # 0.0 = dead, 1.0 = healthy


# ============================================================
# AUTONOMOUS SUPERVISOR
# ============================================================

class SelfHealingCore:
    """
    Autonomous system supervisor:

    - detects failure patterns
    - isolates unstable modules
    - triggers recovery actions
    - adapts trust score dynamically
    """

    def __init__(self):
        self.components: Dict[str, ComponentState] = {}
        self.fallback_registry: Dict[str, Callable] = {}
        self.predictor = PredictiveFailureEngine()

        # thresholds
        self.isolation_threshold = 3
        self.recovery_threshold = 5

    # ========================================================
    # INTERNAL STATE
    # ========================================================

    def _state(self, name: str) -> ComponentState:
        if name not in self.components:
            self.components[name] = ComponentState(name)
        return self.components[name]

    # ========================================================
    # REGISTRATION
    # ========================================================

    def register_fallback(self, name: str, fn: Callable):
        self.fallback_registry[name] = fn

    # ========================================================
    # EVENT TRACKING
    # ========================================================
    def record_failure(self, name: str):
        s = self._state(name)

        s.failures += 1
        s.last_failure = time.time()
        s.trust_score *= 0.85

        # 🔥 PREDICTIVE TRACKING
        self.predictor.record_failure_event(name)

        logger.warning(f"[HEAL] FAILURE {name} | trust={s.trust_score:.2f}")

        self._evaluate_state(s)

    def record_success(self, name: str):
        s = self._state(name)

        s.successes += 1
        s.last_success = time.time()
        s.trust_score = min(1.0, s.trust_score + 0.05)

    # ========================================================
    # DECISION ENGINE (CORE OF AUTONOMY)
    # ========================================================

    def should_isolate(self, name: str) -> bool:
        s = self._state(name)
        return s.failures >= self.isolation_threshold or s.trust_score < 0.3

    def should_recover(self, name: str) -> bool:
        s = self._state(name)
        return s.failures >= self.recovery_threshold and not s.isolated
    
    def should_preemptively_isolate(self, name: str) -> bool:
        risk = self.predictor.risk_score(name)
        return risk >= 0.75

    # ========================================================
    # ISOLATION + RECOVERY
    # ========================================================

    def isolate(self, name: str):
        s = self._state(name)
        s.isolated = True
        logger.error(f"[HEAL] ISOLATING {name}")

    def recover(self, name: str):
        s = self._state(name)

        s.recovery_attempts += 1
        s.failures = 0
        s.isolated = False
        s.trust_score = 0.6  # partial reset

        logger.info(f"[HEAL] RECOVERED {name} | attempt={s.recovery_attempts}")

        fallback = self.fallback_registry.get(name)
        if fallback:
            try:
                fallback()
            except Exception as e:
                logger.critical(f"[HEAL] fallback failed for {name}: {e}")

    # ========================================================
    # AUTONOMOUS EVALUATION ENGINE
    # ========================================================

    def _evaluate_state(self, s: ComponentState):

        # isolate if unstable
        if s.failures >= self.isolation_threshold:
            if not s.isolated:
                self.isolate(s.name)

        # auto recovery trigger
        if s.failures >= self.recovery_threshold:
            self.recover(s.name)

    # ========================================================
    # SAFE EXECUTION WRAPPER
    # ========================================================

    def safe_call(self, name: str, fn: Callable, *args, **kwargs) -> Any:
        try:
            result = fn(*args, **kwargs)
            self.record_success(name)
            return result

        except Exception as e:
            logger.error(f"[HEAL] {name} crashed: {e}")
            self.record_failure(name)

            fallback = self.fallback_registry.get(name)
            if fallback:
                try:
                    return fallback(*args, **kwargs)
                except Exception as fe:
                    logger.critical(f"[HEAL] fallback failed {name}: {fe}")

            return None


class PredictiveFailureEngine:
    """
    Predicts failure BEFORE it happens using trend signals.
    """

    def __init__(self):
        self.latency_history = {}   # name -> deque
        self.failure_history = {}   # name -> deque

    # ========================================================
    # TRACK METRICS
    # ========================================================

    def record_latency(self, name: str, value: float):
        if name not in self.latency_history:
            self.latency_history[name] = deque(maxlen=20)

        self.latency_history[name].append(value)

    def record_failure_event(self, name: str):
        if name not in self.failure_history:
            self.failure_history[name] = deque(maxlen=20)

        self.failure_history[name].append(time.time())

    # ========================================================
    # RISK SCORE (CORE AI FUNCTION)
    # ========================================================

    def risk_score(self, name: str) -> float:
        """
        Returns 0.0 (safe) → 1.0 (about to fail)
        """

        score = 0.0

        # ----------------------------
        # latency instability
        # ----------------------------
        lat = self.latency_history.get(name, [])
        if len(lat) >= 5:
            score += min(0.4, statistics.pstdev(lat) * 2)

        # ----------------------------
        # failure frequency
        # ----------------------------
        failures = self.failure_history.get(name, [])
        if len(failures) >= 2:
            intervals = [
                t2 - t1 for t1, t2 in zip(failures, list(failures)[1:])
            ]
            if intervals:
                avg_interval = sum(intervals) / len(intervals)
                if avg_interval < 5:
                    score += 0.4

        # ----------------------------
        # burst detection
        # ----------------------------
        if len(failures) >= 3:
            recent = failures[-3:]
            if recent[-1] - recent[0] < 10:
                score += 0.3

        return min(1.0, score)
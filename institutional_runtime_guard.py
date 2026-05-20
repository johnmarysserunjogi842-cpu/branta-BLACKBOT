"""
institutional_runtime_guard.py

Institution-grade centralized runtime governance layer for BLACKBOT.

MISSION
-------
This module centralizes:

- Broker resilience
- Market data integrity
- Runtime governance
- Retry behavior
- Async stability
- Execution firewalling
- Broker capability caching
- Latency monitoring
- Shared market snapshots
- Circuit breaking
- Duplicate execution protection
- Emergency runtime shutdown

DESIGN GOALS
------------
1. MINIMAL integration edits
2. MAXIMUM runtime stability
3. LOW latency overhead
4. HIGH MT5 survivability
5. THREAD + ASYNC safe
6. FAST hot-path execution
7. CENTRALIZED infrastructure governance

INTEGRATION POINTS
------------------
1. pipeline_orchestrator.py
2. execution_controller.py
3. trade_executor.py

NO strategy rewrites required.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple


# ============================================================
# LOGGER
# ============================================================

logger = logging.getLogger("institutional_runtime_guard")

if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================
# ENUMS
# ============================================================

class RuntimeState(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    EXECUTION_DISABLED = "EXECUTION_DISABLED"
    BROKER_UNSTABLE = "BROKER_UNSTABLE"
    MARKET_DATA_STALE = "MARKET_DATA_STALE"
    EMERGENCY_LOCKDOWN = "EMERGENCY_LOCKDOWN"


class ValidationResult(Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DEGRADED = "DEGRADED"


class RetryAction(Enum):
    RETRY_FAST = "RETRY_FAST"
    RETRY_SLOW = "RETRY_SLOW"
    FAIL = "FAIL"
    DISABLE_SYMBOL = "DISABLE_SYMBOL"
    DISABLE_EXECUTION = "DISABLE_EXECUTION"


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass(slots=True)
class SymbolCapabilities:
    symbol: str
    min_lot: float = 0.01
    volume_step: float = 0.01
    stop_level: int = 0
    freeze_level: int = 0
    fill_mode: str = "IOC"
    spread_float: bool = True
    trade_allowed: bool = True
    digits: int = 5
    contract_size: int = 100000
    leverage: int = 100
    last_refresh: float = field(default_factory=time.time)


@dataclass(slots=True)
class TickSnapshot:
    symbol: str
    bid: float
    ask: float
    spread: float
    ts: float


@dataclass(slots=True)
class MarketSnapshot:
    symbol: str
    spread: float = 0.0
    atr_ratio: float = 0.0
    liquidity_score: float = 0.0
    volatility_score: float = 0.0
    efficiency_ratio: float = 0.0
    session_state: str = "UNKNOWN"
    broker_state: str = "UNKNOWN"
    tick_freshness_ms: float = 0.0
    feed_healthy: bool = True
    last_update: float = field(default_factory=time.time)


@dataclass(slots=True)
class ExecutionIntent:
    symbol: str
    side: str
    volume: float
    stop_loss: float
    take_profit: float
    confidence: float
    strategy: str
    ts: float = field(default_factory=time.time)


@dataclass(slots=True)
class ExecutionValidation:
    allowed: bool
    reason: str
    runtime_state: RuntimeState
    latency_ms: float = 0.0


@dataclass(slots=True)
class RetryPolicy:
    max_retries: int
    base_delay: float
    max_delay: float
    exponential: bool = True
    jitter: bool = True


@dataclass(slots=True)
class LatencyMetrics:
    broker_latency_ms: float = 0.0
    execution_latency_ms: float = 0.0
    validation_latency_ms: float = 0.0
    queue_latency_ms: float = 0.0
    avg_broker_latency_ms: float = 0.0
    avg_execution_latency_ms: float = 0.0


# ============================================================
# HIGH PERFORMANCE TTL CACHE
# ============================================================

class TTLCache:
    """
    Ultra-fast lock-minimized TTL cache.
    """

    __slots__ = (
        "_store",
        "_ttl",
        "_lock",
    )

    def __init__(self, ttl_seconds: float = 300.0):
        self._store: Dict[str, Tuple[Any, float]] = {}
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)

        if item is None:
            return None

        value, expiry = item

        if expiry < time.time():
            with self._lock:
                self._store.pop(key, None)
            return None

        return value

    def set(self, key: str, value: Any) -> None:
        expiry = time.time() + self._ttl
        with self._lock:
            self._store[key] = (value, expiry)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# ============================================================
# BROKER CAPABILITY CACHE
# ============================================================

class BrokerCapabilityCache:
    """
    Centralized MT5 metadata cache.

    Prevents repeated symbol_info() calls.
    Massive latency reduction.
    """

    def __init__(self):
        self._cache = TTLCache(ttl_seconds=900)
        self._refresh_lock = threading.Lock()

    def get(self, symbol: str) -> Optional[SymbolCapabilities]:
        return self._cache.get(symbol)

    def set(self, symbol: str, caps: SymbolCapabilities) -> None:
        self._cache.set(symbol, caps)

    def preload(
        self,
        symbol: str,
        broker_fetcher: Callable[[str], Dict[str, Any]],
    ) -> SymbolCapabilities:

        cached = self.get(symbol)
        if cached:
            return cached

        with self._refresh_lock:
            cached = self.get(symbol)
            if cached:
                return cached

            raw = broker_fetcher(symbol)

            caps = SymbolCapabilities(
                symbol=symbol,
                min_lot=float(raw.get("min_lot", 0.01)),
                volume_step=float(raw.get("volume_step", 0.01)),
                stop_level=int(raw.get("stop_level", 0)),
                freeze_level=int(raw.get("freeze_level", 0)),
                fill_mode=str(raw.get("fill_mode", "IOC")),
                spread_float=bool(raw.get("spread_float", True)),
                trade_allowed=bool(raw.get("trade_allowed", True)),
                digits=int(raw.get("digits", 5)),
                contract_size=int(raw.get("contract_size", 100000)),
                leverage=int(raw.get("leverage", 100)),
            )

            self.set(symbol, caps)
            return caps


# ============================================================
# MARKET DATA INTEGRITY ENGINE
# ============================================================

class MarketIntegrityEngine:
    """
    Centralized market data validator.
    """

    def __init__(self):
        self._last_tick_ts: Dict[str, float] = {}
        self._last_candle_ts: Dict[str, float] = {}
        self._duplicate_ticks: Dict[str, int] = {}

    @staticmethod
    def _is_nan(x: Any) -> bool:
        try:
            return math.isnan(float(x))
        except Exception:
            return True

    def validate_tick(self, symbol: str, tick: Dict[str, Any]) -> bool:
        now = time.time()

        bid = tick.get("bid")
        ask = tick.get("ask")
        ts = float(tick.get("timestamp", now))

        if bid is None or ask is None:
            return False

        if self._is_nan(bid) or self._is_nan(ask):
            return False

        if bid <= 0 or ask <= 0:
            return False

        if bid > ask:
            return False

        if ts > now + 2:
            return False

        last_ts = self._last_tick_ts.get(symbol)

        if last_ts and ts < last_ts:
            logger.warning("Timestamp rollback detected | %s", symbol)
            return False

        spread = ask - bid

        if spread <= 0:
            return False

        if spread > (bid * 0.02):
            logger.warning("Abnormal spread spike | %s", symbol)
            return False

        self._last_tick_ts[symbol] = ts
        return True

    def validate_candle(
        self,
        symbol: str,
        candle: Dict[str, Any],
    ) -> bool:

        try:
            o = float(candle["open"])
            h = float(candle["high"])
            l = float(candle["low"])
            c = float(candle["close"])
            v = float(candle.get("volume", 0))
        except Exception:
            return False

        if any(self._is_nan(x) for x in [o, h, l, c]):
            return False

        if h < l:
            return False

        if c > h or c < l:
            return False

        if o > h or o < l:
            return False

        if v < 0:
            return False

        return True

    def validate_market_freshness(
        self,
        symbol: str,
        max_staleness_seconds: float = 5.0,
    ) -> bool:

        ts = self._last_tick_ts.get(symbol)

        if ts is None:
            return False

        return (time.time() - ts) <= max_staleness_seconds


# ============================================================
# EXECUTION DEDUPLICATION ENGINE
# ============================================================

class ExecutionDeduplicator:
    """
    Prevent duplicate fills from async races.
    """

    def __init__(self, ttl_seconds: float = 3.0):
        self._cache = TTLCache(ttl_seconds)

    @staticmethod
    def fingerprint(intent: ExecutionIntent) -> str:
        payload = (
            f"{intent.symbol}|"
            f"{intent.side}|"
            f"{round(intent.volume, 4)}|"
            f"{int(intent.ts)}"
        )

        return hashlib.sha256(payload.encode()).hexdigest()

    def seen(self, intent: ExecutionIntent) -> bool:
        fp = self.fingerprint(intent)

        if self._cache.get(fp):
            return True

        self._cache.set(fp, True)
        return False


# ============================================================
# BROKER ERROR CLASSIFIER
# ============================================================

class BrokerErrorClassifier:

    ERROR_ACTIONS = {
        10004: RetryAction.RETRY_FAST,
        10006: RetryAction.DISABLE_EXECUTION,
        10016: RetryAction.FAIL,
        10018: RetryAction.DISABLE_SYMBOL,
        10024: RetryAction.RETRY_SLOW,
    }

    def classify(self, error_code: int) -> RetryAction:
        return self.ERROR_ACTIONS.get(error_code, RetryAction.FAIL)


# ============================================================
# SMART RETRY ENGINE
# ============================================================

class SmartRetryEngine:

    def __init__(self):
        self.fast_policy = RetryPolicy(
            max_retries=3,
            base_delay=0.08,
            max_delay=1.0,
        )

        self.slow_policy = RetryPolicy(
            max_retries=6,
            base_delay=0.5,
            max_delay=8.0,
        )

    @staticmethod
    def _compute_delay(
        attempt: int,
        policy: RetryPolicy,
    ) -> float:

        if policy.exponential:
            delay = policy.base_delay * (2 ** attempt)
        else:
            delay = policy.base_delay

        delay = min(delay, policy.max_delay)

        if policy.jitter:
            delay += random.uniform(0.0, delay * 0.2)

        return delay

    async def retry_async(
        self,
        fn: Callable,
        action: RetryAction,
    ) -> Any:

        if action == RetryAction.FAIL:
            return await fn()

        policy = (
            self.fast_policy
            if action == RetryAction.RETRY_FAST
            else self.slow_policy
        )

        last_error = None

        for attempt in range(policy.max_retries):
            try:
                return await fn()
            except Exception as e:
                last_error = e
                delay = self._compute_delay(attempt, policy)
                await asyncio.sleep(delay)

        raise last_error


# ============================================================
# ASYNC GOVERNOR
# ============================================================

class AsyncGovernor:
    """
    Prevent task explosions and async starvation.
    """

    def __init__(
        self,
        max_concurrent_tasks: int = 64,
        max_queue_pressure: int = 1000,
    ):

        self.execution_sem = asyncio.Semaphore(max_concurrent_tasks)
        self.market_data_sem = asyncio.Semaphore(max_concurrent_tasks)

        self.max_queue_pressure = max_queue_pressure

        self._queue_sizes: Dict[str, int] = {}
        self._degraded_mode = False

    def register_queue_pressure(self, queue_name: str, size: int) -> None:
        self._queue_sizes[queue_name] = size

        total = sum(self._queue_sizes.values())

        self._degraded_mode = total > self.max_queue_pressure

    @property
    def degraded(self) -> bool:
        return self._degraded_mode


# ============================================================
# LATENCY TRACKER
# ============================================================

class LatencyTracker:

    def __init__(self):
        self.metrics = LatencyMetrics()
        self._broker_latencies: Deque[float] = deque(maxlen=500)
        self._exec_latencies: Deque[float] = deque(maxlen=500)

    def record_broker_latency(self, latency_ms: float) -> None:
        self._broker_latencies.append(latency_ms)

        self.metrics.avg_broker_latency_ms = (
            sum(self._broker_latencies) / len(self._broker_latencies)
        )

    def record_execution_latency(self, latency_ms: float) -> None:
        self._exec_latencies.append(latency_ms)

        self.metrics.avg_execution_latency_ms = (
            sum(self._exec_latencies) / len(self._exec_latencies)
        )


# ============================================================
# EXECUTION FIREWALL
# ============================================================

class ExecutionFirewall:

    def __init__(self):
        self._symbol_cooldowns: Dict[str, float] = {}
        self._disabled_symbols: Set[str] = set()

    def suspend_symbol(self, symbol: str) -> None:
        self._disabled_symbols.add(symbol)

    def resume_symbol(self, symbol: str) -> None:
        self._disabled_symbols.discard(symbol)

    def validate(
        self,
        intent: ExecutionIntent,
        runtime_state: RuntimeState,
        snapshot: Optional[MarketSnapshot],
        caps: Optional[SymbolCapabilities],
    ) -> ExecutionValidation:

        t0 = time.perf_counter()

        if runtime_state == RuntimeState.EMERGENCY_LOCKDOWN:
            return ExecutionValidation(
                False,
                "Emergency lockdown active",
                runtime_state,
            )

        if intent.symbol in self._disabled_symbols:
            return ExecutionValidation(
                False,
                "Symbol suspended",
                runtime_state,
            )

        if runtime_state in {
            RuntimeState.EXECUTION_DISABLED,
            RuntimeState.BROKER_UNSTABLE,
        }:
            return ExecutionValidation(
                False,
                "Execution disabled by runtime state",
                runtime_state,
            )

        if snapshot:
            if not snapshot.feed_healthy:
                return ExecutionValidation(
                    False,
                    "Feed unhealthy",
                    runtime_state,
                )

            if snapshot.tick_freshness_ms > 4000:
                return ExecutionValidation(
                    False,
                    "Market data stale",
                    runtime_state,
                )

            if snapshot.spread > 0.01:
                return ExecutionValidation(
                    False,
                    "Spread too large",
                    runtime_state,
                )

        if caps:
            if not caps.trade_allowed:
                return ExecutionValidation(
                    False,
                    "Broker trading disabled",
                    runtime_state,
                )

            if intent.volume < caps.min_lot:
                return ExecutionValidation(
                    False,
                    "Volume below minimum lot",
                    runtime_state,
                )

        latency = (time.perf_counter() - t0) * 1000

        return ExecutionValidation(
            True,
            "Accepted",
            runtime_state,
            latency_ms=latency,
        )


# ============================================================
# INSTITUTIONAL RUNTIME GUARD
# ============================================================

class InstitutionalRuntimeGuard:

    def __init__(self):

        self.runtime_state = RuntimeState.HEALTHY

        self.integrity_engine = MarketIntegrityEngine()
        self.capability_cache = BrokerCapabilityCache()
        self.retry_engine = SmartRetryEngine()
        self.error_classifier = BrokerErrorClassifier()
        self.execution_firewall = ExecutionFirewall()
        self.async_governor = AsyncGovernor()
        self.latency_tracker = LatencyTracker()
        self.deduplicator = ExecutionDeduplicator()

        self.market_snapshots: Dict[str, MarketSnapshot] = {}

        self._runtime_lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=8)

        self._broker_error_count = 0
        self._market_data_failures = 0

        self._health_history: Deque[Tuple[float, RuntimeState]] = deque(maxlen=1000)

    # ========================================================
    # RUNTIME STATE MANAGEMENT
    # ========================================================

    def set_runtime_state(self, state: RuntimeState) -> None:
        with self._runtime_lock:
            if state != self.runtime_state:
                logger.warning(
                    "Runtime state transition | %s -> %s",
                    self.runtime_state.value,
                    state.value,
                )

            self.runtime_state = state
            self._health_history.append((time.time(), state))

    # ========================================================
    # MARKET DATA VALIDATION
    # ========================================================

    def validate_market_data(
        self,
        symbol: str,
        candles: List[Dict[str, Any]],
    ) -> bool:

        if not candles:
            self._market_data_failures += 1
            return False

        for candle in candles:
            if not self.integrity_engine.validate_candle(symbol, candle):
                logger.warning("Invalid candle detected | %s", symbol)
                self._market_data_failures += 1
                return False

        if not self.integrity_engine.validate_market_freshness(symbol):
            self.set_runtime_state(RuntimeState.MARKET_DATA_STALE)
            return False

        return True

    # ========================================================
    # SHARED MARKET SNAPSHOTS
    # ========================================================

    def update_market_snapshot(
        self,
        symbol: str,
        **kwargs,
    ) -> None:

        snap = self.market_snapshots.get(symbol)

        if snap is None:
            snap = MarketSnapshot(symbol=symbol)
            self.market_snapshots[symbol] = snap

        for k, v in kwargs.items():
            if hasattr(snap, k):
                setattr(snap, k, v)

        snap.last_update = time.time()

    def get_market_snapshot(
        self,
        symbol: str,
    ) -> Optional[MarketSnapshot]:
        return self.market_snapshots.get(symbol)

    # ========================================================
    # EXECUTION VALIDATION
    # ========================================================

    def validate_execution(
        self,
        intent: ExecutionIntent,
    ) -> ExecutionValidation:

        if self.deduplicator.seen(intent):
            return ExecutionValidation(
                False,
                "Duplicate execution blocked",
                self.runtime_state,
            )

        snapshot = self.market_snapshots.get(intent.symbol)
        caps = self.capability_cache.get(intent.symbol)

        result = self.execution_firewall.validate(
            intent,
            self.runtime_state,
            snapshot,
            caps,
        )

        self.latency_tracker.record_execution_latency(result.latency_ms)

        return result

    # ========================================================
    # EXECUTION PROTECTION WRAPPER
    # ========================================================

    async def execute_with_protection(
        self,
        execution_fn: Callable,
        *args,
        **kwargs,
    ) -> Any:

        async with self.async_governor.execution_sem:

            start = time.perf_counter()

            try:
                result = await execution_fn(*args, **kwargs)

                latency_ms = (time.perf_counter() - start) * 1000
                self.latency_tracker.record_broker_latency(latency_ms)

                if latency_ms > 1500:
                    logger.warning(
                        "Broker latency spike | %.2fms",
                        latency_ms,
                    )

                return result

            except Exception as e:
                error_code = getattr(e, "code", -1)
                action = self.error_classifier.classify(error_code)

                logger.warning(
                    "Broker error classified | code=%s | action=%s",
                    error_code,
                    action.value,
                )

                self._broker_error_count += 1

                if self._broker_error_count > 15:
                    self.set_runtime_state(RuntimeState.BROKER_UNSTABLE)

                if action == RetryAction.DISABLE_EXECUTION:
                    self.set_runtime_state(RuntimeState.EXECUTION_DISABLED)
                    raise

                return await self.retry_engine.retry_async(
                    lambda: execution_fn(*args, **kwargs),
                    action,
                )

    # ========================================================
    # EMERGENCY CONTROLS
    # ========================================================

    def emergency_shutdown(self, reason: str) -> None:
        logger.critical("EMERGENCY SHUTDOWN | %s", reason)
        self.set_runtime_state(RuntimeState.EMERGENCY_LOCKDOWN)

    def suspend_symbol(self, symbol: str) -> None:
        logger.warning("Symbol suspended | %s", symbol)
        self.execution_firewall.suspend_symbol(symbol)

    # ========================================================
    # HEALTH SNAPSHOT
    # ========================================================

    def health_snapshot(self) -> Dict[str, Any]:

        return {
            "runtime_state": self.runtime_state.value,
            "broker_error_count": self._broker_error_count,
            "market_data_failures": self._market_data_failures,
            "cached_symbols": len(self.market_snapshots),
            "queue_degraded": self.async_governor.degraded,
            "avg_broker_latency_ms": (
                self.latency_tracker.metrics.avg_broker_latency_ms
            ),
            "avg_execution_latency_ms": (
                self.latency_tracker.metrics.avg_execution_latency_ms
            ),
            "timestamp": time.time(),
        }


# ============================================================
# GLOBAL SINGLETON
# ============================================================

runtime_guard = InstitutionalRuntimeGuard()

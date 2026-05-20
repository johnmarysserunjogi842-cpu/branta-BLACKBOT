# connectors/account_connector.py

from __future__ import annotations

import asyncio
import logging
import os
import random
import time

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any, List, Callable, Set

import aiohttp

logger = logging.getLogger("account_connector")
logger.setLevel(logging.INFO)

# ============================================================
# CONFIG
# ============================================================

ACCOUNT_REFRESH_INTERVAL = 1.0
TICK_POLL_INTERVAL = 0.05
EXECUTION_POLL_INTERVAL = 0.2
HEALTH_CHECK_INTERVAL = 1.0
HEARTBEAT_INTERVAL = 5.0

HTTP_TIMEOUT = 3.0

MAX_BACKOFF = 5.0
MAX_RECONNECT_BACKOFF = 30.0
MAX_CONCURRENT_REQUESTS = 16

HEALTH_STALE_THRESHOLD = 10.0
CIRCUIT_BREAK_THRESHOLD = 5

REQUEST_RETRIES = 2

# ============================================================
# ERRORS
# ============================================================


class BrokerErrorCode(Enum):
    AUTH_FAILED = "AUTH_FAILED"
    CONNECTION_LOST = "CONNECTION_LOST"
    RATE_LIMIT = "RATE_LIMIT"
    DESYNC = "DESYNC"
    BROKER_REJECT = "BROKER_REJECT"
    HTTP_ERROR = "HTTP_ERROR"
    UNKNOWN = "UNKNOWN"


class BrokerError(Exception):
    def __init__(
        self,
        code: BrokerErrorCode,
        message: str,
        raw: Any = None,
    ):
        super().__init__(message)
        self.code = code
        self.raw = raw


# ============================================================
# DTO
# ============================================================


@dataclass(slots=True)
class AccountState:
    balance: float
    equity: float
    free_margin: float
    leverage: int
    currency: str
    is_demo: bool


# ============================================================
# CONNECTOR
# ============================================================


class AccountConnector:

    def __init__(
        self,
        bridge_url: Optional[str] = None,
        api_key: Optional[str] = None,
        tick_handler: Optional[Callable] = None,
        execution_handler: Optional[Callable] = None,
    ):

        self._bridge_url = (
            bridge_url
            or os.getenv("BRIDGE_URL", "http://127.0.0.1:8000")
        ).rstrip("/")

        self._api_key = api_key or os.getenv("BRIDGE_API_KEY")

        self._tick_handler = tick_handler or (
            lambda *args, **kwargs: None
        )

        self._execution_handler = execution_handler or (
            lambda *args, **kwargs: None
        )

        self._running = False

        self._tasks: List[asyncio.Task] = []
        self._detached_tasks: Set[asyncio.Task] = set()

        self._session: Optional[aiohttp.ClientSession] = None

        self._session_lock = asyncio.Lock()
        self._reconnect_lock = asyncio.Lock()

        self._semaphore = asyncio.Semaphore(
            MAX_CONCURRENT_REQUESTS
        )

        self._account_state: Optional[AccountState] = None

        self._last_tick_seq = 0
        self._last_update_seq = 0

        now = time.monotonic()

        self._last_tick_ts = now
        self._last_execution_ts = now
        self._last_account_refresh = now

        self._last_latency = 0.0

        self._consecutive_failures = 0

        self._trading_locked = True

        self._reconnect_delay = 1.0

        self._symbols: Dict[str, Any] = {}

    # ============================================================
    # PUBLIC COMPAT
    # ============================================================

    def attach_symbol(
        self,
        symbol: str,
        fetcher=None,
    ):
        self._symbols[symbol] = fetcher

    # ============================================================
    # LIFECYCLE
    # ============================================================

    async def start(self):

        if self._running:
            return

        self._running = True

        await self._connect()

        self._tasks = [
            asyncio.create_task(self._account_loop()),
            asyncio.create_task(self._tick_loop()),
            asyncio.create_task(self._execution_loop()),
            asyncio.create_task(self._heartbeat_loop()),
            asyncio.create_task(self._health_monitor_loop()),
        ]

        logger.info("AccountConnector started")

    async def stop(self):

        self._running = False

        for task in self._tasks:
            task.cancel()

        for task in list(self._detached_tasks):
            task.cancel()

        await asyncio.gather(
            *self._tasks,
            return_exceptions=True,
        )

        await asyncio.gather(
            *self._detached_tasks,
            return_exceptions=True,
        )

        async with self._session_lock:

            if self._session:
                await self._session.close()
                self._session = None

        logger.info("AccountConnector stopped")

    async def boot(self):
        await self.start()

    async def shutdown(self):
        await self.stop()

    # ============================================================
    # CONNECTION
    # ============================================================

    async def _connect(self):

        timeout = aiohttp.ClientTimeout(
            total=HTTP_TIMEOUT,
            connect=1.0,
            sock_read=2.0,
        )

        connector = aiohttp.TCPConnector(
            limit=64,
            ttl_dns_cache=300,
            ssl=False,
        )

        session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
        )

        async with self._session_lock:

            old = self._session
            self._session = session

            if old:
                await old.close()

        logger.info("HTTP session established")

    async def _safe_reconnect(self):

        async with self._reconnect_lock:

            logger.warning(
                "Bridge reconnect initiated"
            )

            try:

                async with self._session_lock:

                    if self._session:
                        await self._session.close()
                        self._session = None

                await asyncio.sleep(
                    self._reconnect_delay
                )

                self._reconnect_delay = min(
                    self._reconnect_delay * 2
                    * random.uniform(0.8, 1.2),
                    MAX_RECONNECT_BACKOFF,
                )

                await self._connect()

                self._consecutive_failures = 0

                logger.info(
                    "Reconnect successful"
                )

            except Exception:
                logger.exception(
                    "Reconnect failed"
                )

    # ============================================================
    # REQUEST CORE
    # ============================================================

    async def _request(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Dict[str, Any]] = None,
    ):

        headers = {}

        if self._api_key:
            headers["x-api-key"] = self._api_key

        url = f"{self._bridge_url}{endpoint}"

        for attempt in range(REQUEST_RETRIES):

            async with self._session_lock:
                session = self._session

            if not session:
                raise BrokerError(
                    BrokerErrorCode.CONNECTION_LOST,
                    "Session unavailable",
                )

            try:

                async with self._semaphore:

                    start = time.perf_counter()

                    async with session.request(
                        method,
                        url,
                        json=payload,
                        headers=headers,
                    ) as response:

                        self._last_latency = (
                            time.perf_counter() - start
                        )

                        if response.status == 401:
                            raise BrokerError(
                                BrokerErrorCode.AUTH_FAILED,
                                "Authentication failed",
                            )

                        if response.status == 409:
                            raise BrokerError(
                                BrokerErrorCode.DESYNC,
                                "Sequence desync",
                            )

                        if response.status == 429:
                            raise BrokerError(
                                BrokerErrorCode.RATE_LIMIT,
                                "Rate limit",
                            )

                        if response.status >= 400:

                            body = await response.text()

                            raise BrokerError(
                                BrokerErrorCode.HTTP_ERROR,
                                f"HTTP {response.status}",
                                body,
                            )

                        self._consecutive_failures = 0

                        try:
                            return await response.json()

                        except Exception:
                            raise BrokerError(
                                BrokerErrorCode.UNKNOWN,
                                "Invalid JSON response",
                            )

            except BrokerError:
                raise

            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
            ):

                self._consecutive_failures += 1

                if attempt < REQUEST_RETRIES - 1:

                    await asyncio.sleep(
                        0.05 * (attempt + 1)
                    )

                    continue

                raise BrokerError(
                    BrokerErrorCode.CONNECTION_LOST,
                    "Connection failure",
                )

            except Exception as e:

                self._consecutive_failures += 1

                raise BrokerError(
                    BrokerErrorCode.UNKNOWN,
                    str(e),
                )

    # ============================================================
    # PUBLIC API
    # ============================================================

    async def send_order(
        self,
        payload: Dict[str, Any],
    ):

        if self._trading_locked:
            return {
                "success": False,
                "reason": "TRADING_LOCKED",
            }

        try:

            response = await self._request(
                "POST",
                "/order",
                payload,
            )

            return {
                "success": True,
                "response": response,
            }

        except BrokerError as e:

            return {
                "success": False,
                "reason": e.code.value,
            }

    async def place_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        order_type: str = "MARKET",
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        comment: str = "BLACKBOT_AI",
    ):

        payload = {
            "symbol": symbol,
            "side": side.upper(),
            "volume": float(volume),
            "type": order_type.upper(),
            "sl": sl,
            "tp": tp,
            "comment": comment,
            "timestamp": time.time(),
        }

        return await self.send_order(payload)

    def trading_locked(self) -> bool:
        return self._trading_locked

    def get_account_state(
        self
    ) -> Optional[AccountState]:
        return self._account_state

    def health_snapshot(self):

        now = time.monotonic()

        return {
            "tick_stale":
                now - self._last_tick_ts,

            "execution_stale":
                now - self._last_execution_ts,

            "account_stale":
                now - self._last_account_refresh,

            "latency":
                self._last_latency,

            "failures":
                self._consecutive_failures,

            "trading_locked":
                self._trading_locked,
        }

    async def heartbeat(self):

        try:
            await self._request(
                "GET",
                "/heartbeat",
            )

        except Exception:
            pass

    # ============================================================
    # HEALTH LOOP
    # ============================================================

    async def _health_monitor_loop(self):

        while self._running:

            try:

                now = time.monotonic()

                data_stale = (
                    now - self._last_tick_ts
                    > HEALTH_STALE_THRESHOLD
                    or
                    now - self._last_account_refresh
                    > HEALTH_STALE_THRESHOLD
                )

                connection_bad = (
                    self._consecutive_failures
                    >= CIRCUIT_BREAK_THRESHOLD
                )

                self._trading_locked = (
                    data_stale
                    or
                    connection_bad
                )

                await asyncio.sleep(
                    HEALTH_CHECK_INTERVAL
                )

            except asyncio.CancelledError:
                break

            except Exception:
                logger.exception(
                    "Health monitor failure"
                )

    # ============================================================
    # HEARTBEAT LOOP
    # ============================================================

    async def _heartbeat_loop(self):

        while self._running:

            try:

                await self._request(
                    "GET",
                    "/heartbeat",
                )

                self._reconnect_delay = 1.0

            except Exception:

                logger.warning(
                    "Heartbeat failure"
                )

                await self._safe_reconnect()

            await asyncio.sleep(
                HEARTBEAT_INTERVAL
            )

    # ============================================================
    # ACCOUNT LOOP
    # ============================================================

    async def _account_loop(self):

        backoff = 0.5

        while self._running:

            try:

                data = await self._request(
                    "GET",
                    "/account",
                )

                self._account_state = (
                    AccountState(**data)
                )

                self._last_account_refresh = (
                    time.monotonic()
                )

                backoff = 0.5

                await asyncio.sleep(
                    ACCOUNT_REFRESH_INTERVAL
                )

            except asyncio.CancelledError:
                break

            except Exception:

                await asyncio.sleep(backoff)

                backoff = min(
                    backoff * 2,
                    MAX_BACKOFF,
                )

    # ============================================================
    # TICK LOOP
    # ============================================================

    async def _tick_loop(self):

        backoff = 0.5

        while self._running:

            try:

                response = await self._request(
                    "GET",
                    f"/ticks?after_seq={self._last_tick_seq}",
                )

                ticks = response.get(
                    "ticks",
                    [],
                )

                for tick in ticks:

                    self._spawn_task(
                        self._safe_tick_handler(tick)
                    )

                    self._last_tick_seq = tick["seq"]

                self._last_tick_ts = (
                    time.monotonic()
                )

                backoff = 0.5

                await asyncio.sleep(
                    TICK_POLL_INTERVAL
                )

            except asyncio.CancelledError:
                break

            except BrokerError as e:

                if e.code == BrokerErrorCode.DESYNC:
                    self._last_tick_seq = 0

                await asyncio.sleep(backoff)

                backoff = min(
                    backoff * 2,
                    MAX_BACKOFF,
                )

            except Exception:

                logger.exception(
                    "Tick loop failure"
                )

                await asyncio.sleep(backoff)

    # ============================================================
    # EXECUTION LOOP
    # ============================================================

    async def _execution_loop(self):

        backoff = 0.5

        while self._running:

            try:

                response = await self._request(
                    "GET",
                    f"/updates?after_seq={self._last_update_seq}",
                )

                updates = response.get(
                    "updates",
                    [],
                )

                for update in updates:

                    self._spawn_task(
                        self._safe_execution_handler(update)
                    )

                    self._last_update_seq = (
                        update["seq"]
                    )

                self._last_execution_ts = (
                    time.monotonic()
                )

                backoff = 0.5

                await asyncio.sleep(
                    EXECUTION_POLL_INTERVAL
                )

            except asyncio.CancelledError:
                break

            except BrokerError as e:

                if e.code == BrokerErrorCode.DESYNC:
                    self._last_update_seq = 0

                await asyncio.sleep(backoff)

                backoff = min(
                    backoff * 2,
                    MAX_BACKOFF,
                )

            except Exception:

                logger.exception(
                    "Execution loop failure"
                )

                await asyncio.sleep(backoff)

    # ============================================================
    # SAFE TASKS
    # ============================================================

    def _spawn_task(
        self,
        coro,
    ):

        task = asyncio.create_task(coro)

        self._detached_tasks.add(task)

        task.add_done_callback(
            self._detached_tasks.discard
        )

    async def _safe_tick_handler(
        self,
        tick: Dict[str, Any],
    ):

        try:

            result = self._tick_handler(
                tick["symbol"],
                tick["ts"],
                tick["bid"],
                tick["ask"],
                tick["volume"],
            )

            if asyncio.iscoroutine(result):
                await result

        except Exception:
            logger.exception(
                "Tick handler crashed"
            )

    async def _safe_execution_handler(
        self,
        update: Dict[str, Any],
    ):

        try:

            result = self._execution_handler(
                update
            )

            if asyncio.iscoroutine(result):
                await result

        except Exception:
            logger.exception(
                "Execution handler crashed"
            )
    #===========================================================
    # UI 
    #===========================================================
    def attach_ui(self, app):
      """
    Called AFTER App is created.
    Safe place to bind UI + scheduler.
    """
      self.app = app
      self.ui_scheduler = getattr(app, "ui_scheduler", None)       
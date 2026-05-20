# core/event_bus.py

from __future__ import annotations

from collections import defaultdict, deque
import asyncio
import threading
import time
import logging
from typing import Callable, Any, Dict, List, Union

logger = logging.getLogger("event_bus")


# ============================================================
# EVENT BUS (INSTITUTIONAL GRADE)
# ============================================================

class EventBus:
    """
    ============================================================
    BLACKBOT EVENT BUS (TICK-DRIVEN CORE)
    ============================================================

    Features:
    - thread-safe publishing (MT5 compatible)
    - async + sync listeners
    - high-speed tick fanout
    - optional buffering for replay/debug
    ============================================================
    """

    def __init__(self, max_buffer: int = 10000):

        # event registry
        self.listeners = defaultdict(list)

        # thread safety
        self.lock = threading.RLock()

        # optional tick/event buffer
        self.buffer = deque(maxlen=max_buffer)

        # stats
        self.events_emitted = 0
        self.last_event_time = 0.0

    # ============================================================
    # SUBSCRIBE
    # ============================================================

    def subscribe(self, event: str, callback: Callable):

        with self.lock:
            self.listeners[event].append(callback)

        logger.info(f"Subscribed → {event} | {callback.__name__}")

    # ============================================================
    # EMIT (SYNC SAFE ENTRY POINT)
    # ============================================================

    def emit(self, event: str, data: Any):

        """
        Safe entry point for MT5 threads.
        Can be called from ANY thread (critical fix).
        """

        with self.lock:
            self.events_emitted += 1
            self.last_event_time = time.time()

            # store in buffer for debugging/replay
            self.buffer.append((event, data, self.last_event_time))

            callbacks = list(self.listeners.get(event, []))

        # fanout outside lock (IMPORTANT FIX)
        for cb in callbacks:
            try:
                # async handler
                if asyncio.iscoroutinefunction(cb):
                    asyncio.create_task(cb(data))
                else:
                    cb(data)

            except Exception as e:
                logger.exception(f"Event handler failed: {e}")

    # ============================================================
    # ASYNC EMIT (OPTIONAL HIGH SPEED PATH)
    # ============================================================

    async def emit_async(self, event: str, data: Any):

        with self.lock:
            self.events_emitted += 1
            self.last_event_time = time.time()

            self.buffer.append((event, data, self.last_event_time))

            callbacks = list(self.listeners.get(event, []))

        for cb in callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(data)
                else:
                    cb(data)

            except Exception as e:
                logger.exception(f"Async event handler failed: {e}")

    # ============================================================
    # UTIL
    # ============================================================

    def get_recent_events(self, n: int = 100):

        with self.lock:
            return list(self.buffer)[-n:]

    def health(self) -> Dict[str, Any]:

        return {
            "events_emitted": self.events_emitted,
            "last_event_time": self.last_event_time,
            "buffer_size": len(self.buffer),
        }
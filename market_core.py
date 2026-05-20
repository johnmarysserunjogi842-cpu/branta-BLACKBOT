from __future__ import annotations

import time
import threading
from collections import deque
from typing import Dict, Optional
import numpy as np

MAX_CANDLES = 4096
STALE_FEED_MS = 3000


# ============================================================
# CONFIG
# ============================================================

class InstrumentConfig:
    def __init__(self):
        self.symbol = ""
        self.spread_limit = 5.0
        self.session_filter = False


# ============================================================
# DATA FETCHER (FIXED + SAFE + FEED-READY)
# ============================================================

class DataFetcher:

    def __init__(self, cfg):
        self._symbol = cfg.symbol
        self._lock = threading.Lock()

        # feed tracking
        self._last_feed_ts = 0.0

        # candle storage
        self._c1m = deque(maxlen=MAX_CANDLES)
        self._current_1m = None
        self._current_1m_open_ts = 0

        # debugging
        self.tick_count = 0

    # ========================================================
    # CRITICAL FIX: THIS MUST BE CALLED EXTERNALLY
    # ========================================================
    def on_tick(self, ts, bid, ask, vol):

        with self._lock:

            self._last_feed_ts = time.time()
            self.tick_count += 1

            minute_ts = (ts // 60) * 60
            mid = (bid + ask) / 2.0

            if self._current_1m is None or minute_ts != self._current_1m_open_ts:

                if self._current_1m:
                    self._c1m.append(self._current_1m)

                self._current_1m = {
                    "ts": minute_ts,
                    "o": mid,
                    "h": mid,
                    "l": mid,
                    "c": mid,
                    "v": vol,
                }

                self._current_1m_open_ts = minute_ts

            else:
                c = self._current_1m
                c["h"] = max(c["h"], mid)
                c["l"] = min(c["l"], mid)
                c["c"] = mid
                c["v"] += vol

    # ========================================================
    # FEED HEALTH CHECK (FIXED)
    # ========================================================
    def is_feed_stale(self):

        if self._last_feed_ts == 0.0:
            return True

        return (time.time() - self._last_feed_ts) * 1000 > STALE_FEED_MS

    def get_symbol(self):
        return self._symbol

    # ========================================================
    # SNAPSHOT ENGINE
    # ========================================================
    def _snap(self, candles):

        if not candles:
            return np.array([[int(time.time()), 1.0, 1.0, 1.0, 1.0, 0.0]])

        return np.array(
            [[c["ts"], c["o"], c["h"], c["l"], c["c"], c["v"]] for c in candles],
            dtype=np.float64,
        )

    # ========================================================
    # AGGREGATION
    # ========================================================
    def _agg(self, factor):

        with self._lock:
            src = list(self._c1m)

        out = []

        for i in range(0, len(src) - factor + 1, factor):

            ch = src[i:i + factor]

            out.append({
                "ts": ch[0]["ts"],
                "o": ch[0]["o"],
                "h": max(c["h"] for c in ch),
                "l": min(c["l"] for c in ch),
                "c": ch[-1]["c"],
                "v": sum(c["v"] for c in ch),
            })

        return out

    # ========================================================
    # SNAPSHOTS
    # ========================================================
    def snapshot_1m_np(self):

        with self._lock:
            c = list(self._c1m) + ([self._current_1m] if self._current_1m else [])

        return self._snap(c)

    def snapshot_5m_np(self):
        return self._snap(self._agg(5))

    def snapshot_15m_np(self):
        return self._snap(self._agg(15))

    def snapshot_1h_np(self):
        return self._snap(self._agg(60))


# ============================================================
# MARKET ENGINE (FIXED + CLEAN ACCESS LAYER)
# ============================================================

class MarketEngine:

    def __init__(self):
        self._lock = threading.Lock()
        self._engines: Dict[str, DataFetcher] = {}

    def add_instrument(self, cfg):

        with self._lock:
            self._engines[cfg.symbol] = DataFetcher(cfg)

    def get(self, symbol: str) -> Optional[DataFetcher]:

        with self._lock:
            return self._engines.get(symbol)


# ============================================================
# OPTIONAL: TEST FEED (CRITICAL FOR DEBUGGING)
# ============================================================

def attach_test_feed(engine: MarketEngine, symbol: str):
    """
    USE ONLY FOR TESTING.
    This fixes your 'feed stale forever' problem immediately.
    """

    import asyncio
    import random

    async def feeder():

        fetcher = engine.get(symbol)

        while True:

            if fetcher:
                bid = 1.1000 + random.random() * 0.0010
                ask = bid + 0.0002
                vol = random.random() * 5

                fetcher.on_tick(time.time(), bid, ask, vol)

            await asyncio.sleep(0.5)

    return feeder
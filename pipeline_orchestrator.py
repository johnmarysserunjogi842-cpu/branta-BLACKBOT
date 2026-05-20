from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Any

import numpy as np

from connectors.account_connector import AccountConnector
from institutional_runtime_guard import runtime_guard
from core.self_healing import SelfHealingCore
from market_analyzer.orchestrator import Orchestrator as MarketBrain

from market_analyzer.timeframe_engine import TimeframeEngine
from market_analyzer.session_manager import SessionManager
from market_analyzer.market_regime import MarketRegimeEngine as RegimeEngine

try:
    from market_core import MarketEngine as MarketCore
except Exception:
    MarketCore = None


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger("pipeline_orchestrator")
logger.setLevel(logging.INFO)

if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s | %(message)s"))
    logger.addHandler(h)


# ============================================================
# SAFE COMPONENTS
# ============================================================

class SafeNewsFilter:
    def analyze(self, symbol: str, context: dict):
        return {"news_score": 0.0, "bias": "NEUTRAL", "active": False}


class SafeInstrumentRanker:
    def rank(self, market_state: dict):
        return []


# ============================================================
# UTIL
# ============================================================

def numpy_to_candle_dict(symbol: str, timeframe: str, arr: np.ndarray) -> dict:
    if arr is None or getattr(arr, "size", 0) == 0:
        return {"symbol": symbol, "timeframe": timeframe, "candles": []}

    candles = []
    for r in arr:
        try:
            candles.append({
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            })
        except Exception:
            continue

    return {"symbol": symbol, "timeframe": timeframe, "candles": candles}


# ============================================================
# SYMBOL STATE
# ============================================================

@dataclass(slots=True)
class SymbolState:
    symbol: str
    tick_queue: asyncio.Queue
    last_tick_ts: float = 0.0
    dropped_ticks: int = 0
    paused: bool = False


# ============================================================
# MARKET ORCHESTRATOR
# ============================================================

class MarketOrchestrator:

    def __init__(
        self,
        symbols: List[str],
        account: AccountConnector,
        feature_interval: float = 1.0,
        spread_limit: float = 0.0003,
        queue_size: int = 5,
    ):

        # CORE
        self.symbols = symbols
        self.account = account

        # CONFIG
        self.feature_interval = feature_interval
        self.spread_limit = spread_limit
        self.queue_size = queue_size

        # ENGINE
        self.engine = MarketCore() if MarketCore else None
        self.healer = SelfHealingCore()

        self.market_cache = __import__(
            "market_analyzer.market_state_cache",
            fromlist=["MarketStateCache"]
        ).MarketStateCache()

        # INSTITUTIONAL ENGINES
        self.engines = {
            "regime_engine": RegimeEngine(),
            "timeframe_engine": TimeframeEngine(),
            "session_manager": SessionManager(),
            "news_filter": SafeNewsFilter(),
            "instrument_ranker": SafeInstrumentRanker(),
        }

        # MARKET BRAIN
        self.market_brain = MarketBrain(
            market_state_cache=self.market_cache,
            max_workers=8,
            enable_phase2=True,
            enable_phase3=True,
            engines=self.engines,
        )

        # STATE
        self._running = True
        self._tasks: List[asyncio.Task] = []

        self.symbol_failures: Dict[str, int] = {}
        self.symbol_disabled: Dict[str, bool] = {}
        self.last_trade_ts: Dict[str, float] = {}

        self.symbol_states = {
            sym: SymbolState(sym, asyncio.Queue(maxsize=queue_size))
            for sym in symbols
        }

        logger.info("MarketOrchestrator initialized | symbols=%s", symbols)

    # ============================================================
    # ANALYTICS LOOP (FIXED)
    # ============================================================

    async def _analytics_loop(self, symbol: str):

        if not self.engine:
            logger.warning("Market engine missing | analytics disabled")
            return

        logger.info("Analytics loop started | %s", symbol)

        while self._running:

            try:

                # ====================================================
                # HEARTBEAT
                # ====================================================
                self.symbol_states[symbol].last_tick_ts = time.time()

                # ====================================================
                # DISABLED CHECK
                # ====================================================
                if self.symbol_disabled.get(symbol):
                    await asyncio.sleep(5)
                    continue

                # ====================================================
                # FETCHER (SAFE INIT FIRST)
                # ====================================================
                fetcher = self.engine.get(symbol)

                if not fetcher:
                    logger.warning("Fetcher missing | %s", symbol)
                    await asyncio.sleep(self.feature_interval)
                    continue

                if not hasattr(fetcher, "snapshot_1m_np"):
                    logger.error("Invalid fetcher | %s", symbol)
                    await asyncio.sleep(self.feature_interval)
                    continue

                # ====================================================
                # FEED STALE CHECK (SAFE)
                # ====================================================
                try:
                    if hasattr(fetcher, "is_feed_stale") and fetcher.is_feed_stale():
                        logger.warning("Feed stale | %s", symbol)

                        if hasattr(fetcher, "force_refresh"):
                            fetcher.force_refresh()

                        await asyncio.sleep(1)
                        continue

                except Exception as e:
                    logger.error("Feed check failed | %s | %s", symbol, e)
                    await asyncio.sleep(1)
                    continue

                # ====================================================
                # DEBUG (SAFE POSITION)
                # ====================================================
                logger.debug(
                    "FETCHER DEBUG | %s | ts=%s",
                    symbol,
                    getattr(fetcher, "last_tick_ts", None),
                )

                # ====================================================
                # SNAPSHOT
                # ====================================================
                arr = fetcher.snapshot_1m_np()

                candle_data = numpy_to_candle_dict(symbol, "1m", arr)

                if not candle_data["candles"]:
                    await asyncio.sleep(0.2)
                    continue

                # ====================================================
                # RUNTIME VALIDATION
                # ====================================================
                if not runtime_guard.validate_market_data(symbol, candle_data["candles"]):
                    continue

                # ====================================================
                # ANALYSIS
                # ====================================================
                analysis = self.market_brain.run({symbol: {"1m": candle_data}})

                if not isinstance(analysis, dict) or "market_state" not in analysis:
                    continue

                ms = analysis["market_state"].get(symbol, {})

                # ====================================================
                # NEWS (SAFE)
                # ====================================================
                news_score = 0.0
                try:
                    nf = self.engines["news_filter"]
                    news_score = nf.analyze(symbol, analysis).get("news_score", 0.0)
                except Exception:
                    pass

                # ====================================================
                # SIMPLE CONFIDENCE
                # ====================================================
                confidence = 0.5 + (0.2 if ms.get("regime") == "TRENDING" else 0)

                decision = "BUY" if confidence > 0.75 else "HOLD"

                # ====================================================
                # EXECUTION GUARD
                # ====================================================
                if decision != "HOLD":

                    try:
                        spread = self.account.get_spread(symbol)
                        if spread <= self.spread_limit:

                            await self.account.place_order(
                                symbol=symbol,
                                side=decision,
                                volume=round(confidence * 0.02, 3),
                            )

                            logger.info("TRADE EXECUTED | %s | %s", symbol, decision)

                    except Exception as e:
                        logger.error("Execution failed | %s | %s", symbol, e)

                self.symbol_failures[symbol] = 0

            except Exception as e:

                logger.exception("Loop crash | %s | %s", symbol, e)

                self.symbol_failures[symbol] = self.symbol_failures.get(symbol, 0) + 1

                if self.symbol_failures[symbol] > 10:
                    self.symbol_disabled[symbol] = True
                    logger.critical("%s DISABLED", symbol)

            await asyncio.sleep(self.feature_interval)

    # ============================================================
    # BOOT
    # ============================================================

    async def boot(self):

        logger.info("Booting market orchestrator...")

        if not self.engine:
            logger.warning("Market engine unavailable")
            return

        for sym in self.symbols:

            try:
                cfg = type("cfg", (), {"symbol": sym})

                self.engine.add_instrument(cfg)

                fetcher = self.engine.get(sym)

                if fetcher and hasattr(self.account, "attach_symbol"):
                    self.account.attach_symbol(sym, fetcher)
                    logger.info("%s attached", sym)

            except Exception as e:
                logger.exception("Boot failure | %s | %s", sym, e)

        logger.info("Boot complete")

    # ============================================================
    # RUN
    # ============================================================

    async def run(self):

        await self.boot()

        logger.info("Starting orchestrator tasks...")

        for sym in self.symbols:
            self._tasks.append(
                asyncio.create_task(self._analytics_loop(sym))
            )

        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ============================================================
    # STOP
    # ============================================================

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("Stopped cleanly")
from __future__ import annotations

import asyncio
import threading
import logging
import time
from typing import Any, Optional, Callable
from unittest import result

from connectors.app_connector import AppConnector

logger = logging.getLogger("controller")
logger.setLevel(logging.INFO)


class Controller:
    """
    =========================================================
    🚀 BLACKBOT v4.1 — INSTITUTIONAL TRADING OS CONTROLLER
    =========================================================

    ✔ Async orchestration core (UI-safe)
    ✔ Signal → Pipeline → Execution → MT5 bridge
    ✔ Trade Desk integration (FIXED)
    ✔ Live trade + event stream
    ✔ Deduplication + rate limiting
    ✔ Crash-safe async engine
    =========================================================
    """

    # =========================================================
    # INIT
    # =========================================================
    def __init__(self, app: AppConnector):
        self.app = app

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._start_loop,
            daemon=True,
            name="BlackBotV4.1-AsyncLoop"
        )

        self._running = True

        # -------------------------
        # BACKPRESSURE FLAGS
        # -------------------------
        self._dashboard_pending = False
        self._signals_pending = False
        self._health_pending = False

        # -------------------------
        # LIVE STREAMS
        # -------------------------
        self._trade_stream = []
        self._signal_stream = []
        self._event_stream = []

        # -------------------------
        # SYSTEM STATE
        # -------------------------
        self._tasks = set()
        self._last_call = {}
        self._min_interval = 0.18
        self._inflight_keys = set()

        self._thread.start()
        logger.info("🚀 BlackBot v4.1 Controller ONLINE")

    # =========================================================
    # LOOP
    # =========================================================
    def _start_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # =========================================================
    # CORE ENGINE
    # =========================================================
    def _run_async(
        self,
        coro,
        callback: Optional[Callable[[Any], None]] = None,
        fallback: Any = None,
        key: Optional[str] = None,
        retries: int = 2,
        timeout: float = 8.0,
        tag: str = "GENERIC",
    ):
        if not self._running:
            return

        if key and key in self._inflight_keys:
            return
        if key:
            self._inflight_keys.add(key)

        async def runner():
            attempt = 0
            delay = 0.25
            result = fallback

            try:
                while attempt <= retries:
                    try:
                        result = await asyncio.wait_for(coro, timeout=timeout)
                        break
                    except Exception:
                        attempt += 1
                        await asyncio.sleep(delay)
                        delay *= 2

                if callback:
                    try:
                        import tkinter as tk
                        root = tk._default_root
                        if root and root.winfo_exists():
                            root.after(0, lambda: callback(result))
                    except Exception:
                        logger.exception("UI callback failed")

                return result

            finally:
                if key:
                    self._inflight_keys.discard(key)

        try:
            future = asyncio.run_coroutine_threadsafe(runner(), self._loop)
            self._tasks.add(future)

            def cleanup(f):
                self._tasks.discard(f)

            future.add_done_callback(cleanup)

        except Exception:
            logger.exception("Async submit failed")

    # =========================================================
    # 🔥 FIXED: EXECUTE SIGNAL → MT5 PIPELINE
    # =========================================================
    def execute_signal(self, signal, session_ctx, news_ctx, market_state, callback=None):
        """
        MAIN TRADING PIPELINE:
        UI → Controller → Pipeline → TradeDesk → MT5 Executor
        """

        key = f"trade:{signal.symbol}:{signal.bias}:{time.time()}"

        def run_trade():
            try:
                # 1. PIPELINE DECISION
                decision = self.app.pipeline.evaluate(
                    signal,
                    session_ctx,
                    news_ctx,
                    market_state
                )

                # 2. REGISTER IN TRADE DESK (SAFE ONCE)
                trade_id = None
                if hasattr(self.app, "trade_desk") and self.app.trade_desk:
                    trade_id = self.app.trade_desk.register_trade(decision)

                # 3. EXECUTE ON MT5 (ONLY ONCE — FIXED BUG)
                if hasattr(self.app, "executor") and self.app.executor:
                    self.app.executor.submit(decision)

                # 4. EVENT LOGGING
                event = {
                    "trade_id": trade_id,
                    "symbol": decision.symbol,
                    "bias": decision.bias,
                    "confidence": decision.final_confidence,
                    "mode": decision.aggression_mode,
                    "risk": decision.risk_multiplier,
                    "allowed": decision.allow_trade,
                    "reason": decision.reason,
                    "timestamp": time.time()
                }

                self._trade_stream.append(event)
                self._event_stream.append(event)

                if len(self._trade_stream) > 100:
                    self._trade_stream.pop(0)

                if len(self._event_stream) > 200:
                    self._event_stream.pop(0)

                return event

            except Exception as e:
                logger.exception("Trade pipeline failed")
                return {"error": str(e)}

        self._run_async(
            coro=self._wrap_sync(run_trade),
            callback=callback,
            fallback={"error": "execution failed"},
            key=key,
            tag="TRADE_ENGINE"
        )

    # =========================================================
    # WRAPPER
    # =========================================================
    def _wrap_sync(self, func):
        async def wrapper():
            return func()
        return wrapper()

    def get_dashboard_snapshot(self, callback, force_refresh=False):
        if self._dashboard_pending or not self._allow_call("dashboard"):
            return

        self._dashboard_pending = True

        def wrapped(result):
             self._dashboard_pending = False

        # ✅ ADD TRADE DESK SNAPSHOT HERE
        try:
            if hasattr(self.app, "trade_desk"):
                result["trade_desk"] = self.app.trade_desk.snapshot()
        except Exception:
            logger.exception("TradeDesk snapshot failed")

        callback(result)

        self._run_async(
            self.app.get_dashboard_snapshot(force_refresh=force_refresh),
            callback=wrapped,
            fallback={"error": "dashboard failed"},
            key="dashboard",
            tag="DASHBOARD"
    )

    # =========================================================
    # SIGNALS
    # =========================================================
    def get_signals(self, callback):
        if self._signals_pending:
            return

        self._signals_pending = True

        def wrapped(result):
            self._signals_pending = False
            result["live_trades"] = self._trade_stream[-10:]
            callback(result)

        self._run_async(
            self.app.get_signals(),
            callback=wrapped,
            fallback={"signals": [], "live_trades": []},
            key="signals",
            tag="SIGNALS"
        )

    # =========================================================
    # HEALTH
    # =========================================================
    def get_system_health(self, callback):
        if self._health_pending:
            return

        self._health_pending = True

        def wrapped(result):
            self._health_pending = False
            callback(result)

        self._run_async(
            self.app.get_system_health(),
            callback=wrapped,
            fallback={"error": "health failed"},
            key="health",
            tag="HEALTH"
        )

    # =========================================================
    # LIVE ACCESS
    # =========================================================
    def get_live_trades(self):
        return self._trade_stream[-20:]

    def get_event_stream(self):
        return self._event_stream[-50:]

    # =========================================================
    # BOT CONTROL
    # =========================================================
    def start_bot(self, callback=None):
        self._run_async(self.app.start_bot(), callback=callback)

    def pause_bot(self, callback=None):
        self._run_async(self.app.pause_bot(), callback=callback)

    def toggle_bot(self, callback=None):
        self._run_async(self.app.toggle_bot(), callback=callback)

    # =========================================================
    # SHUTDOWN
    # =========================================================
    def shutdown(self):
        if not self._running:
            return

        self._running = False

        try:
            def stop():
                for t in asyncio.all_tasks(loop=self._loop):
                    t.cancel()
                self._loop.stop()

            self._loop.call_soon_threadsafe(stop)
            self._thread.join(timeout=3)

            logger.info("🚀 BlackBot v4.1 shutdown complete")

        except Exception:
            logger.exception("Shutdown error")
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict

import yaml

from market_analyzer.market_state_cache import MarketStateCache
from market_analyzer.orchestrator import (
    Orchestrator as AnalyticsOrchestrator,
)

from performance.metrics import MetricsTracker
from performance.debugger import Debugger

from connectors.feedback import FeedbackEngine
from connectors.account_connector import AccountConnector
from connectors.app_connector import AppConnector

from pipeline_orchestrator import MarketOrchestrator
from signal_pipeline import SignalPipeline

from core.trade_executor import TradeExecutor
from core.self_healing import SelfHealingCore
from core.event_bus import EventBus

from execution_controller import ExecutionControllerV3

from executors.sim_executor import SimulatedExecutor
from executors.trade_desk import TradeDesk

from strategy_manager.live_strategy_router import (
    LiveStrategyRouter,
)

from boot.runtime_supervisor import RuntimeSupervisor


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("blackbot.main")


# ============================================================
# SYMBOL CONFIG
# ============================================================

def load_symbols():

    path = "config/instruments.yaml"

    try:

        if os.path.exists(path):

            with open(path, "r") as f:

                data = yaml.safe_load(f) or {}

                if data.get("symbols"):
                    return data["symbols"]

    except Exception:
        logger.exception("symbol load failed")

    return ["EURUSD"]


SYMBOLS = load_symbols()


# ============================================================
# SYSTEM CONTEXT
# ============================================================

class SystemContext:

    def __init__(self):

        self.market_cache = MarketStateCache()

        self.metrics = MetricsTracker()

        self.positions: Dict = {}


# ============================================================
# APPLICATION CORE
# ============================================================

class Application:

    HEARTBEAT_SEC = float(
        os.getenv("HEARTBEAT_SEC", 5)
    )

    # ========================================================
    # INIT
    # ========================================================

    def __init__(self):

        logger.info("BOOTING BLACKBOT CORE")

        self.ctx = SystemContext()

        self.healer = SelfHealingCore()

        # ====================================================
        # EVENT BUS
        # ====================================================

        self.event_bus = EventBus()

        # ====================================================
        # CONNECTORS
        # ====================================================

        self.feedback = FeedbackEngine(
            metrics=self.ctx.metrics
        )

        self.account = AccountConnector()

        # ====================================================
        # ANALYTICS ENGINE
        # ====================================================

        self.analytics = AnalyticsOrchestrator(
            market_state_cache=self.ctx.market_cache
        )

        # ====================================================
        # MARKET PIPELINE
        # ====================================================

        self.pipeline = MarketOrchestrator(
            symbols=SYMBOLS,
            account=self.account,
        )

        # ====================================================
        # SIGNAL PIPELINE
        # ====================================================

        self.signal_pipeline = SignalPipeline()

        self.signal_pipeline.current_positions = (
            self.ctx.positions
        )

        # ====================================================
        # TRADE SYSTEM
        # ====================================================

        self.trade_desk = TradeDesk()

        self.executor = SimulatedExecutor(
            self.trade_desk
        )

        self.trade_executor = TradeExecutor()

        self.execution_controller = (
            ExecutionControllerV3(
                signal_pipeline=self.signal_pipeline,
                trade_executor=self.trade_executor,
                account=self.account,
                feedback=self.feedback,
            )
        )

        # ====================================================
        # STRATEGY MANAGER
        # ====================================================

        self.strategy_manager = getattr(
            self.pipeline,
            "strategy_manager",
            None
        )

        # ====================================================
        # LIVE STRATEGY ROUTER
        # ====================================================

        self.strategy_router = None

        if self.strategy_manager:

            self.strategy_router = (
                LiveStrategyRouter(
                    mt5_core=None,
                    market_analyzer=self.analytics,
                    strategy_manager=self.strategy_manager,
                    signal_pipeline=self.signal_pipeline,
                    execution_controller=self.execution_controller,
                )
            )

            self.strategy_router.start()

            logger.info(
                "LiveStrategyRouter started"
            )

        else:

            logger.warning(
                "strategy_manager missing from pipeline"
            )

        # ====================================================
        # EVENT BUS CONNECTION
        # ====================================================

        try:

            if self.strategy_router:

                self.event_bus.subscribe(
                    "TICK",
                    self.strategy_router.on_tick
                )

                logger.info(
                    "EventBus connected to StrategyRouter"
                )

        except Exception:

            logger.exception(
                "Failed to connect EventBus"
            )

        # ====================================================
        # MT5 → EVENT BUS CONNECTION
        # ====================================================

        try:

            if hasattr(self.pipeline, "mt5_core"):

                async def emit_tick(tick):

                    await self.event_bus.emit(
                        "TICK",
                        tick
                    )

                def forward_tick(symbol, tick):

                    tick["symbol"] = symbol

                    try:

                        asyncio.run(
                            emit_tick(tick)
                        )

                    except Exception as e:

                        logger.error(
                            f"Tick emit failed: {e}"
                        )

                self.pipeline.mt5_core.on_tick = (
                    forward_tick
                )

                logger.info(
                    "MT5 live ticks connected to EventBus"
                )

            else:

                logger.warning(
                    "mt5_core missing from pipeline"
                )

        except Exception:

            logger.exception(
                "Failed to bind MT5 tick callback"
            )

        # ====================================================
        # APP CONNECTOR
        # ====================================================

        self.app_connector = AppConnector(
            market_cache=self.ctx.market_cache,
            feedback=self.feedback,
            account=self.account,
            signal_pipeline=self.signal_pipeline,
            metrics=self.ctx.metrics,
        )

        # ====================================================
        # INJECT EXECUTION REFERENCES
        # ====================================================

        self.app_connector.trade_desk = (
            self.trade_desk
        )

        self.app_connector.executor = (
            self.executor
        )

        # ====================================================
        # DEBUG / SUPERVISION
        # ====================================================

        self.debugger = Debugger(
            cache=self.ctx.market_cache
        )

        self.supervisor = RuntimeSupervisor()

        # ====================================================
        # TASK STATE
        # ====================================================

        self.tasks = {}

        self._pool = ThreadPoolExecutor(
            max_workers=2
        )

        self._last_result = None

        self._last_ts = 0

        self._shutdown = False

        logger.info("CORE READY")

    # ========================================================
    # START
    # ========================================================

    async def start(self):

        logger.info(
            "Starting backend services..."
        )

        await self.account.boot()

        await self._launch_tasks()

        while not self._shutdown:

            await asyncio.sleep(3600)

    # ========================================================
    # TASK LAUNCHER
    # ========================================================

    async def _launch_tasks(self):

        tasks = [

            (
                "pipeline",
                self.pipeline.run(),
            ),

            (
                "execution",
                self.execution_controller.run_forever(
                    SYMBOLS,
                    250,
                ),
            ),
        ]

        for name, coro in tasks:

            task = asyncio.create_task(
                coro,
                name=name,
            )

            self.tasks[name] = task

            logger.info(
                f"Task started | {name}"
            )

    # ========================================================
    # SHUTDOWN
    # ========================================================

    async def shutdown(self):

        logger.warning(
            "Shutdown initiated"
        )

        self._shutdown = True

        try:

            if self.strategy_router:
                self.strategy_router.stop()

        except Exception:

            logger.exception(
                "Router shutdown failed"
            )

        for task in self.tasks.values():

            task.cancel()

        await self.account.shutdown()

        logger.info(
            "BLACKBOT shutdown complete"
        )


# ============================================================
# ENTRYPOINT
# ============================================================

def main():

    try:

        app = Application()

        # ====================================================
        # BACKEND THREAD
        # ====================================================

        backend_thread = threading.Thread(
            target=lambda: asyncio.run(
                app.start()
            ),
            daemon=True,
        )

        backend_thread.start()

        logger.info(
            "Backend thread started"
        )

        # ====================================================
        # UI MUST RUN IN MAIN THREAD
        # ====================================================

        from UI.main_window import MainWindow

        ui = MainWindow(
            controller=app.app_connector
        )

        # attach UI to connector
        app.app_connector.attach_ui(ui)

        logger.info(
            "Launching BLACKBOT UI..."
        )

        ui.run()

    except KeyboardInterrupt:

        logger.warning(
            "Shutdown requested"
        )

    except Exception:

        logger.exception(
            "fatal crash"
        )

        sys.exit(1)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    main()
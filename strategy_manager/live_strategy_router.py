# strategy_manager/live_strategy_router.py

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Any


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger("live_strategy_router")
logger.setLevel(logging.INFO)


# ============================================================
# LIVE STRATEGY ROUTER
# ============================================================

class LiveStrategyRouter:
    """
    ============================================================
    BLACKBOT LIVE STRATEGY ROUTER
    ============================================================

    FLOW:
        MT5 / EventBus
            ↓
        Market Analyzer
            ↓
        Strategy Manager
            ↓
        Signal Pipeline
            ↓
        Execution Controller
            ↓
        Broker Execution
    ============================================================
    """

    def __init__(
        self,
        mt5_core,
        market_analyzer,
        strategy_manager,
        signal_pipeline,
        execution_controller,
    ):

        # ====================================================
        # SYSTEMS
        # ====================================================

        self.mt5_core = mt5_core
        self.market_analyzer = market_analyzer
        self.strategy_manager = strategy_manager
        self.signal_pipeline = signal_pipeline
        self.execution_controller = execution_controller

        # ====================================================
        # STATE
        # ====================================================

        self.running = False

        self.tick_counter = 0
        self.signal_counter = 0
        self.execution_counter = 0

        self.lock = threading.RLock()

        # ====================================================
        # METRICS
        # ====================================================

        self.metrics = {
            "ticks_processed": 0,
            "signals_generated": 0,
            "signals_approved": 0,
            "trades_executed": 0,
            "last_tick_time": 0.0,
            "last_execution_time": 0.0,
        }

        logger.info("LiveStrategyRouter initialized")

    # ============================================================
    # START / STOP
    # ============================================================

    def start(self):

        if self.running:
            return

        self.running = True

        # ====================================================
        # CONNECT MT5 CALLBACK
        # ====================================================

        if self.mt5_core:
            self.mt5_core.on_tick = self.on_tick

        logger.info("LiveStrategyRouter started")
        logger.info("MT5 tick callback connected")

    def stop(self):

        self.running = False

        try:
            if self.mt5_core:
                self.mt5_core.on_tick = None
        except Exception:
            pass

        logger.info("LiveStrategyRouter stopped")

    # ============================================================
    # MAIN TICK ENTRY
    # ============================================================

    def on_tick(self, tick: Dict[str, Any]):

        if not self.running:
            return

        if not tick:
            return

        symbol = tick.get("symbol")

        if not symbol:
            return

        try:

            # ====================================================
            # METRICS
            # ====================================================

            self.tick_counter += 1

            self.metrics["ticks_processed"] += 1
            self.metrics["last_tick_time"] = time.time()

            # ====================================================
            # VALIDATE TICK
            # ====================================================

            if "bid" not in tick or "ask" not in tick:
                return

            # ====================================================
            # MARKET ANALYSIS
            # ====================================================

            market_state = None

            try:

                # --------------------------------------------
                # METHOD 1
                # --------------------------------------------

                if hasattr(
                    self.market_analyzer,
                    "update_market_state"
                ):

                    market_state = (
                        self.market_analyzer
                        .update_market_state(
                            symbol=symbol,
                            tick=tick,
                        )
                    )

                # --------------------------------------------
                # METHOD 2
                # --------------------------------------------

                elif hasattr(
                    self.market_analyzer,
                    "process_tick"
                ):

                    market_state = (
                        self.market_analyzer
                        .process_tick(
                            symbol=symbol,
                            tick=tick,
                        )
                    )

                # --------------------------------------------
                # FALLBACK
                # --------------------------------------------

                else:

                    market_state = {
                        "symbol": symbol,
                        "tick": tick,
                    }

            except Exception as e:

                logger.error(
                    f"Market analyzer failed "
                    f"| {symbol} | {e}"
                )

                return

            if not market_state:
                return

            # ====================================================
            # STRATEGY MANAGER
            # ====================================================

            strategy_signal = None

            try:

                # --------------------------------------------
                # METHOD 1
                # --------------------------------------------

                if hasattr(
                    self.strategy_manager,
                    "generate_signal"
                ):

                    strategy_signal = (
                        self.strategy_manager
                        .generate_signal(
                            symbol=symbol,
                            market_state=market_state,
                        )
                    )

                # --------------------------------------------
                # METHOD 2
                # --------------------------------------------

                elif hasattr(
                    self.strategy_manager,
                    "process_symbol"
                ):

                    strategy_signal = (
                        self.strategy_manager
                        .process_symbol(
                            symbol=symbol,
                            market_state=market_state,
                        )
                    )

            except Exception as e:

                logger.error(
                    f"Strategy manager failed "
                    f"| {symbol} | {e}"
                )

                return

            if not strategy_signal:
                return

            # ====================================================
            # SIGNAL GENERATED
            # ====================================================

            self.signal_counter += 1

            self.metrics["signals_generated"] += 1

            logger.info(
                f"SIGNAL GENERATED "
                f"| {symbol} "
                f"| {strategy_signal}"
            )

            # ====================================================
            # SIGNAL PIPELINE
            # ====================================================

            approved_signal = None

            try:

                # --------------------------------------------
                # METHOD 1
                # --------------------------------------------

                if hasattr(
                    self.signal_pipeline,
                    "validate_signal"
                ):

                    approved_signal = (
                        self.signal_pipeline
                        .validate_signal(
                            strategy_signal
                        )
                    )

                # --------------------------------------------
                # METHOD 2
                # --------------------------------------------

                elif hasattr(
                    self.signal_pipeline,
                    "process_signal"
                ):

                    approved_signal = (
                        self.signal_pipeline
                        .process_signal(
                            strategy_signal
                        )
                    )

                # --------------------------------------------
                # FALLBACK
                # --------------------------------------------

                else:

                    approved_signal = strategy_signal

            except Exception as e:

                logger.error(
                    f"Signal pipeline failed "
                    f"| {symbol} | {e}"
                )

                return

            if not approved_signal:

                logger.info(
                    f"SIGNAL REJECTED "
                    f"| {symbol}"
                )

                return

            # ====================================================
            # SIGNAL APPROVED
            # ====================================================

            self.metrics["signals_approved"] += 1

            logger.info(
                f"SIGNAL APPROVED "
                f"| {approved_signal}"
            )

            # ====================================================
            # EXECUTION CONTROLLER
            # ====================================================

            result = None

            try:

                # --------------------------------------------
                # METHOD 1
                # --------------------------------------------

                if hasattr(
                    self.execution_controller,
                    "execute_signal"
                ):

                    result = (
                        self.execution_controller
                        .execute_signal(
                            approved_signal
                        )
                    )

                # --------------------------------------------
                # METHOD 2
                # --------------------------------------------

                elif hasattr(
                    self.execution_controller,
                    "submit_signal"
                ):

                    result = (
                        self.execution_controller
                        .submit_signal(
                            approved_signal
                        )
                    )

                # --------------------------------------------
                # METHOD 3
                # --------------------------------------------

                elif hasattr(
                    self.execution_controller,
                    "handle_signal"
                ):

                    result = (
                        self.execution_controller
                        .handle_signal(
                            approved_signal
                        )
                    )

            except Exception as e:

                logger.error(
                    f"Execution failed "
                    f"| {symbol} | {e}"
                )

                return

            # ====================================================
            # EXECUTION SUCCESS
            # ====================================================

            self.execution_counter += 1

            self.metrics["trades_executed"] += 1
            self.metrics["last_execution_time"] = time.time()

            logger.info(
                f"TRADE EXECUTED "
                f"| SYMBOL={symbol} "
                f"| RESULT={result}"
            )

        except Exception as e:

            logger.exception(
                f"Router tick failure "
                f"| {symbol} | {e}"
            )

    # ============================================================
    # METRICS
    # ============================================================

    def get_metrics(self) -> Dict[str, Any]:

        return {
            "running": self.running,
            "tick_counter": self.tick_counter,
            "signal_counter": self.signal_counter,
            "execution_counter": self.execution_counter,
            "metrics": dict(self.metrics),
        }
from __future__ import annotations

import asyncio
import time
import itertools
import logging
from dataclasses import dataclass
from typing import Dict, Any

from strategy_manager.strategy_loader import StrategyLoader
from strategy_manager.strategy_voter import StrategyVoter
from strategy_manager.confirmation_engine import ConfirmationEngine

from signal_pipeline import (
    SignalPipeline,
    AggressionContext,
)

from core.trade_executor import (
    TradeExecutor,
    TradeIntent,
)

from connectors.account_connector import AccountConnector
from connectors.feedback import FeedbackEngine

from institutional_runtime_guard import (
    runtime_guard,
    ExecutionIntent,
)


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger("execution_controller_v3")

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )
    )

    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================
# METRICS
# ============================================================

@dataclass
class CycleMetrics:
    total_cycles: int = 0
    skipped: int = 0
    no_signal: int = 0
    blocked: int = 0
    submitted: int = 0
    errors: int = 0


# ============================================================
# EXECUTION CONTROLLER V3
# ============================================================

class ExecutionControllerV3:

    _intent_id_gen = itertools.count(1)

    def __init__(
        self,
        signal_pipeline: SignalPipeline,
        trade_executor: TradeExecutor,
        account: AccountConnector,
        feedback: FeedbackEngine,
        *,
        max_concurrent_submits: int = 8,
        max_parallel_symbols: int = 16,
        per_symbol_cooldown_ms: int = 200,
        hard_cycle_timeout_ms: int = 800,
    ):

        self.pipeline = signal_pipeline
        self.executor = trade_executor
        self.account = account
        self.feedback = feedback

        # ------------------------------------------------
        # STRATEGY SYSTEMS
        # ------------------------------------------------
        self.strategy_loader = StrategyLoader()
        self.strategy_voter = StrategyVoter(
            registry={},
            cache={}
        )

        self.confirmation_engine = ConfirmationEngine(
            cache={}
        )

        # ------------------------------------------------
        # RUNTIME STATE
        # ------------------------------------------------
        self._running = True

        self._last_ts: Dict[str, int] = {}
        self._errors: Dict[str, int] = {}
        self._circuit: Dict[str, bool] = {}

        # ------------------------------------------------
        # CONCURRENCY CONTROL
        # ------------------------------------------------
        self._submit_sem = asyncio.Semaphore(
            max_concurrent_submits
        )

        self._symbol_sem = asyncio.Semaphore(
            max_parallel_symbols
        )

        self._cooldown_ns = (
            per_symbol_cooldown_ms * 1_000_000
        )

        self._timeout = (
            hard_cycle_timeout_ms / 1000.0
        )

        # ------------------------------------------------
        # METRICS
        # ------------------------------------------------
        self.metrics: Dict[str, CycleMetrics] = {}

        # ------------------------------------------------
        # VALIDATION
        # ------------------------------------------------
        if not hasattr(
            self.pipeline,
            "current_positions"
        ):
            raise RuntimeError(
                "SignalPipeline missing current_positions"
            )

        self._safe_wire_feedback()

        logger.info(
            "ExecutionControllerV3 initialized"
        )

    # ============================================================
    # FEEDBACK WIRING
    # ============================================================

    def _safe_wire_feedback(self):

        def safe_attach(attr: str, handler):

            try:
                old = getattr(
                    self.feedback,
                    attr,
                    None
                )

                if callable(old):

                    setattr(
                        self.feedback,
                        attr,
                        lambda e: (
                            old(e),
                            handler(e)
                        )
                    )

                else:
                    setattr(
                        self.feedback,
                        attr,
                        handler
                    )

            except Exception:
                logger.warning(
                    "Feedback hook unavailable: %s",
                    attr
                )

        safe_attach(
            "on_filled",
            self._on_fill
        )

        safe_attach(
            "on_closed",
            self._on_close
        )

    # ============================================================
    # MAIN LOOP
    # ============================================================

    async def run_forever(
        self,
        symbols: list[str],
        cycle_ms: int = 250,
    ):

        logger.info(
            "ExecutionControllerV3 started | %s",
            symbols
        )

        while self._running:

            start = time.time_ns()

            async def run_symbol(sym: str):

                runtime_guard.heartbeat(
                    f"exec_{sym}"
                )

                async with self._symbol_sem:

                    try:
                        await self.run_cycle(sym)

                    except Exception:
                        logger.exception(
                            "run_cycle failure: %s",
                            sym
                        )

            await asyncio.gather(
                *(
                    run_symbol(s)
                    for s in symbols
                ),
                return_exceptions=True
            )

            elapsed_ms = (
                time.time_ns() - start
            ) / 1e6

            sleep_sec = max(
                0.01,
                (cycle_ms - elapsed_ms) / 1000
            )

            await asyncio.sleep(sleep_sec)

    # ============================================================
    # STOP
    # ============================================================

    def stop(self):

        logger.warning(
            "ExecutionControllerV3 stopping..."
        )

        self._running = False

    # ============================================================
    # CORE EXECUTION CYCLE
    # ============================================================

    async def run_cycle(self, symbol: str):

        # ------------------------------------------------
        # CIRCUIT BREAKER
        # ------------------------------------------------
        if self._circuit.get(symbol):

            logger.warning(
                "Circuit open for symbol: %s",
                symbol
            )

            return

        # ------------------------------------------------
        # METRICS
        # ------------------------------------------------
        metrics = self.metrics.setdefault(
            symbol,
            CycleMetrics()
        )

        metrics.total_cycles += 1

        now = time.time_ns()

        # ------------------------------------------------
        # COOLDOWN
        # ------------------------------------------------
        if (
            now - self._last_ts.get(symbol, 0)
            < self._cooldown_ns
        ):
            metrics.skipped += 1
            return

        try:

            # =================================================
            # LOAD STRATEGIES
            # =================================================
            strategies = self.strategy_loader.load(
                symbol
            )

            voted = self.strategy_voter.vote(
                strategies
            )

            signal = self.confirmation_engine.confirm(
                voted
            )

            # =================================================
            # NO SIGNAL
            # =================================================
            if (
                not signal
                or signal.bias == "NONE"
            ):
                metrics.no_signal += 1
                return

            # =================================================
            # SPREAD SAFETY
            # =================================================
            try:
                spread = self.account.get_spread(
                    symbol
                )

            except Exception:
                spread = 0.0

            if spread > 0.0015:

                logger.warning(
                    "Spread too high: %s | %s",
                    symbol,
                    spread
                )

                metrics.skipped += 1
                return

            # =================================================
            # ACCOUNT STATE
            # =================================================
            acct = self.account.get_account_state()

            get_pos = getattr(
                self.pipeline,
                "get_position",
                lambda s: self.pipeline.current_positions.get(
                    s,
                    0.0
                ),
            )

            market_state = {
                "balance": acct.balance,
                "spread": spread,
                "open_positions": get_pos(symbol),
                "ts": now,
            }

            # =================================================
            # AGGRESSION CONTEXT
            # =================================================
            ctx = AggressionContext(
                mode="NORMAL",
                confidence=getattr(
                    signal,
                    "confidence",
                    0.7
                ),
                reason="v3_core",
            )

            # =================================================
            # PIPELINE DECISION
            # =================================================
            decision = await asyncio.wait_for(
                asyncio.to_thread(
                    self.pipeline.evaluate,
                    signal,
                    ctx,
                    market_state,
                ),
                timeout=self._timeout,
            )

            if not decision.allow_trade:

                metrics.blocked += 1

                logger.info(
                    "Trade blocked: %s",
                    symbol
                )

                return

            # =================================================
            # BUILD TRADE INTENT
            # =================================================
            volume = max(
                0.001,
                round(
                    decision.risk_multiplier * 0.01,
                    6
                )
            )

            intent = TradeIntent(
                intent_id=next(
                    self._intent_id_gen
                ),
                symbol=symbol,
                side=decision.bias,
                volume=volume,
                sl=0.0,
                tp=0.0,
                account=getattr(
                    self.account,
                    "account_id",
                    "UNKNOWN"
                ),
                ts_ns=now,
            )

            # =================================================
            # RUNTIME GUARD
            # =================================================
            guard = ExecutionIntent(
                symbol=symbol,
                side=intent.side,
                volume=intent.volume,
                stop_loss=0.0,
                take_profit=0.0,
                confidence=getattr(
                    signal,
                    "confidence",
                    0.5
                ),
                strategy="v3_core",
            )

            validation = runtime_guard.validate_execution(
                guard
            )

            if not validation.allowed:

                logger.warning(
                    "Runtime guard blocked trade: %s",
                    symbol
                )

                metrics.blocked += 1
                return

            # =================================================
            # EXECUTION
            # =================================================
            async with self._submit_sem:

                logger.info(
                    "Submitting trade | %s | %s | volume=%s",
                    symbol,
                    intent.side,
                    intent.volume
                )

                await asyncio.to_thread(
                    self.executor.submit,
                    decision,
                    intent,
                )

            self._last_ts[symbol] = now

            metrics.submitted += 1

            logger.info(
                "Trade submitted successfully | %s",
                symbol
            )

        except Exception as e:

            metrics.errors += 1

            self._errors[symbol] = (
                self._errors.get(symbol, 0) + 1
            )

            logger.exception(
                "Execution error | %s | %s",
                symbol,
                e
            )

            # =================================================
            # CIRCUIT BREAKER
            # =================================================
            if self._errors[symbol] >= 10:

                self._circuit[symbol] = True

                logger.critical(
                    "SYMBOL CIRCUIT OPENED: %s",
                    symbol
                )

    # ============================================================
    # FEEDBACK HANDLERS
    # ============================================================

    def _on_fill(
        self,
        event: Dict[str, Any]
    ):

        try:

            sym = event.get("symbol")

            vol = float(
                event.get("volume", 0.0)
            )

            side = event.get("side")

            delta = (
                vol
                if side == "BUY"
                else -vol
            )

            self.pipeline.update_open_positions(
                sym,
                delta
            )

            logger.info(
                "Position updated on fill | %s",
                sym
            )

        except Exception:
            logger.warning(
                "Fill handler failed"
            )

    def _on_close(
        self,
        event: Dict[str, Any]
    ):

        try:

            sym = event.get("symbol")

            vol = float(
                event.get("volume", 0.0)
            )

            self.pipeline.update_open_positions(
                sym,
                -vol
            )

            logger.info(
                "Position updated on close | %s",
                sym
            )

        except Exception:
            logger.warning(
                "Close handler failed"
            )


# ============================================================
# BACKWARD COMPATIBILITY
# ============================================================

ExecutionController = ExecutionControllerV3
# core/unified_engine.py

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Any, Optional


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger("unified_engine")
logger.setLevel(logging.INFO)


# ============================================================
# UNIFIED FETCHER
# ============================================================

class UnifiedFetcher:
    """
    ============================================================
    STANDARD MARKET DATA WRAPPER
    ============================================================

    Wraps any market engine into a unified interface.

    Responsibilities:
    - snapshot access
    - stale feed checks
    - engine abstraction
    - symbol identity
    ============================================================
    """

    def __init__(self, raw_engine, symbol: str):

        self.engine = raw_engine
        self.symbol = symbol

    # ============================================================
    # SNAPSHOT
    # ============================================================

    def snapshot(self):

        return self.engine.snapshot_1m_np()

    # ============================================================
    # HEALTH
    # ============================================================

    def is_alive(self):

        return not self.is_feed_stale()

    def is_feed_stale(self):

        try:
            return self.engine.is_feed_stale()
        except Exception:
            return True

    # ============================================================
    # META
    # ============================================================

    def get_symbol(self):

        return self.symbol


# ============================================================
# UNIFIED ENGINE
# ============================================================

class UnifiedEngine:
    """
    ============================================================
    BLACKBOT INSTITUTIONAL CORE ENGINE
    ============================================================

    CENTRAL AUTHORITY OF THE ENTIRE SYSTEM.

    Responsibilities:
    ------------------------------------------------------------
    - receives approved strategy signals
    - validates risk
    - validates execution safety
    - validates feed health
    - coordinates execution
    - blocks dangerous trading conditions
    - becomes final trade authority
    ============================================================
    """

    def __init__(
        self,
        *,
        execution_controller,
        risk_governor=None,
        execution_validator=None,
        trading_safety=None,
        self_healing=None,
        metrics=None,
    ):

        # ====================================================
        # CORE SYSTEMS
        # ====================================================

        self.execution_controller = execution_controller

        self.risk_governor = risk_governor
        self.execution_validator = execution_validator
        self.trading_safety = trading_safety
        self.self_healing = self_healing

        self.metrics = metrics

        # ====================================================
        # STATE
        # ====================================================

        self.running = False

        self.lock = threading.RLock()

        self.processed_signals = 0
        self.executed_trades = 0
        self.rejected_trades = 0

        self.last_signal_time = 0.0

        logger.info("UnifiedEngine initialized")

    # ============================================================
    # START
    # ============================================================

    def start(self):

        self.running = True

        logger.info("UnifiedEngine started")

    # ============================================================
    # STOP
    # ============================================================

    def stop(self):

        self.running = False

        logger.warning("UnifiedEngine stopped")

    # ============================================================
    # MAIN ENTRY
    # ============================================================

    def process_signal(
        self,
        signal: Dict[str, Any]
    ) -> Dict[str, Any]:

        if not self.running:

            return {
                "status": "ENGINE_OFFLINE"
            }

        with self.lock:

            try:

                self.processed_signals += 1
                self.last_signal_time = time.time()

                symbol = signal.get("symbol", "UNKNOWN")

                logger.info(
                    f"PROCESSING SIGNAL | {symbol}"
                )

                # ====================================================
                # STEP 1 — GLOBAL SAFETY
                # ====================================================

                if self.trading_safety:

                    safe = self._safe_call(
                        self.trading_safety.is_safe_to_trade,
                        default=True
                    )

                    if not safe:

                        logger.warning(
                            f"TRADING BLOCKED BY SAFETY | {symbol}"
                        )

                        self.rejected_trades += 1

                        return {
                            "status": "BLOCKED_BY_SAFETY"
                        }

                # ====================================================
                # STEP 2 — RISK GOVERNOR
                # ====================================================

                if self.risk_governor:

                    allowed = self._safe_call(
                        self.risk_governor.allow_trade,
                        signal,
                        default=True
                    )

                    if not allowed:

                        logger.warning(
                            f"TRADE BLOCKED BY RISK GOVERNOR | {symbol}"
                        )

                        self.rejected_trades += 1

                        return {
                            "status": "BLOCKED_BY_RISK"
                        }

                # ====================================================
                # STEP 3 — EXECUTION VALIDATION
                # ====================================================

                if self.execution_validator:

                    valid = self._safe_call(
                        self.execution_validator.validate,
                        signal,
                        default=True
                    )

                    if not valid:

                        logger.warning(
                            f"TRADE FAILED VALIDATION | {symbol}"
                        )

                        self.rejected_trades += 1

                        return {
                            "status": "VALIDATION_FAILED"
                        }

                # ====================================================
                # STEP 4 — EXECUTION
                # ====================================================

                result = self.execution_controller.execute_signal(
                    signal
                )

                self.executed_trades += 1

                logger.info(
                    f"TRADE EXECUTED | {result}"
                )

                return {
                    "status": "EXECUTED",
                    "result": result,
                }

            except Exception as e:

                logger.exception(
                    f"UnifiedEngine process failure: {e}"
                )

                self.rejected_trades += 1

                return {
                    "status": "ERROR",
                    "error": str(e),
                }

    # ============================================================
    # SAFE CALL
    # ============================================================

    def _safe_call(
        self,
        fn,
        *args,
        default=None,
        **kwargs,
    ):

        try:

            return fn(*args, **kwargs)

        except Exception as e:

            logger.exception(
                f"Subsystem failure: {e}"
            )

            return default

    # ============================================================
    # HEALTH
    # ============================================================

    def health(self) -> Dict[str, Any]:

        return {
            "running": self.running,
            "processed_signals": self.processed_signals,
            "executed_trades": self.executed_trades,
            "rejected_trades": self.rejected_trades,
            "last_signal_time": self.last_signal_time,
        }
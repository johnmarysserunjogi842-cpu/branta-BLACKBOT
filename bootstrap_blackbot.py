"""
bootstrap_blackbot.py
============================================================
Institutional BlackBot Bootstrap Authority
============================================================

Purpose
-------
Centralized startup authority for:

- runtime initialization
- lifecycle orchestration
- startup validation
- graceful shutdown
- service ownership
- logging bootstrap
- frozen executable parity
- async orchestration
- boot supervision
- deterministic startup sequencing

Design Goals
------------
- institutional-grade
- PyInstaller-safe
- Windows-safe
- async-safe
- deterministic startup
- minimal patch integration
- centralized ownership
- scalable architecture
- production-safe shutdown
- resilient startup handling

IMPORTANT
---------
This file becomes the PRIMARY application entrypoint.

DO NOT directly run:
    main.py

Instead use:
    python bootstrap_blackbot.py

or:
    run_app.py

Architecture
------------
bootstrap
    ↓
runtime
    ↓
lifecycle
    ↓
startup validation
    ↓
service registration
    ↓
application runtime
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

# ============================================================
# CORE RUNTIME
# ============================================================

from core.runtime import runtime
from core.lifecycle import lifecycle

# ============================================================
# BOOT LAYER
# ============================================================

from boot.startup_guard import StartupGuard

# ------------------------------------------------------------
# OPTIONAL OFFLINE UI
# ------------------------------------------------------------

try:
    from boot.offline_ui import OfflineUI
except Exception:
    OfflineUI = None

# ============================================================
# APPLICATION ENTRY
# ============================================================

from main import main as blackbot_main

# ============================================================
# LOGGER
# ============================================================

logger = logging.getLogger("bootstrap.blackbot")

# ============================================================
# CONSTANTS
# ============================================================

STARTUP_TIMEOUT = 60
SHUTDOWN_TIMEOUT = 20
HEALTHCHECK_INTERVAL = 15
MAX_RESTART_ATTEMPTS = 3

# ============================================================
# BOOT STATE
# ============================================================

@dataclass(slots=True)
class BootstrapState:
    started_at: float = field(default_factory=time.time)

    initialized: bool = False
    validated: bool = False
    services_started: bool = False

    shutting_down: bool = False

    restart_attempts: int = 0

    last_error: Optional[str] = None

# ============================================================
# MAIN BOOTSTRAP AUTHORITY
# ============================================================

class BlackBotBootstrap:
    """
    Institutional startup authority.

    Responsibilities:
    - runtime bootstrap
    - lifecycle ownership
    - startup sequencing
    - graceful shutdown
    - crash containment
    - startup validation
    - async supervision
    """

    def __init__(self) -> None:
        self.state = BootstrapState()

        self._shutdown_event = asyncio.Event()

        self._main_task: Optional[asyncio.Task] = None

        self._startup_guard = StartupGuard(
            base_dir=runtime.root_dir,
        )

    # ========================================================
    # INITIALIZATION
    # ========================================================

    async def initialize(self) -> None:
        """
        Deterministic runtime initialization.
        """

        logger.info("Initializing bootstrap runtime...")

        runtime.initialize_logging(
            filename="blackbot.log",
            console=True,
        )

        runtime.install_signal_handlers()

        runtime.validate_environment()

        self._install_signal_hooks()

        self.state.initialized = True

        logger.info("Runtime initialization complete.")

    # ========================================================
    # SIGNALS
    # ========================================================

    def _install_signal_hooks(self) -> None:
        """
        Safe async shutdown hooks.
        """

        def _handler(signum: int, _frame: Any) -> None:
            logger.warning(
                "Received shutdown signal: %s",
                signum,
            )

            loop = None

            with contextlib.suppress(Exception):
                loop = asyncio.get_running_loop()

            if loop and not loop.is_closed():
                loop.create_task(
                    self.shutdown(
                        reason=f"signal:{signum}"
                    )
                )

        signals = [signal.SIGINT]

        if hasattr(signal, "SIGTERM"):
            signals.append(signal.SIGTERM)

        for sig in signals:
            with contextlib.suppress(Exception):
                signal.signal(sig, _handler)

    # ========================================================
    # STARTUP VALIDATION
    # ========================================================

    async def validate_startup(self) -> None:
        """
        Institutional startup readiness validation.
        """

        logger.info("Running startup validation...")

        report = await asyncio.wait_for(
            self._startup_guard.validate_startup(),
            timeout=STARTUP_TIMEOUT,
        )

        if not report.ready:
            logger.error(
                "Startup validation failed: %s",
                report.failure_reason,
            )

            if OfflineUI:
                try:
                    ui = OfflineUI(report)
                    ui.show()
                except Exception:
                    logger.exception(
                        "Offline UI failed"
                    )

            raise RuntimeError(
                f"Startup validation failed: {report.failure_reason}"
            )

        self.state.validated = True

        logger.info("Startup validation successful.")

    # ========================================================
    # SERVICES
    # ========================================================

    async def register_services(self) -> None:
        """
        Centralized lifecycle service registration.

        Future systems should register ALL services here.
        """

        logger.info("Registering lifecycle services...")

        # ----------------------------------------------------
        # HEALTH MONITOR
        # ----------------------------------------------------

        lifecycle.register_service(
            name="health-monitor",
            startup=self._start_health_monitor,
            shutdown=self._stop_health_monitor,
            critical=False,
        )

        logger.info("Lifecycle services registered.")

    async def _start_health_monitor(self) -> None:
        lifecycle.create_task(
            self._health_monitor_loop(),
            name="health-monitor-loop",
            critical=False,
            restartable=True,
            tags={"monitoring"},
        )

    async def _stop_health_monitor(self) -> None:
        logger.info("Health monitor shutdown complete.")

    # ========================================================
    # HEALTH MONITORING
    # ========================================================

    async def _health_monitor_loop(self) -> None:
        """
        Lightweight operational heartbeat.
        """

        while not lifecycle.is_stopping:
            snapshot = lifecycle.snapshot()

            logger.info(
                "Lifecycle health | state=%s tasks=%s services=%s",
                snapshot["state"],
                snapshot["tasks"],
                snapshot["services"],
            )

            await asyncio.sleep(HEALTHCHECK_INTERVAL)

    # ========================================================
    # MAIN APPLICATION
    # ========================================================

    async def start_application(self) -> None:
        """
        Start primary BlackBot runtime.
        """

        logger.info("Starting BlackBot application...")

        if asyncio.iscoroutinefunction(blackbot_main):
            self._main_task = lifecycle.create_task(
                blackbot_main(),
                name="blackbot-main",
                critical=True,
                tags={"core"},
            )
        else:
            loop = asyncio.get_running_loop()

            self._main_task = lifecycle.create_task(
                loop.run_in_executor(
                    None,
                    blackbot_main,
                ),
                name="blackbot-main-threaded",
                critical=True,
                tags={"core"},
            )

    # ========================================================
    # RUNTIME LOOP
    # ========================================================

    async def run(self) -> None:
        """
        Full deterministic startup sequence.
        """

        try:
            logger.info("Bootstrapping BlackBot...")

            # ------------------------------------------------
            # RUNTIME INIT
            # ------------------------------------------------

            await self.initialize()

            # ------------------------------------------------
            # VALIDATION
            # ------------------------------------------------

            await self.validate_startup()

            # ------------------------------------------------
            # SERVICE REGISTRATION
            # ------------------------------------------------

            await self.register_services()

            # ------------------------------------------------
            # LIFECYCLE START
            # ------------------------------------------------

            await lifecycle.start_services()

            self.state.services_started = True

            # ------------------------------------------------
            # APPLICATION START
            # ------------------------------------------------

            await self.start_application()

            logger.info("BlackBot fully operational.")

            # ------------------------------------------------
            # WAIT FOR SHUTDOWN
            # ------------------------------------------------

            await lifecycle.wait_for_shutdown()

        except asyncio.CancelledError:
            logger.warning("Bootstrap cancelled.")
            raise

        except Exception as exc:
            self.state.last_error = str(exc)

            logger.exception(
                "Fatal bootstrap failure"
            )

            raise

        finally:
            await self.shutdown(
                reason="bootstrap-finalizer"
            )

    # ========================================================
    # SHUTDOWN
    # ========================================================

    async def shutdown(
        self,
        *,
        reason: str,
    ) -> None:
        """
        Deterministic coordinated shutdown.
        """

        if self.state.shutting_down:
            return

        self.state.shutting_down = True

        logger.warning(
            "Bootstrap shutdown initiated: %s",
            reason,
        )

        try:
            await asyncio.wait_for(
                lifecycle.shutdown(
                    reason=reason,
                    timeout=SHUTDOWN_TIMEOUT,
                ),
                timeout=SHUTDOWN_TIMEOUT + 5,
            )

        except Exception:
            logger.exception(
                "Lifecycle shutdown failure"
            )

        logger.info("Bootstrap shutdown complete.")

# ============================================================
# ENTRYPOINT
# ============================================================

async def _async_main() -> int:
    """
    Async entrypoint.
    """

    bootstrap = BlackBotBootstrap()

    await bootstrap.run()

    return 0


def main() -> None:
    """
    Synchronous executable entrypoint.

    PyInstaller-safe.
    """

    try:
        exit_code = asyncio.run(_async_main())

    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
        exit_code = 1

    except Exception:
        logger.exception("Fatal process failure")
        exit_code = 1

    sys.exit(exit_code)

# ============================================================
# EXECUTION
# ============================================================

if __name__ == "__main__":
    main()

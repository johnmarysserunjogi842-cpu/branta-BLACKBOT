# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from boot.startup_guard import StartupGuard
from boot.offline_ui import OfflineUI


# ============================================================
# PATH RESOLUTION (PYINSTALLER SAFE)
# ============================================================

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent


# ============================================================
# DIRECTORIES
# ============================================================

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# LOGGING
# ============================================================

LOG_FILE = LOG_DIR / "boot.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("BOOT")


# ============================================================
# CONSTANTS
# ============================================================

MAX_CRASH_RESTARTS = 5
RESTART_COOLDOWN_SECONDS = 10
VALIDATION_TIMEOUT = 30
SHUTDOWN_TIMEOUT = 15


# ============================================================
# RUNTIME STATE
# ============================================================

@dataclass(slots=True)
class BootState:
    session_id: str
    startup_time: float
    crash_count: int = 0
    shutting_down: bool = False


# ============================================================
# BOOT SUPERVISOR
# ============================================================

class BootSupervisor:
    """
    Institutional-grade runtime bootstrap supervisor.

    RESPONSIBILITIES:
    - Environment initialization
    - Startup validation routing
    - Runtime lifecycle supervision
    - Crash containment
    - Safe recovery routing

    NON-RESPONSIBILITIES:
    - Trading logic
    - MT5 execution
    - Strategy initialization
    - Market orchestration
    """

    def __init__(self) -> None:
        self.state = BootState(
            session_id=str(int(time.time() * 1000)),
            startup_time=time.time(),
        )

        self.runtime_app = None
        self.runtime_task: Optional[asyncio.Task] = None

        self.shutdown_event = asyncio.Event()

    # ========================================================
    # ENVIRONMENT INITIALIZATION
    # ========================================================

    async def initialize_environment(self) -> None:
        logger.info("Initializing boot environment...")

        load_dotenv(BASE_DIR / ".env")

        os.environ.setdefault("PYTHONASYNCIODEBUG", "0")

        if sys.platform.startswith("win"):
            asyncio.set_event_loop_policy(
                asyncio.WindowsSelectorEventLoopPolicy()
            )

        logger.info("Environment initialization complete")

    # ========================================================
    # SIGNAL HANDLERS
    # ========================================================

    def install_signal_handlers(self) -> None:
        logger.info("Installing shutdown signal handlers...")

        def _handle_signal(signum, frame):
            logger.warning(f"Received shutdown signal: {signum}")
            self.state.shutting_down = True
            self.shutdown_event.set()

        signal.signal(signal.SIGINT, _handle_signal)

        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle_signal)

    # ========================================================
    # VALIDATION
    # ========================================================

    async def validate_system(self):
        logger.info("Starting system validation...")

        guard = StartupGuard(base_dir=BASE_DIR)

        try:
            report = await asyncio.wait_for(
                guard.validate_system(),
                timeout=VALIDATION_TIMEOUT,
            )

            logger.info(
                "Validation complete | "
                f"ready={report.ready} | "
                f"internet={getattr(report, 'internet', None)} | "
                f"bridge={getattr(report, 'bridge', None)} | "
                f"mt5={getattr(report, 'mt5', None)}"
            )

            return report

        except asyncio.TimeoutError:
            logger.exception("Startup validation timeout")
            raise RuntimeError("STARTUP_VALIDATION_TIMEOUT")

        except Exception:
            logger.exception("System validation crashed")
            raise

    # ========================================================
    # OFFLINE RECOVERY MODE
    # ========================================================

    async def launch_offline_mode(self, report) -> None:
        logger.warning("Entering offline recovery mode...")

        ui = OfflineUI(
            startup_report=report,
            base_dir=BASE_DIR,
        )

        await ui.run()

    # ========================================================
    # RUNTIME LAUNCH
    # ========================================================

    async def launch_runtime(self) -> None:
        """
        Delayed import prevents premature initialization.
        """

        logger.info("Launching BLACKBOT runtime...")

        try:
            from main import Application

            self.runtime_app = Application()

            self.runtime_task = asyncio.create_task(
                self.runtime_app.start(),
                name="blackbot_runtime",
            )

            await self.runtime_task

        except asyncio.CancelledError:
            logger.warning("Runtime task cancelled")
            raise

        except Exception:
            logger.exception("Runtime crashed")
            raise

    # ========================================================
    # SAFE SHUTDOWN
    # ========================================================

    async def shutdown_runtime(self) -> None:
        logger.warning("Starting safe runtime shutdown...")

        try:
            if self.runtime_app and hasattr(self.runtime_app, "shutdown"):
                await asyncio.wait_for(
                    self.runtime_app.shutdown(),
                    timeout=SHUTDOWN_TIMEOUT,
                )

        except asyncio.TimeoutError:
            logger.error("Runtime shutdown timeout")

        except Exception:
            logger.exception("Runtime shutdown failure")

        finally:
            if self.runtime_task and not self.runtime_task.done():
                self.runtime_task.cancel()

                try:
                    await self.runtime_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass

            logger.info("Runtime shutdown complete")

    # ========================================================
    # SUPERVISOR LOOP
    # ========================================================

    async def supervisor_loop(self) -> None:
        """
        Institutional fail-closed supervisor loop.
        """

        while not self.state.shutting_down:

            try:
                report = await self.validate_system()

                if not getattr(report, "ready", False):
                    logger.warning("System not ready")

                    await self.launch_offline_mode(report)

                    await asyncio.sleep(5)
                    continue

                logger.info("System healthy — starting runtime")

                await self.launch_runtime()

                logger.warning("Runtime exited normally")
                break

            except asyncio.CancelledError:
                logger.warning("Supervisor cancelled")
                break

            except Exception as exc:
                self.state.crash_count += 1

                logger.exception(
                    "Supervisor captured fatal runtime failure | "
                    f"crash_count={self.state.crash_count} | "
                    f"error={exc}"
                )

                await self.shutdown_runtime()

                if self.state.crash_count >= MAX_CRASH_RESTARTS:
                    logger.critical(
                        "Maximum restart threshold reached — entering hard fail-safe"
                    )
                    break

                logger.warning(
                    f"Cooldown before restart: "
                    f"{RESTART_COOLDOWN_SECONDS}s"
                )

                await asyncio.sleep(RESTART_COOLDOWN_SECONDS)

    # ========================================================
    # ENTRYPOINT
    # ========================================================

    async def run(self) -> None:
        logger.info(
            f"BOOT SESSION STARTED | "
            f"session_id={self.state.session_id}"
        )

        await self.initialize_environment()

        self.install_signal_handlers()

        try:
            await self.supervisor_loop()

        finally:
            self.state.shutting_down = True

            await self.shutdown_runtime()

            logger.info("BOOT SUPERVISOR TERMINATED")


# ============================================================
# MAIN ENTRYPOINT
# ============================================================

async def _async_main() -> None:
    supervisor = BootSupervisor()
    await supervisor.run()


def main() -> None:
    try:
        asyncio.run(_async_main())

    except KeyboardInterrupt:
        logger.warning("Boot interrupted by user")

    except Exception:
        logger.exception("Fatal boot failure")
        sys.exit(1)


if __name__ == "__main__":
    main()

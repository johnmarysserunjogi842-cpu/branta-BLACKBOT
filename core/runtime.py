"""
core/runtime.py

Institutional Runtime Bootstrap Layer
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import platform
import signal
import sys
import tempfile
import threading
import time

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Mapping
from collections.abc import Coroutine


# ============================================================
# INTERNAL CONSTANTS
# ============================================================

_ENV_FROZEN: Final[bool] = bool(getattr(sys, "frozen", False))
_ENV_MEIPASS: Final[str | None] = getattr(sys, "_MEIPASS", None)

_APP_NAME: Final[str] = "blackbot"


# ============================================================
# RUNTIME EXCEPTIONS
# ============================================================

class RuntimeBootstrapError(RuntimeError):
    """Critical runtime initialization failure."""


class ConfigurationError(RuntimeError):
    """Configuration resolution failure."""


# ============================================================
# PLATFORM INFO
# ============================================================

@dataclass(frozen=True, slots=True)
class PlatformInfo:
    system: str
    release: str
    version: str
    machine: str
    python_version: str
    frozen: bool


# ============================================================
# MAIN RUNTIME CORE
# ============================================================

class Runtime:

    def __init__(self) -> None:
        self._lock = threading.RLock()

        self._initialized = False
        self._logger_initialized = False
        self._startup_ts = time.perf_counter()
        self._shutdown = threading.Event()

        self._tasks: set[asyncio.Task[Any]] = set()

        self._bootstrap()

    # ========================================================
    # TASK SAFETY (SELF-HEALING CORE FIX)
    # ========================================================

    def create_task(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
        critical: bool = False,
    ) -> asyncio.Task[Any]:

        task = asyncio.create_task(coro, name=name)

        self._tasks.add(task)

        def _done_callback(t: asyncio.Task[Any]) -> None:
            self._tasks.discard(t)

            try:
                exc = t.exception()
                if exc:
                    logging.getLogger(__name__).error(
                        "Runtime task crashed | name=%s | error=%s",
                        t.get_name(),
                        exc,
                    )

                    # 🔥 SELF-HEALING HOOK (optional future restart logic)
                    if critical:
                        logging.getLogger(__name__).critical(
                            "Critical task failed — runtime may destabilize"
                        )

            except Exception:
                pass

        task.add_done_callback(_done_callback)

        return task

    def health(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                "uptime": self.uptime_seconds,
                "tasks": len(self._tasks),
                "pid": self.process_id,
                "shutdown": self.shutting_down,
            }
        )

    # ========================================================
    # SHUTDOWN CONTROL (FIX)
    # ========================================================

    def shutdown(self) -> None:
        self._shutdown.set()

    async def async_shutdown(self) -> None:
        self._shutdown.set()

        for t in list(self._tasks):
            t.cancel()

        await asyncio.gather(*self._tasks, return_exceptions=True)

    @property
    def shutting_down(self) -> bool:
        return self._shutdown.is_set()

    # ========================================================
    # BOOTSTRAP
    # ========================================================

    def _bootstrap(self) -> None:
        with self._lock:
            if self._initialized:
                return

            self._configure_environment()
            self._configure_platform_runtime()
            self._ensure_directories()

            self._initialized = True

    # ========================================================
    # ENVIRONMENT
    # ========================================================

    def _configure_environment(self) -> None:
        os.environ.setdefault("PYTHONUNBUFFERED", "1")
        os.environ.setdefault("PYTHONUTF8", "1")
        os.environ.setdefault("PYTHONASYNCIODEBUG", "0")
        os.environ.setdefault("TZ", "UTC")

        if os.name == "nt":
            os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "0")

    # ========================================================
    # WINDOWS FIX
    # ========================================================

    def _configure_platform_runtime(self) -> None:
        if sys.platform.startswith("win"):
            try:
                asyncio.set_event_loop_policy(
                    asyncio.WindowsSelectorEventLoopPolicy()
                )
            except Exception:
                pass

    # ========================================================
    # PATH RESOLUTION
    # ========================================================

    @cached_property
    def root_dir(self) -> Path:
        if _ENV_FROZEN:
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent.parent

    @cached_property
    def logs_dir(self) -> Path:
        return self.root_dir / "logs"

    @cached_property
    def config_dir(self) -> Path:
        return self.root_dir / "config"

    @cached_property
    def data_dir(self) -> Path:
        return self.root_dir / "data"

    @cached_property
    def cache_dir(self) -> Path:
        return self.root_dir / "cache"

    @cached_property
    def temp_dir(self) -> Path:
        return Path(tempfile.gettempdir()) / _APP_NAME

    # ========================================================
    # DIRECTORY SETUP
    # ========================================================

    def _ensure_directories(self) -> None:
        for d in (
            self.logs_dir,
            self.data_dir,
            self.cache_dir,
            self.temp_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    # ========================================================
    # LOGGING FIX (SAFE INIT)
    # ========================================================

    def initialize_logging(self, level=logging.INFO) -> None:
        with self._lock:
            if self._logger_initialized:
                return

            fmt = logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            )

            root = logging.getLogger()
            root.setLevel(level)

            for h in root.handlers[:]:
                root.removeHandler(h)

            file_handler = logging.handlers.RotatingFileHandler(
                self.logs_dir / "runtime.log",
                maxBytes=10_000_000,
                backupCount=5,
            )
            file_handler.setFormatter(fmt)

            root.addHandler(file_handler)
            root.addHandler(logging.StreamHandler(sys.stdout))

            self._logger_initialized = True

    # ========================================================
    # SIGNAL HANDLING
    # ========================================================

    def install_signal_handlers(self) -> None:
        logger = logging.getLogger(__name__)

        def handler(signum, _frame):
            logger.warning("Shutdown signal received: %s", signum)
            self._shutdown.set()

        for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
            if sig:
                try:
                    signal.signal(sig, handler)
                except Exception:
                    pass

    # ========================================================
    # DIAGNOSTICS FIX
    # ========================================================

    def snapshot(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                "root_dir": str(self.root_dir),
                "logs_dir": str(self.logs_dir),
                "config_dir": str(self.config_dir),
                "data_dir": str(self.data_dir),
                "cache_dir": str(self.cache_dir),
                "temp_dir": str(self.temp_dir),
                "frozen": self.is_frozen,
                "python": sys.version,
                "pid": self.process_id,
                "uptime_seconds": self.uptime_seconds,
                "tasks": len(self._tasks),
            }
        )

    # ========================================================
    # PROPERTIES
    # ========================================================

    @property
    def is_frozen(self) -> bool:
        return _ENV_FROZEN

    @property
    def process_id(self) -> int:
        return os.getpid()

    @property
    def uptime_seconds(self) -> float:
        return round(time.perf_counter() - self._startup_ts, 3)


# ============================================================
# SINGLETON
# ============================================================

runtime = Runtime()

__all__ = [
    "runtime",
    "Runtime",
    "RuntimeBootstrapError",
    "ConfigurationError",
    "PlatformInfo",
]

"""
core/lifecycle.py

Institutional Lifecycle + Task Orchestration System
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
import traceback

from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Optional,
)

# ============================================================
# LOGGER
# ============================================================

logger = logging.getLogger(__name__)

# ============================================================
# STATES
# ============================================================


class LifecycleState(str, Enum):
    INITIALIZED = "initialized"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


# ============================================================
# TASK METADATA
# ============================================================


@dataclass(slots=True)
class ManagedTask:
    name: str
    task: asyncio.Task[Any]
    created_at: float
    critical: bool = False
    restartable: bool = False
    tags: set[str] = field(default_factory=set)


# ============================================================
# SERVICE METADATA
# ============================================================


@dataclass(slots=True)
class LifecycleService:
    name: str
    startup: Callable[[], Awaitable[Any]]
    shutdown: Optional[Callable[[], Awaitable[Any]]] = None
    critical: bool = True
    started: bool = False


# ============================================================
# EXCEPTIONS
# ============================================================


class LifecycleError(RuntimeError):
    """Base lifecycle exception."""


class ServiceStartupError(LifecycleError):
    """Critical service startup failure."""


# ============================================================
# MAIN LIFECYCLE MANAGER
# ============================================================


class LifecycleManager:
    """
    Central async lifecycle authority.

    Responsibilities:
    - task ownership
    - graceful shutdown
    - service startup ordering
    - crash containment
    - orphan prevention
    - runtime coordination
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

        self._state = LifecycleState.INITIALIZED

        self._tasks: dict[str, ManagedTask] = {}
        self._services: dict[str, LifecycleService] = {}

        self._shutdown_event = asyncio.Event()

        self._started_at: float | None = None
        self._stopped_at: float | None = None

    # ========================================================
    # STATE
    # ========================================================

    @property
    def state(self) -> LifecycleState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state == LifecycleState.RUNNING

    @property
    def is_stopping(self) -> bool:
        return self._state == LifecycleState.STOPPING

    # ========================================================
    # SERVICES
    # ========================================================

    def register_service(
        self,
        *,
        name: str,
        startup: Callable[[], Awaitable[Any]],
        shutdown: Callable[[], Awaitable[Any]] | None = None,
        critical: bool = True,
    ) -> None:
        """
        Register managed service.
        """

        with self._lock:
            if name in self._services:
                raise LifecycleError(
                    f"Service already registered: {name}"
                )

            self._services[name] = LifecycleService(
                name=name,
                startup=startup,
                shutdown=shutdown,
                critical=critical,
            )

    async def start_services(self) -> None:
        """
        Deterministic service startup.

        Startup order = registration order.
        """

        self._state = LifecycleState.STARTING

        logger.info("Starting lifecycle services...")

        for service in self._services.values():
            try:
                logger.info(
                    "Starting service: %s",
                    service.name,
                )

                await service.startup()

                service.started = True

                logger.info(
                    "Service started: %s",
                    service.name,
                )

            except Exception as exc:
                logger.exception(
                    "Service startup failed: %s",
                    service.name,
                )

                if service.critical:
                    self._state = LifecycleState.FAILED

                    raise ServiceStartupError(
                        f"Critical service failed: {service.name}"
                    ) from exc

        self._state = LifecycleState.RUNNING
        self._started_at = time.time()

        logger.info("Lifecycle manager running.")

    # ========================================================
    # TASK MANAGEMENT
    # ========================================================

    def create_task(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
        critical: bool = False,
        restartable: bool = False,
        tags: set[str] | None = None,
    ) -> asyncio.Task[Any]:
        """
        Institutional task creation.

        Replaces raw asyncio.create_task().
        """

        if self.is_stopping:
            raise LifecycleError(
                "Cannot create tasks during shutdown"
            )

        task_name = name or f"task-{len(self._tasks) + 1}"

        async def _runner() -> Any:
            try:
                return await coro

            except asyncio.CancelledError:
                logger.info(
                    "Task cancelled: %s",
                    task_name,
                )
                raise

            except Exception:
                logger.error(
                    "Task crashed: %s\n%s",
                    task_name,
                    traceback.format_exc(),
                )

                if critical:
                    logger.critical(
                        "Critical task failure: %s",
                        task_name,
                    )

                    await self.shutdown(
                        reason=(
                            f"Critical task failure: "
                            f"{task_name}"
                        )
                    )

                raise

        task = asyncio.create_task(
            _runner(),
            name=task_name,
        )

        managed = ManagedTask(
            name=task_name,
            task=task,
            created_at=time.time(),
            critical=critical,
            restartable=restartable,
            tags=tags or set(),
        )

        self._tasks[task_name] = managed

        task.add_done_callback(
            lambda _: self._cleanup_task(task_name)
        )

        logger.debug(
            "Task registered: %s",
            task_name,
        )

        return task

    def _cleanup_task(self, name: str) -> None:
        """
        Remove completed task safely.
        """

        self._tasks.pop(name, None)

    # ========================================================
    # TASK INTROSPECTION
    # ========================================================

    def active_tasks(self) -> list[str]:
        return list(self._tasks.keys())

    def task_count(self) -> int:
        return len(self._tasks)

    # ========================================================
    # WAIT UTILITIES
    # ========================================================

    async def wait_for_shutdown(self) -> None:
        """
        Await shutdown signal.
        """

        await self._shutdown_event.wait()

    # ========================================================
    # SHUTDOWN
    # ========================================================

    async def shutdown(
        self,
        *,
        reason: str = "normal shutdown",
        timeout: float = 15.0,
    ) -> None:
        """
        Coordinated graceful shutdown.
        """

        if self._state in (
            LifecycleState.STOPPING,
            LifecycleState.STOPPED,
        ):
            return

        logger.warning(
            "Lifecycle shutdown initiated: %s",
            reason,
        )

        self._state = LifecycleState.STOPPING

        # ----------------------------------------------------
        # CANCEL TASKS
        # ----------------------------------------------------

        pending: list[asyncio.Task[Any]] = []

        current_task = asyncio.current_task()

        for managed in list(self._tasks.values()):
            task = managed.task

            if task.done():
                continue

            if task is current_task:
                continue

            task.cancel()
            pending.append(task)

        if pending:
            logger.info(
                "Awaiting %s task cancellations...",
                len(pending),
            )

            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    asyncio.gather(
                        *pending,
                        return_exceptions=True,
                    ),
                    timeout=timeout,
                )

        # ----------------------------------------------------
        # SHUTDOWN SERVICES
        # ----------------------------------------------------

        services = list(self._services.values())
        services.reverse()

        for service in services:
            if not service.started:
                continue

            if service.shutdown is None:
                continue

            try:
                logger.info(
                    "Stopping service: %s",
                    service.name,
                )

                await service.shutdown()

            except Exception:
                logger.exception(
                    "Service shutdown failed: %s",
                    service.name,
                )

        self._tasks.clear()

        self._state = LifecycleState.STOPPED
        self._stopped_at = time.time()

        self._shutdown_event.set()

        logger.info("Lifecycle shutdown complete.")

    # ========================================================
    # HEALTH SNAPSHOT
    # ========================================================

    def snapshot(self) -> dict[str, Any]:
        """
        Operational diagnostics snapshot.
        """

        uptime = None

        if self._started_at is not None:
            uptime = (
                time.time() - self._started_at
            )

        return {
            "state": self._state.value,
            "tasks": len(self._tasks),
            "services": len(self._services),
            "uptime_seconds": uptime,
            "active_tasks": list(self._tasks.keys()),
            "registered_services": list(
                self._services.keys()
            ),
        }


# ============================================================
# GLOBAL SINGLETON
# ============================================================

lifecycle = LifecycleManager()

# ============================================================
# PUBLIC EXPORTS
# ============================================================

__all__ = [
    "LifecycleManager",
    "LifecycleState",
    "LifecycleService",
    "ManagedTask",
    "LifecycleError",
    "ServiceStartupError",
    "lifecycle",
    ]

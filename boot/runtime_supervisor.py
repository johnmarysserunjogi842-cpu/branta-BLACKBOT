from __future__ import annotations

import asyncio
import time
import logging
from typing import Dict, Callable, Optional, Any


logger = logging.getLogger("runtime_supervisor")
logger.setLevel(logging.INFO)


# ============================================================
# TASK STATE
# ============================================================

class TaskState:
    def __init__(self, name: str, task_factory: Callable[[], asyncio.Task]):
        self.name = name
        self.task_factory = task_factory
        self.task: Optional[asyncio.Task] = None

        self.last_heartbeat: float = time.time()
        self.crash_count: int = 0
        self.disabled: bool = False


# ============================================================
# RUNTIME SUPERVISOR
# ============================================================

class RuntimeSupervisor:

    def __init__(
        self,
        heartbeat_timeout: float = 10.0,
        max_restarts: int = 5,
        restart_cooldown: float = 3.0,
    ):

        self.tasks: Dict[str, TaskState] = {}

        self.heartbeat_timeout = heartbeat_timeout
        self.max_restarts = max_restarts
        self.restart_cooldown = restart_cooldown

        self._running = False

    # ========================================================
    # REGISTER TASK
    # ========================================================

    def register_task(
        self,
        name: str,
        task_factory: Callable[[], asyncio.Task],
    ):

        self.tasks[name] = TaskState(name, task_factory)

        logger.info(f"Task registered: {name}")

    # ========================================================
    # START TASK
    # ========================================================

    def _start_task(self, state: TaskState):

        if state.disabled:
            logger.warning(f"Task disabled: {state.name}")
            return

        try:
            state.task = state.task_factory()

            logger.info(f"Task started: {state.name}")

        except Exception as e:
            logger.error(f"Failed to start task {state.name}: {e}")

    # ========================================================
    # HEARTBEAT UPDATE
    # ========================================================

    def heartbeat(self, name: str):

        if name in self.tasks:
            self.tasks[name].last_heartbeat = time.time()

    # ========================================================
    # MONITOR LOOP
    # ========================================================

    async def monitor(self):

        self._running = True

        logger.info("Runtime Supervisor started")

        while self._running:

            now = time.time()

            for name, state in self.tasks.items():

                # Skip disabled tasks
                if state.disabled:
                    continue

                # =================================================
                # TASK NOT RUNNING
                # =================================================

                if state.task is None or state.task.done():

                    if state.task and state.task.done():

                        exc = state.task.exception()

                        state.crash_count += 1

                        logger.error(
                            f"Task crashed: {name} | crashes={state.crash_count} | error={exc}"
                        )

                    # restart logic
                    if state.crash_count >= self.max_restarts:
                        state.disabled = True
                        logger.critical(f"Task permanently disabled: {name}")
                        continue

                    time.sleep(self.restart_cooldown)
                    self._start_task(state)
                    continue

                # =================================================
                # HEARTBEAT CHECK
                # =================================================

                if now - state.last_heartbeat > self.heartbeat_timeout:

                    logger.warning(f"Heartbeat stale: {name}")

                    state.task.cancel()

                    state.crash_count += 1

                    await asyncio.sleep(self.restart_cooldown)

                    self._start_task(state)

            await asyncio.sleep(1)

    # ========================================================
    # STOP SUPERVISOR
    # ========================================================

    def stop(self):

        self._running = False

        logger.info("Runtime Supervisor stopped")
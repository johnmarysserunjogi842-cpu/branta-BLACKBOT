# ui_scheduler.py

from __future__ import annotations

import logging
from queue import Queue, Empty
from typing import Callable

logger = logging.getLogger(__name__)


class UIScheduler:
    """
    =========================================================
    🚀 BLACKBOT v4.1 UI SCHEDULER CORE
    =========================================================

    ✔ Thread-safe UI execution bridge
    ✔ Deterministic Tkinter event pump
    ✔ Safe trade/event rendering pipeline
    ✔ Crash-isolated execution queue
    =========================================================
    """

    def __init__(self, root):
        self.root = root

        self.queue = Queue(maxsize=5000)   # prevents memory explosion
        self._running = False

    # =========================================================
    # START / STOP
    # =========================================================
    def start(self):
        if self._running:
            return

        self._running = True
        self._poll()

    def stop(self):
        self._running = False

    # =========================================================
    # DISPATCH (THREAD → UI SAFE QUEUE)
    # =========================================================
    def dispatch(self, fn: Callable):
        try:
            self.queue.put_nowait(fn)
        except Exception:
            logger.warning("UI queue full — dropping task")

    # =========================================================
    # MAIN PUMP LOOP
    # =========================================================
    def _poll(self):
        if not self._running:
            return

        try:
            # batch drain (faster UI rendering)
            for _ in range(50):
                try:
                    fn = self.queue.get_nowait()
                    fn()
                except Empty:
                    break
                except Exception:
                    logger.exception("UI task failed")

        except Exception:
            logger.exception("Scheduler poll crash")

        self.root.after(16, self._poll)
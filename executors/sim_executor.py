# executors/sim_executor.py

from __future__ import annotations

import random
import threading
import time


class SimulatedExecutor:
    """
    =====================================================
    🚀 BLACKBOT SIMULATED EXECUTOR
    =====================================================

    Fake institutional execution engine.

    Simulates:
    ✔ order fills
    ✔ pnl movement
    ✔ execution latency
    ✔ slippage
    ✔ trade lifecycle
    ✔ close events

    SAFE FOR TESTING UI + PIPELINE.
    =====================================================
    """

    def __init__(self, trade_desk):
        self.trade_desk = trade_desk

        self.running = True

    # =====================================================
    # SUBMIT TRADE
    # =====================================================
    def submit(self, decision):

        trade_id = self.trade_desk.register_trade(decision)

        t = threading.Thread(
            target=self._simulate_trade,
            args=(trade_id,),
            daemon=True
        )

        t.start()

        return trade_id

    # =====================================================
    # TRADE SIMULATION
    # =====================================================
    def _simulate_trade(self, trade_id):

        pnl = 0.0

        for _ in range(20):

            if not self.running:
                return

            # simulate movement
            pnl += random.uniform(-0.8, 1.2)

            self.trade_desk.update_trade(
                trade_id=trade_id,
                pnl=round(pnl, 2)
            )

            time.sleep(1)

        # final close
        self.trade_desk.close_trade(
            trade_id=trade_id,
            pnl=round(pnl, 2),
            exit_price=0.0
        )
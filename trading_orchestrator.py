# trading_orchestrator.py

from __future__ import annotations

import time
import asyncio
import logging
from typing import Dict, Any, Optional

from signal_engine import SignalEngine
from signal_pipeline import SignalPipeline, StrategySignal, AggressionContext

logger = logging.getLogger("trading_orchestrator")
logger.setLevel(logging.INFO)


# ============================================================
# MARKET FEED INTERFACE (YOU WILL REPLACE THIS)
# ============================================================

class MarketFeed:
    """
    Replace this with:
    - MT5 data fetcher
    - Binance websocket
    - or your orchestrator.market_analyzer
    """

    def get_market_state(self, symbol: str) -> Dict[str, Any]:
        # MOCK DATA (replace later)
        return {
            "account_balance": 100,
            "spread_multiplier": 1.1,
            "atr_ratio": 1.2,
            "efficiency_ratio": 0.55,
            "session_pnl_r": 0.0,
        }

    def get_signal_input(self, symbol: str) -> Dict[str, Any]:
        """
        This simulates raw indicator inputs for SignalEngine.
        Replace with your real market analyzer output.
        """
        return {
            "timeframes": {
                "M5": {
                    "bos_up_flag": True,
                    "displacement_flag": True,
                    "fvg_active_flag": True,
                },
                "M15": {
                    "liquidity_sweep_low_flag": True,
                }
            },
            "regime": {
                "final_regime": "TRENDING"
            },
            "session": {
                "mode": "LONDON"
            },
            "rank": {
                "score": 85
            }
        }


# ============================================================
# EXECUTOR (PAPER MODE FIRST)
# ============================================================

class PaperExecutor:
    """
    DO NOT connect to MT5 yet.
    This just prints decisions safely.
    """

    def execute(self, decision):
        if not decision.allow_trade:
            logger.info(f"❌ REJECTED | {decision.symbol} | {decision.reason}")
            return

        logger.info(
            f"✅ EXECUTE {decision.symbol} "
            f"| {decision.bias} "
            f"| conf={decision.final_confidence} "
            f"| risk={decision.risk_multiplier} "
            f"| mode={decision.aggression_mode} "
            f"| stack={decision.allow_stack}"
        )


# ============================================================
# TRADING ORCHESTRATOR (MAIN LOOP)
# ============================================================

class TradingOrchestrator:

    def __init__(
        self,
        symbols: list[str],
        loop_delay: float = 1.0,
    ):
        self.symbols = symbols
        self.loop_delay = loop_delay

        self.market = MarketFeed()
        print("🧠 Loading SignalEngine...")
        self.engine = SignalEngine()
        print("🧠 SignalEngine loaded successfully")
        self.pipeline = SignalPipeline()
        self.executor = PaperExecutor()

        self.running = False

    # --------------------------------------------------------
    # AGGRESSION CONTEXT (STATIC FOR NOW)
    # --------------------------------------------------------

    def get_session_context(self) -> AggressionContext:
        return AggressionContext(
            mode="NORMAL",
            confidence=0.8,
            reason="default session"
        )

    # --------------------------------------------------------
    # CORE CYCLE
    # --------------------------------------------------------

    def process_symbol(self, symbol: str):
        print(f"🔄 Processing {symbol}")

        # 1. MARKET INPUT
        raw_input = self.market.get_signal_input(symbol)
        market_state = self.market.get_market_state(symbol)

        # 2. SIGNAL ENGINE (STAGE 1)
        trade_signal = self.engine.analyze_symbol(symbol, raw_input)
        print("📊 Raw signal:", trade_signal)

        if not trade_signal:
            return

        # 3. ADAPT TO PIPELINE FORMAT
        strategy_signal = StrategySignal(
            symbol=trade_signal.symbol,
            bias=trade_signal.direction,
            confidence=trade_signal.confidence / 100.0,  # IMPORTANT FIX
            regime="TRENDING"
        )

        # 4. AGGRESSION CONTEXT
        session_ctx = self.get_session_context()

        # 5. PIPELINE DECISION (STAGE 2)
        decision = self.pipeline.evaluate(
            signal=strategy_signal,
            session_ctx=session_ctx,
            market_state=market_state
        )
        print("🧠 PIPELINE INPUT:", strategy_signal)
        decision = self.pipeline.evaluate(...)
        print("📤 PIPELINE OUTPUT:", decision)

        # 6. EXECUTION (PAPER FIRST)
        self.executor.execute(decision)

    # --------------------------------------------------------
    # MAIN LOOP
    # --------------------------------------------------------

    async def run(self):

        self.running = True
        logger.info("🚀 Trading Orchestrator Started")

        while self.running:

            for symbol in self.symbols:
                try:
                    self.process_symbol(symbol)

                except Exception as e:
                    logger.exception(f"Error processing {symbol}: {e}")

            await asyncio.sleep(self.loop_delay)

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    def stop(self):
        self.running = False
        logger.info("🛑 Trading Orchestrator Stopped")


# ============================================================
# RUN ENTRYPOINT
# ============================================================

if __name__ == "__main__":

    bot = TradingOrchestrator(
        symbols=["EURUSD", "GBPUSD", "XAUUSD"],
        loop_delay=1.0
    )

    asyncio.run(bot.run())
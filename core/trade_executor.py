from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

logger = logging.getLogger("core.trade_executor")


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class TradeIntent:
    symbol: str
    side: str  # BUY / SELL normalized
    volume: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


@dataclass
class ExecutionDecision:
    approved: bool
    reason: str = ""


# ============================================================
# NORMALIZER
# ============================================================

def normalize_side(side: str) -> str:
    s = side.strip().upper()

    if s in {"BUY", "LONG"}:
        return "BUY"
    if s in {"SELL", "SHORT"}:
        return "SELL"

    raise ValueError(f"Invalid trade side: {side}")


# ============================================================
# EXECUTOR
# ============================================================

class TradeExecutor:
    """
    Bridge layer between AI system and C++ / MT5 execution engine.
    """

    def __init__(self) -> None:
        logger.info("TradeExecutor initialized (C++ bridge ready)")

    # --------------------------------------------------------
    # VALIDATION LAYER
    # --------------------------------------------------------

    def validate(self, intent: TradeIntent) -> ExecutionDecision:

        if intent.volume <= 0:
            return ExecutionDecision(False, "invalid volume")

        if intent.volume > 100:
            return ExecutionDecision(False, "volume too large")

        if not intent.symbol or len(intent.symbol) < 3:
            return ExecutionDecision(False, "invalid symbol")

        try:
            normalize_side(intent.side)
        except ValueError as e:
            return ExecutionDecision(False, str(e))

        return ExecutionDecision(True, "approved")

    # --------------------------------------------------------
    # EXECUTION LAYER
    # --------------------------------------------------------

    async def execute(self, intent: TradeIntent) -> Dict[str, Any]:

        logger.info(
            f"[EXECUTOR] {intent.symbol} {intent.side} {intent.volume}"
        )

        # Normalize BEFORE execution
        try:
            intent.side = normalize_side(intent.side)
        except ValueError as e:
            return {
                "success": False,
                "error": str(e),
            }

        decision = self.validate(intent)

        if not decision.approved:
            return {
                "success": False,
                "error": decision.reason,
            }

        try:
            # =================================================
            # FUTURE C++ CALL GOES HERE
            # =================================================
            # result = cpp_engine.execute(intent)

            result = {
                "status": "simulated_success",
                "symbol": intent.symbol,
                "side": intent.side,
                "volume": intent.volume,
            }

            return {
                "success": True,
                "result": result,
            }

        except Exception as e:

            logger.exception("Execution failed")

            return {
                "success": False,
                "error": str(e),
            }
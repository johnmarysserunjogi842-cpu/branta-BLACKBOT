import logging
import time

logger = logging.getLogger("TRADING_SAFETY")


class TradingSafetyEngine:
    """
    Pre-trade and runtime safety enforcement layer.
    """

    def __init__(self):
        self.max_trades_per_min = 10
        self.max_risk_score = 0.02
        self.max_drawdown = 0.10

        self.trade_timestamps = []
        self.block_trading = False

    # ========================================================
    # KILL SWITCH
    # ========================================================

    def emergency_stop(self, reason: str):
        self.block_trading = True
        logger.critical(f"[KILL SWITCH] Activated: {reason}")

    def resume(self):
        self.block_trading = False
        logger.info("[KILL SWITCH] Trading resumed")

    # ========================================================
    # TRADE VALIDATION
    # ========================================================

    def validate_trade(self, symbol: str, lot: float, spread: float) -> bool:
        if self.block_trading:
            return False

        if lot <= 0 or lot > 1.0:
            logger.warning(f"{symbol} rejected: invalid lot {lot}")
            return False

        if spread > 0.0005:
            logger.warning(f"{symbol} rejected: spread too high {spread}")
            return False

        now = time.time()

        # trade rate limiter
        self.trade_timestamps = [
            t for t in self.trade_timestamps if now - t < 60
        ]

        if len(self.trade_timestamps) >= self.max_trades_per_min:
            logger.warning("Trade rate limit exceeded")
            return False

        self.trade_timestamps.append(now)

        return True
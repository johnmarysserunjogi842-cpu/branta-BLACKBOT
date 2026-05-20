class ExecutionValidator:

    def validate(self, symbol, price, spread, volatility):

        if spread > 0.0005:
            return False

        if volatility > 0.02:
            return False

        if price <= 0:
            return False

        return True
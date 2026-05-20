# market_analyzer/feature_extractor.py
import numpy as np


class FeatureExtractor:
    """
    Institution-grade, single-timeframe market feature extractor.
    Deterministic, cache-safe, numerically stable.

    Goals:
    - ultra-fast extraction (low allocations, cached primitives)
    - stable numerics (EPS guards)
    - output schema unchanged (backward compatible)
    - add institutional structure features: FVG/imbalance/void/displacement/BOS/CHOCH/sweeps
    """

    MIN_CANDLES = 50
    EPS = 1e-9

    # =========================================================
    # INIT
    # =========================================================
    def __init__(self, candle_data: dict):
        try:
            self.symbol = candle_data["symbol"]
            self.timeframe = candle_data["timeframe"]
            candles = candle_data["candles"]
        except KeyError as e:
            raise ValueError(f"Missing candle field: {e}")

        # Fast path: extract OHLCV arrays
        self.opens = np.asarray([c["open"] for c in candles], dtype=np.float64)
        self.highs = np.asarray([c["high"] for c in candles], dtype=np.float64)
        self.lows = np.asarray([c["low"] for c in candles], dtype=np.float64)
        self.closes = np.asarray([c["close"] for c in candles], dtype=np.float64)
        self.volumes = np.asarray([c["volume"] for c in candles], dtype=np.float64)

        self.length = int(self.closes.size)
        if self.length < self.MIN_CANDLES:
            raise ValueError(f"Insufficient candles ({self.length})")

        # Strict validation (fast)
        if (
            not np.all(np.isfinite(self.opens))
            or not np.all(np.isfinite(self.highs))
            or not np.all(np.isfinite(self.lows))
            or not np.all(np.isfinite(self.closes))
            or not np.all(np.isfinite(self.volumes))
        ):
            raise ValueError("Non-finite OHLCV data detected")

        # Cache for computed arrays/scalars
        self._cache = {}

    # =========================================================
    # INTERNAL CACHED PRIMITIVES (FAST)
    # =========================================================
    def _returns(self):
        """log returns: diff(log(close))"""
        r = self._cache.get("returns")
        if r is None:
            r = np.diff(np.log(self.closes + self.EPS))
            self._cache["returns"] = r
        return r

    def _true_range(self):
        """True range array (vectorized)."""
        tr = self._cache.get("tr")
        if tr is None:
            prev_close = np.empty_like(self.closes)
            prev_close[0] = self.closes[0]
            prev_close[1:] = self.closes[:-1]

            hl = self.highs - self.lows
            hc = np.abs(self.highs - prev_close)
            lc = np.abs(self.lows - prev_close)

            tr = np.maximum(hl, np.maximum(hc, lc))
            tr[0] = hl[0]
            self._cache["tr"] = tr
        return tr

    def _ema(self, period: int):
        """EMA (tight loop)."""
        key = f"ema_{int(period)}"
        ema = self._cache.get(key)
        if ema is None:
            p = int(period)
            alpha = 2.0 / (p + 1.0)
            closes = self.closes

            ema = np.empty_like(closes)
            ema[0] = closes[0]

            a = alpha
            inv = 1.0 - a
            e = ema
            for i in range(1, self.length):
                e[i] = a * closes[i] + inv * e[i - 1]

            self._cache[key] = ema
        return ema

    def _vwap(self):
        """VWAP series."""
        vwap = self._cache.get("vwap")
        if vwap is None:
            typical = (self.highs + self.lows + self.closes) / 3.0
            vol = self.volumes
            cum_vol = np.cumsum(vol)
            cum_vp = np.cumsum(typical * vol)
            vwap = cum_vp / np.maximum(cum_vol, self.EPS)
            self._cache["vwap"] = vwap
        return vwap

    def _candle_body(self):
        key = "body"
        v = self._cache.get(key)
        if v is None:
            v = np.abs(self.closes - self.opens)
            self._cache[key] = v
        return v

    def _candle_range(self):
        key = "range"
        v = self._cache.get(key)
        if v is None:
            v = self.highs - self.lows
            self._cache[key] = v
        return v

    # =========================================================
    # TREND
    # =========================================================
    def trend_slope(self):
        v = self._cache.get("trend_slope")
        if v is None:
            x = np.arange(self.length, dtype=np.float64)
            y = np.log(self.closes + self.EPS)
            slope, _ = np.polyfit(x, y, 1)
            v = float(slope)
            self._cache["trend_slope"] = v
        return v

    def trend_strength(self):
        v = self._cache.get("trend_strength")
        if v is None:
            x = np.arange(self.length, dtype=np.float64)
            y = np.log(self.closes + self.EPS)
            slope, intercept = np.polyfit(x, y, 1)
            fitted = slope * x + intercept
            residuals = y - fitted
            var_y = float(np.var(y))
            var_r = float(np.var(residuals))
            r2 = 1.0 - (var_r / (var_y + self.EPS))
            v = float(np.clip(r2, 0.0, 1.0))
            self._cache["trend_strength"] = v
        return v

    # =========================================================
    # STRUCTURE
    # =========================================================
    def _swing_highs_lows(self, window: int = 5):
        w = int(window)
        key = f"swings_{w}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        n = self.length
        if n < (2 * w + 1):
            arr = np.asarray([], dtype=np.int32)
            self._cache[key] = arr
            return arr

        highs = self.highs
        lows = self.lows

        idx = []
        for i in range(w, n - w):
            hi = highs[i]
            lo = lows[i]
            hwin = highs[i - w : i + w + 1]
            lwin = lows[i - w : i + w + 1]

            if hi == np.max(hwin):
                idx.append(i)
            elif lo == np.min(lwin):
                idx.append(i)

        arr = np.asarray(idx, dtype=np.int32)
        self._cache[key] = arr
        return arr

    def structure_clarity(self, period: int = 20):
        swings = self._swing_highs_lows(period)
        if swings.size < 3:
            return 0.0
        diffs = np.diff(swings)
        return float(1.0 / (1.0 + float(np.std(diffs))))

    # =========================================================
    # MOMENTUM
    # =========================================================
    def momentum(self, period=14):
        r = self._returns()
        p = int(period)
        if r.size < p:
            return 0.0
        return float(np.mean(r[-p:]))

    def momentum_slope(self, period=14):
        r = self._returns()
        p = int(period)
        if r.size < p:
            return 0.0
        x = np.arange(p, dtype=np.float64)
        slope, _ = np.polyfit(x, r[-p:], 1)
        return float(slope)

    def momentum_acceleration(self, period=14):
        r = self._returns()
        p = int(period)
        if r.size < 2:
            return 0.0
        tail = r[-min(p, r.size):]
        if tail.size < 2:
            return 0.0
        return float(np.mean(np.diff(tail)))

    def momentum_spike_flag(self, threshold=0.005, period=14):
        r = self._returns()
        p = int(period)
        if r.size < 1:
            return False
        tail = r[-min(p, r.size):]
        thr = float(threshold)
        return bool(np.any(np.abs(tail) > thr))

    def momentum_exhaustion_flag(self, period=14):
        return self.momentum_acceleration(period) < 0.0

    # =========================================================
    # VOLATILITY
    # =========================================================
    def atr(self, period=14):
        tr = self._true_range()
        p = int(period)
        if tr.size < p:
            return float(np.mean(tr))
        return float(np.mean(tr[-p:]))

    def relative_volatility(self, short=14, long=42):
        base = self.atr(long)
        if base <= 0:
            return 0.0
        return float(self.atr(short) / base)

    def volatility_compression(self, short=14, long=42):
        rv = self.relative_volatility(short, long)
        return float(1.0 / (1.0 + rv))

    def volatility_expansion(self, short=14, long=42):
        return float(self.relative_volatility(short, long))

    def candle_range_expansion(self, period=14):
        ranges = self._candle_range()
        p = int(period)
        if ranges.size < 2:
            return 0.0
        mean_range = float(np.mean(ranges[-min(p, ranges.size):]))
        return float(ranges[-1] / (mean_range + self.EPS))

    # =========================================================
    # VOLUME
    # =========================================================
    def volume_average(self, period=20):
        v = self.volumes
        p = int(period)
        if v.size < p:
            return float(np.mean(v))
        return float(np.mean(v[-p:]))

    def volume_spike_ratio(self, period=20):
        avg = self.volume_average(period)
        return float(self.volumes[-1] / (avg + self.EPS))

    def volume_trend(self, period=20):
        v = self.volumes
        p = int(period)
        if v.size < p:
            return 0.0
        x = np.arange(p, dtype=np.float64)
        slope, _ = np.polyfit(x, v[-p:], 1)
        return float(slope)

    def volume_pressure(self, period=20):
        closes = self.closes
        vol = self.volumes
        p = int(period)

        if closes.size < 2:
            return 0.0

        delta = np.diff(closes)
        vol1 = vol[1:]

        if delta.size < 1:
            return 0.0

        tail = slice(max(0, delta.size - p), delta.size)
        d = delta[tail]
        vv = vol1[tail]

        buy = float(np.sum(vv[d > 0.0]))
        sell = float(np.sum(vv[d < 0.0]))
        total = buy + sell
        if total <= 0:
            return 0.0
        return float((buy - sell) / total)

    # =========================================================
    # LIQUIDITY
    # =========================================================
    def wick_dominance(self, period=14):
        p = int(period)
        o = self.opens[-p:]
        c = self.closes[-p:]
        h = self.highs[-p:]
        l = self.lows[-p:]

        body = np.abs(c - o)
        upper = h - np.maximum(o, c)
        lower = np.minimum(o, c) - l

        return float(np.mean((upper + lower) / (body + self.EPS)))

    def equal_highs_lows(self, period=14):
        p = int(period)
        h = self.highs[-p:]
        l = self.lows[-p:]
        return bool(
            np.any(np.isclose(h, h[-1], rtol=1e-5))
            or np.any(np.isclose(l, l[-1], rtol=1e-5))
        )

    # =========================================================
    # INSTITUTIONAL FEATURES (NEW)
    # =========================================================
    def displacement(self, period: int = 20, z_thr: float = 2.0):
        """
        Displacement candle detection:
        - compares last candle body to rolling body mean/std
        - direction from close-open
        Returns: (flag, strength, direction)
        direction: +1 bull, -1 bear, 0 neutral
        """
        p = int(period)
        bodies = self._candle_body()
        if bodies.size < 5:
            return False, 0.0, 0

        tail = bodies[-min(p, bodies.size):]
        mu = float(np.mean(tail))
        sd = float(np.std(tail))
        last = float(bodies[-1])

        z = (last - mu) / (sd + self.EPS)
        direction = 1 if (self.closes[-1] > self.opens[-1]) else (-1 if (self.closes[-1] < self.opens[-1]) else 0)

        flag = bool(z > float(z_thr))
        strength = float(max(0.0, z))
        return flag, strength, direction

    def fvg_imbalance(self, lookback: int = 60):
        """
        Fair Value Gap (FVG) / imbalance detection (ICT style):
        Bullish FVG if low[i] > high[i-2]
        Bearish FVG if high[i] < low[i-2]
        We count active gaps in last lookback and compute distance of nearest gap to current price in ATR units.
        """
        lb = int(lookback)
        n = self.length
        if n < 5:
            return 0, 0, 0.0, False

        start = max(2, n - lb)
        highs = self.highs
        lows = self.lows
        close = float(self.closes[-1])

        bull_count = 0
        bear_count = 0
        nearest_dist = np.inf
        active = False

        # tight loop (faster than vectorizing with many temporaries)
        for i in range(start, n):
            # bullish gap: low[i] > high[i-2]
            if lows[i] > highs[i - 2]:
                bull_count += 1
                gap_low = highs[i - 2]
                gap_high = lows[i]
                # active if price inside gap zone
                if gap_low <= close <= gap_high:
                    active = True
                # distance to zone
                if close < gap_low:
                    dist = gap_low - close
                elif close > gap_high:
                    dist = close - gap_high
                else:
                    dist = 0.0
                if dist < nearest_dist:
                    nearest_dist = dist

            # bearish gap: high[i] < low[i-2]
            if highs[i] < lows[i - 2]:
                bear_count += 1
                gap_high = lows[i - 2]
                gap_low = highs[i]
                if gap_low <= close <= gap_high:
                    active = True
                if close < gap_low:
                    dist = gap_low - close
                elif close > gap_high:
                    dist = close - gap_high
                else:
                    dist = 0.0
                if dist < nearest_dist:
                    nearest_dist = dist

        atr = self.atr()
        if not np.isfinite(nearest_dist):
            nearest_atr = 999.0
        else:
            nearest_atr = float(nearest_dist / (atr + self.EPS))

        return int(bull_count), int(bear_count), nearest_atr, bool(active)

    def inefficiency_score(self, lookback: int = 60):
        """
        Inefficiency score combines:
        - low efficiency_ratio (chop) + volatility expansion + wick dominance
        - plus FVG density (imbalance frequency)
        Returns: (score 0..1, flag)
        """
        lb = int(lookback)
        er = float(self.efficiency_ratio(min(20, self.length - 1)))
        rv = float(self.relative_volatility())
        wick = float(self.wick_dominance(period=min(14, self.length)))

        bull, bear, nearest_atr, active = self.fvg_imbalance(lookback=lb)
        fvg_density = float((bull + bear) / max(1, min(lb, self.length)))

        # normalize components (bounded)
        er_bad = float(np.clip(1.0 - er, 0.0, 1.0))
        rv_n = float(np.clip((rv - 1.0) / 1.0, 0.0, 1.0))  # rv >1 => expansion
        wick_n = float(np.clip(wick / 3.0, 0.0, 1.0))
        fvg_n = float(np.clip(fvg_density * 10.0, 0.0, 1.0))

        score = 0.40 * er_bad + 0.25 * rv_n + 0.20 * wick_n + 0.15 * fvg_n
        score = float(np.clip(score, 0.0, 1.0))

        # flag if inefficiency high OR active FVG nearby
        flag = bool(score > 0.65 or (active and nearest_atr < 1.0))
        return score, flag

    def liquidity_sweep(self, period: int = 20, atr_mult: float = 0.25):
        """
        Detect liquidity sweep (stop run):
        - Sweep high: last high breaks prior max(high) but closes back below prior max
        - Sweep low: last low breaks prior min(low) but closes back above prior min
        Strength: distance beyond level in ATR units
        """
        p = int(period)
        if self.length < p + 2:
            return False, False, 0.0

        highs = self.highs
        lows = self.lows
        closes = self.closes

        prev_high = float(np.max(highs[-(p + 1):-1]))
        prev_low = float(np.min(lows[-(p + 1):-1]))

        last_high = float(highs[-1])
        last_low = float(lows[-1])
        last_close = float(closes[-1])

        atr = self.atr()
        thr = float(atr_mult) * (atr + self.EPS)

        sweep_high = bool(last_high > prev_high + thr and last_close < prev_high)
        sweep_low = bool(last_low < prev_low - thr and last_close > prev_low)

        strength = 0.0
        if sweep_high:
            strength = float((last_high - prev_high) / (atr + self.EPS))
        elif sweep_low:
            strength = float((prev_low - last_low) / (atr + self.EPS))

        return sweep_high, sweep_low, float(np.clip(strength, 0.0, 10.0))

    def bos_choch(self, swing_window: int = 5):
        """
        BOS/CHOCH detection using swing points:
        - BOS up: close breaks above last swing high
        - BOS down: close breaks below last swing low
        - CHoCH: BOS opposite direction after previous BOS
        """
        w = int(swing_window)
        swings = self._swing_highs_lows(w)
        if swings.size < 5:
            return False, False, False

        # identify last two swing highs/lows
        highs = self.highs
        lows = self.lows
        close = float(self.closes[-1])

        # collect swing highs and lows indices
        sh = []
        sl = []
        for i in swings:
            # classify swing by local dominance
            if highs[i] >= np.max(highs[max(0, i - w):min(self.length, i + w + 1)]):
                sh.append(int(i))
            if lows[i] <= np.min(lows[max(0, i - w):min(self.length, i + w + 1)]):
                sl.append(int(i))

        if len(sh) < 1 or len(sl) < 1:
            return False, False, False

        last_swing_high = float(highs[sh[-1]])
        last_swing_low = float(lows[sl[-1]])

        bos_up = bool(close > last_swing_high)
        bos_down = bool(close < last_swing_low)

        # crude CHoCH: if both sides broken recently -> reversal signal
        # (still deterministic + fast)
        prev_close = float(self.closes[-2])
        choch = bool((prev_close < last_swing_high and bos_up) or (prev_close > last_swing_low and bos_down))

        return bos_up, bos_down, choch

    def orderflow_proxy(self, period: int = 20):
        """
        Orderflow proxy without tick data:
        - signed volume = volume * sign(close - open)
        - delta proxy = sum(signed vol) / sum(vol)
        - imbalance pressure = weighted by candle body / range
        """
        p = int(period)
        n = self.length
        if n < 3:
            return 0.0, 0.0

        p = min(p, n)
        o = self.opens[-p:]
        c = self.closes[-p:]
        v = self.volumes[-p:]
        rng = self._candle_range()[-p:]
        body = self._candle_body()[-p:]

        sign = np.sign(c - o)
        signed = v * sign
        denom = float(np.sum(v)) + self.EPS
        delta = float(np.sum(signed) / denom)

        # pressure: more weight when body dominates range
        w = body / (rng + self.EPS)
        pressure = float(np.sum(signed * w) / (np.sum(v * w) + self.EPS))

        return float(np.clip(delta, -1.0, 1.0)), float(np.clip(pressure, -1.0, 1.0))

    # =========================================================
    # MEAN / FAIR VALUE
    # =========================================================
    def ema(self, period=20):
        return float(self._ema(period)[-1])

    def vwap(self):
        return float(self._vwap()[-1])

    def distance_from_mean(self, period=20):
        atr = self.atr()
        if atr <= 0:
            return 0.0
        return float((self.closes[-1] - self._ema(period)[-1]) / (atr + self.EPS))

    def vwap_deviation(self):
        atr = self.atr()
        if atr <= 0:
            return 0.0
        return float((self.closes[-1] - self._vwap()[-1]) / (atr + self.EPS))

    # =========================================================
    # EFFICIENCY / REGIME
    # =========================================================
    def efficiency_ratio(self, period=20):
        p = int(period)
        if self.closes.size <= p:
            return 0.0

        closes = self.closes
        net = abs(closes[-1] - closes[-p])
        gross = float(np.sum(np.abs(np.diff(closes[-p:]))))
        return float(net / gross) if gross > 0 else 0.0

    def regime(self):
        strength = self.trend_strength()
        er = self.efficiency_ratio()
        vol = self.relative_volatility()

        score = float(np.clip(strength * er, 0.0, 1.0))

        if strength > 0.6 and er > 0.5:
            return "TRENDING", score
        if er < 0.3 and vol < 1.0:
            return "RANGING", score
        if vol > 1.3:
            return "VOLATILE", score

        return "UNCLEAR", score

    # =========================================================
    # EXPORT (UNCHANGED OUTPUT SCHEMA + NEW KEYS)
    # =========================================================
    def extract(self):
        regime_type, regime_score = self.regime()

        # micro-opt: avoid repeated slicing allocations
        h20 = self.highs[-20:]
        l20 = self.lows[-20:]
        hi = float(np.max(h20))
        lo = float(np.min(l20))

        # institutional features
        disp_flag, disp_strength, disp_dir = self.displacement()
        bull_fvg, bear_fvg, fvg_nearest_atr, fvg_active = self.fvg_imbalance()
        ineff_score, ineff_flag = self.inefficiency_score()
        sweep_hi, sweep_lo, sweep_strength = self.liquidity_sweep()
        bos_up, bos_down, choch = self.bos_choch()
        delta_proxy, imbalance_pressure = self.orderflow_proxy()

        return {
            # existing keys (unchanged)
            "symbol": self.symbol,
            "timeframe": self.timeframe,

            "trend_slope": self.trend_slope(),
            "trend_strength": self.trend_strength(),
            "structure_clarity": self.structure_clarity(),

            "momentum": self.momentum(),
            "momentum_slope": self.momentum_slope(),
            "momentum_acceleration": self.momentum_acceleration(),
            "momentum_spike_flag": self.momentum_spike_flag(),
            "momentum_exhaustion_flag": self.momentum_exhaustion_flag(),

            "atr": self.atr(),
            "relative_volatility": self.relative_volatility(),
            "volatility_compression": self.volatility_compression(),
            "volatility_expansion": self.volatility_expansion(),
            "candle_range_expansion": self.candle_range_expansion(),

            "volume_average": self.volume_average(),
            "volume_spike_ratio": self.volume_spike_ratio(),
            "volume_trend": self.volume_trend(),
            "volume_pressure": self.volume_pressure(),

            "range_high": hi,
            "range_low": lo,
            "range_width": hi - lo,
            "swing_indices": self._swing_highs_lows(),

            "wick_dominance": self.wick_dominance(),
            "equal_highs_lows": self.equal_highs_lows(),

            "ema_20": self.ema(20),
            "vwap": self.vwap(),
            "distance_from_ema": self.distance_from_mean(),
            "vwap_deviation": self.vwap_deviation(),

            "efficiency_ratio": self.efficiency_ratio(),

            "regime_type": regime_type,
            "regime_score": regime_score,

            # NEW institutional keys (added, backward compatible)
            "displacement_flag": disp_flag,
            "displacement_strength": disp_strength,
            "displacement_direction": disp_dir,

            "fvg_bull_count": bull_fvg,
            "fvg_bear_count": bear_fvg,
            "fvg_nearest_distance_atr": fvg_nearest_atr,
            "fvg_active_flag": fvg_active,

            "inefficiency_score": ineff_score,
            "inefficiency_flag": ineff_flag,

            "liquidity_sweep_high_flag": sweep_hi,
            "liquidity_sweep_low_flag": sweep_lo,
            "sweep_strength": sweep_strength,

            "bos_up_flag": bos_up,
            "bos_down_flag": bos_down,
            "choch_flag": choch,

            "delta_volume_proxy": delta_proxy,
            "imbalance_pressure": imbalance_pressure,
            }

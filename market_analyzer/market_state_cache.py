# market_analyzer/market_state_cache.py

from __future__ import annotations

import time
import threading

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List


# ============================================================
# TIMEFRAME STATE
# ============================================================

@dataclass
class TFState:
    """
    Stores ONE timeframe state for ONE symbol.
    """

    bar_ts: int = 0
    updated_ts: float = 0.0
    valid: bool = False
    features: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# SYMBOL STATE
# ============================================================

@dataclass
class SymbolState:
    """
    Stores FULL symbol intelligence state.
    """

    symbol: str

    # timeframe -> TFState
    timeframes: Dict[str, TFState] = field(default_factory=dict)

    # Phase 2 outputs
    regime: Dict[str, Any] = field(default_factory=dict)
    tf_policy: Dict[str, Any] = field(default_factory=dict)
    session: Dict[str, Any] = field(default_factory=dict)

    # Phase 3 output
    rank: Dict[str, Any] = field(default_factory=dict)

    # metadata
    last_update_ts: float = 0.0
    last_bar_ts: int = 0


# ============================================================
# MARKET STATE CACHE
# ============================================================

class MarketStateCache:
    """
    Institutional-grade market cache.

    Responsibilities:
    - feature storage
    - regime storage
    - session storage
    - ranking storage
    - snapshot generation
    - stale protection
    - thread safety
    """

    # ========================================================
    # INIT
    # ========================================================

    def __init__(
        self,
        allowed_symbols: Optional[List[str]] = None,
        allowed_timeframes: Optional[List[str]] = None,
        ttl_seconds: int = 600,
    ):

        self.allowed_symbols = (
            set(allowed_symbols)
            if allowed_symbols
            else None
        )

        self.allowed_timeframes = (
            set(allowed_timeframes)
            if allowed_timeframes
            else None
        )

        self.ttl_seconds = int(ttl_seconds)

        # symbol -> SymbolState
        self._symbols: Dict[str, SymbolState] = {}

        # locks
        self._global_lock = threading.Lock()
        self._symbol_locks: Dict[str, threading.Lock] = {}

        # audit trail
        self._audit: List[Dict[str, Any]] = []
        self._audit_max = 2000

        # versioning
        self._version = 0

    # ========================================================
    # INTERNAL HELPERS
    # ========================================================

    def _now(self) -> float:
        return time.time()

    def _bump_version(self):
        self._version += 1

    def _audit_log(self, event: Dict[str, Any]):

        try:
            self._audit.append(event)

            if len(self._audit) > self._audit_max:
                self._audit = self._audit[-self._audit_max:]

        except Exception:
            pass

    def _get_symbol_lock(self, symbol: str):

        with self._global_lock:

            if symbol not in self._symbol_locks:
                self._symbol_locks[symbol] = threading.Lock()

            return self._symbol_locks[symbol]

    def _get_or_create_symbol_state(
        self,
        symbol: str
    ) -> SymbolState:

        if (
            self.allowed_symbols is not None and
            symbol not in self.allowed_symbols
        ):
            raise ValueError(f"Symbol not allowed: {symbol}")

        if symbol not in self._symbols:
            self._symbols[symbol] = SymbolState(symbol=symbol)

        return self._symbols[symbol]

    def _get_or_create_tf_state(
        self,
        sym_state: SymbolState,
        timeframe: str
    ) -> TFState:

        if (
            self.allowed_timeframes is not None and
            timeframe not in self.allowed_timeframes
        ):
            raise ValueError(
                f"Timeframe not allowed: {timeframe}"
            )

        if timeframe not in sym_state.timeframes:
            sym_state.timeframes[timeframe] = TFState()

        return sym_state.timeframes[timeframe]

    # ========================================================
    # FEATURE NORMALIZATION
    # ========================================================

    def _normalize_features(
        self,
        features: Dict[str, Any]
    ) -> Dict[str, Any]:

        if not isinstance(features, dict):
            return {}

        f = dict(features)

        # default schema
        defaults = {

            "regime": "UNCLEAR",

            "displacement_flag": False,
            "displacement_strength": 0.0,
            "displacement_direction": 0,

            "fvg_bull_count": 0,
            "fvg_bear_count": 0,
            "fvg_nearest_distance_atr": 999.0,
            "fvg_active_flag": False,

            "inefficiency_score": 0.0,
            "inefficiency_flag": False,

            "liquidity_sweep_high_flag": False,
            "liquidity_sweep_low_flag": False,
            "sweep_strength": 0.0,

            "bos_up_flag": False,
            "bos_down_flag": False,
            "choch_flag": False,

            "delta_volume_proxy": 0.0,
            "imbalance_pressure": 0.0,
        }

        for k, v in defaults.items():

            if k not in f or f[k] is None:
                f[k] = v

        return f

    # ========================================================
    # MAIN UPDATE API
    # ========================================================

    def update(
        self,
        symbol: str,
        timeframe: str,
        bar_ts: int,
        data: Dict[str, Any],
        valid: bool = True,
    ) -> bool:

        if not symbol or not timeframe:
            return False

        lock = self._get_symbol_lock(symbol)

        with lock:

            sym_state = self._get_or_create_symbol_state(
                symbol
            )

            tf_state = self._get_or_create_tf_state(
                sym_state,
                timeframe
            )

            # stale protection
            if (
                tf_state.bar_ts > 0 and
                int(bar_ts) <= tf_state.bar_ts
            ):

                self._audit_log({
                    "type": "STALE_REJECT",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bar_ts": bar_ts,
                    "previous_bar_ts": tf_state.bar_ts,
                    "ts": self._now(),
                })

                return False

            normalized = self._normalize_features(data)

            tf_state.bar_ts = int(bar_ts)
            tf_state.updated_ts = self._now()
            tf_state.valid = bool(valid)
            tf_state.features = normalized

            sym_state.last_update_ts = tf_state.updated_ts
            sym_state.last_bar_ts = max(
                sym_state.last_bar_ts,
                tf_state.bar_ts
            )

            self._bump_version()

            self._audit_log({
                "type": "UPDATE_TF",
                "symbol": symbol,
                "timeframe": timeframe,
                "bar_ts": bar_ts,
                "valid": valid,
                "version": self._version,
                "ts": self._now(),
            })

            return True

    # alias compatibility
    def update_tf(
        self,
        symbol: str,
        timeframe: str,
        bar_ts: int,
        features: Dict[str, Any],
        valid: bool = True,
    ) -> bool:

        return self.update(
            symbol=symbol,
            timeframe=timeframe,
            bar_ts=bar_ts,
            data=features,
            valid=valid,
        )

    # ========================================================
    # PHASE 2 STATE
    # ========================================================

    def update_regime(
        self,
        symbol: str,
        regime_payload: Dict[str, Any]
    ):

        lock = self._get_symbol_lock(symbol)

        with lock:

            st = self._get_or_create_symbol_state(symbol)

            st.regime = dict(regime_payload or {})
            st.last_update_ts = self._now()

            self._bump_version()

    def update_tf_policy(
        self,
        symbol: str,
        tf_policy: Dict[str, Any]
    ):

        lock = self._get_symbol_lock(symbol)

        with lock:

            st = self._get_or_create_symbol_state(symbol)

            st.tf_policy = dict(tf_policy or {})
            st.last_update_ts = self._now()

            self._bump_version()

    def update_session(
        self,
        symbol: str,
        session_payload: Dict[str, Any]
    ):

        lock = self._get_symbol_lock(symbol)

        with lock:

            st = self._get_or_create_symbol_state(symbol)

            st.session = dict(session_payload or {})
            st.last_update_ts = self._now()

            self._bump_version()

    def update_rank(
        self,
        symbol: str,
        rank_payload: Dict[str, Any]
    ):

        lock = self._get_symbol_lock(symbol)

        with lock:

            st = self._get_or_create_symbol_state(symbol)

            st.rank = dict(rank_payload or {})
            st.last_update_ts = self._now()

            self._bump_version()

    # ========================================================
    # ORCHESTRATOR COMPATIBILITY
    # ========================================================

    def update_symbol_state(
        self,
        symbol: str,
        payload: Dict[str, Any]
    ):

        if not isinstance(payload, dict):
            return

        if "regime" in payload:
            self.update_regime(
                symbol,
                payload["regime"]
            )

        if "timeframe_policy" in payload:
            self.update_tf_policy(
                symbol,
                payload["timeframe_policy"]
            )

        if "tf_policy" in payload:
            self.update_tf_policy(
                symbol,
                payload["tf_policy"]
            )

        if "session" in payload:
            self.update_session(
                symbol,
                payload["session"]
            )

        if "rank" in payload:
            self.update_rank(
                symbol,
                payload["rank"]
            )

    def update_global_state(
        self,
        payload: Dict[str, Any]
    ):

        if not hasattr(self, "_global_state"):
            self._global_state = {}

        self._global_state.update(payload or {})

    # ========================================================
    # READ API
    # ========================================================

    def get_tf_features(
        self,
        symbol: str,
        timeframe: str
    ) -> Dict[str, Any]:

        st = self._symbols.get(symbol)

        if st is None:
            return {}

        tf = st.timeframes.get(timeframe)

        if tf is None:
            return {}

        if not tf.valid:
            return {}

        return tf.features or {}

    def get_features_by_tf(
        self,
        symbol: str
    ) -> Dict[str, Dict[str, Any]]:

        st = self._symbols.get(symbol)

        if st is None:
            return {}

        out = {}

        for tf, tf_state in st.timeframes.items():

            if tf_state.valid:
                out[tf] = tf_state.features

        return out

    def get_symbol_tf_features(
        self,
        symbol: str
    ) -> Dict[str, Dict[str, Any]]:

        return self.get_features_by_tf(symbol)

    def get_symbol_state(
        self,
        symbol: str
    ) -> Dict[str, Any]:

        st = self._symbols.get(symbol)

        if st is None:
            return {}

        return {

            "symbol": st.symbol,

            "last_update_ts": st.last_update_ts,
            "last_bar_ts": st.last_bar_ts,

            "regime": st.regime,
            "tf_policy": st.tf_policy,
            "session": st.session,
            "rank": st.rank,

            "timeframes": {

                tf: {
                    "bar_ts": tf_state.bar_ts,
                    "updated_ts": tf_state.updated_ts,
                    "valid": tf_state.valid,
                    "features": tf_state.features,
                }

                for tf, tf_state
                in st.timeframes.items()
            },
        }

    # ========================================================
    # GLOBAL READS
    # ========================================================

    def symbols(self) -> List[str]:
        return list(self._symbols.keys())

    def list_symbols(self) -> List[str]:
        return self.symbols()

    def version(self) -> int:
        return self._version

    def is_stale(self, symbol: str) -> bool:

        st = self._symbols.get(symbol)

        if st is None:
            return True

        if st.last_update_ts <= 0:
            return True

        return (
            self._now() - st.last_update_ts
        ) > self.ttl_seconds

    # ========================================================
    # SNAPSHOT
    # ========================================================

    def snapshot(self) -> Dict[str, Any]:

        global_state = getattr(
            self,
            "_global_state",
            {}
        )

        return {

            "meta": {
                "version": self._version,
                "ts": self._now(),
                "ttl_seconds": self.ttl_seconds,
            },

            "symbols": {
                sym: self.get_symbol_state(sym)
                for sym in self.symbols()
            },

            "global_market_state": global_state,

            "audit_tail": self._audit[-50:],
        }

    # ========================================================
    # ANALYTICS SNAPSHOT
    # ========================================================

    def snapshot_for_analysis(self):

        return self.snapshot()
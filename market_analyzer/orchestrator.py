# market_analyzer/orchestrator.py

from __future__ import annotations

from typing import Dict, Any, Optional, List, Iterable
import concurrent.futures as cf
import threading
import time
import logging
from dataclasses import dataclass

from market_analyzer.feature_extractor import FeatureExtractor


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger("market_orchestrator")
logger.setLevel(logging.INFO)


# ============================================================
# JOB MODEL
# ============================================================

@dataclass(frozen=True)
class _Job:
    symbol: str
    timeframe: str
    candle_data: dict


# ============================================================
# ORCHESTRATOR
# ============================================================

class Orchestrator:
    """
    Institutional Market Orchestrator

    Responsibilities:
    ------------------------------------------------
    1. Receive raw market candles
    2. Extract institutional features
    3. Update MarketStateCache
    4. Run regime/session analysis
    5. Feed Strategy Manager
    6. Maintain stable low-latency flow
    """

    def __init__(
        self,
        market_state_cache,
        *,
        strategy_manager=None,
        max_workers: int = 8,
        global_timeout_seconds: float = 10.0,
        enable_phase2: bool = True,
        enable_phase3: bool = True,
        logger_: Optional[logging.Logger] = None,
        engines: Optional[dict] = None,
    ):

        self.cache = market_state_cache
        self.strategy_manager = strategy_manager

        self.max_workers = max(1, int(max_workers))
        self.global_timeout = max(0.5, float(global_timeout_seconds))

        self.enable_phase2 = bool(enable_phase2)
        self.enable_phase3 = bool(enable_phase3)

        self.logger = logger_ or logger

        engines = engines or {}

        self.regime_engine = engines.get("regime_engine")
        self.tf_engine = engines.get("timeframe_engine")
        self.session_manager = engines.get("session_manager")
        self.news_filter = engines.get("news_filter")
        self.instrument_ranker = engines.get("instrument_ranker")

        self._tls = threading.local()

        logger.info("Market Orchestrator initialized")

    # ============================================================
    # MAIN ENTRY
    # ============================================================

    def run(
        self,
        raw_market_data: Dict[str, Dict[str, dict]]
    ) -> Dict[str, Any]:

        t0 = time.perf_counter()

        if not raw_market_data:
            return self._result(
                status="EMPTY",
                t0=t0,
                phase1=None,
                phase2_status="SKIPPED",
                phase3_status="SKIPPED",
                strategy_status="SKIPPED",
                symbols_processed=[],
            )

        # ========================================================
        # PHASE 1
        # ========================================================

        p1_t0 = time.perf_counter()

        phase1 = self._phase1_extract_features(raw_market_data)

        phase1["latency_seconds"] = round(
            time.perf_counter() - p1_t0,
            6
        )

        # ========================================================
        # PHASE 2
        # ========================================================

        phase2_status = "SKIPPED"

        if self.enable_phase2:
            phase2_status = self._phase2_symbol_postprocess()

        # ========================================================
        # PHASE 3
        # ========================================================

        phase3_status = "SKIPPED"

        if self.enable_phase3:
            phase3_status = self._phase3_global_postprocess()

        # ========================================================
        # STRATEGY MANAGER
        # ========================================================

        strategy_status = self._run_strategy_manager()

        # ========================================================
        # FINAL STATUS
        # ========================================================

        status = "OK"

        if phase1["failed"] > 0:
            status = "DEGRADED"

        return self._result(
            status=status,
            t0=t0,
            phase1=phase1,
            phase2_status=phase2_status,
            phase3_status=phase3_status,
            strategy_status=strategy_status,
            symbols_processed=sorted(
                phase1["symbols_processed"]
            ),
        )

    # ============================================================
    # STRATEGY MANAGER INTEGRATION
    # ============================================================

    def _run_strategy_manager(self) -> str:

        if not self.strategy_manager:
            return "NO_STRATEGY_MANAGER"

        try:
            symbols = self._cache_list_symbols()

            for symbol in symbols:

                try:
                    tf_features = (
                        self._cache_get_symbol_tf_features(symbol)
                    )

                    if not tf_features:
                        continue

                    market_state = (
                        self.cache.get_symbol_state(symbol)
                    )

                    # ============================================
                    # CALL STRATEGY MANAGER
                    # ============================================

                    self.strategy_manager.process_symbol(
                        symbol=symbol,
                        tf_features=tf_features,
                        market_state=market_state,
                    )

                except Exception as e:
                    logger.error(
                        f"Strategy manager failed "
                        f"for {symbol}: {e}"
                    )

            return "OK"

        except Exception as e:
            logger.exception(
                f"Strategy manager execution failed: {e}"
            )
            return "FAILED"

    # ============================================================
    # CACHE LAYER
    # ============================================================

    def _cache_update_tf(
        self,
        symbol: str,
        timeframe: str,
        bar_ts: int,
        features: Dict[str, Any],
        valid: bool,
    ) -> bool:

        if hasattr(self.cache, "update_tf"):
            return bool(
                self.cache.update_tf(
                    symbol=symbol,
                    timeframe=timeframe,
                    bar_ts=bar_ts,
                    features=features,
                    valid=valid,
                )
            )

        return bool(
            self.cache.update(
                symbol=symbol,
                timeframe=timeframe,
                bar_ts=bar_ts,
                data=features,
                valid=valid,
            )
        )

    def _cache_list_symbols(self) -> List[str]:

        if hasattr(self.cache, "list_symbols"):
            return list(self.cache.list_symbols())

        if hasattr(self.cache, "symbols"):
            return list(self.cache.symbols())

        return []

    def _cache_get_symbol_tf_features(
        self,
        symbol: str
    ) -> Dict[str, Dict[str, Any]]:

        if hasattr(self.cache, "get_symbol_tf_features"):
            return dict(
                self.cache.get_symbol_tf_features(symbol)
            )

        if hasattr(self.cache, "get_features_by_tf"):
            return dict(
                self.cache.get_features_by_tf(symbol)
            )

        return {}

    def _cache_update_symbol_state(
        self,
        symbol: str,
        payload: Dict[str, Any]
    ) -> None:

        if hasattr(self.cache, "update_symbol_state"):
            self.cache.update_symbol_state(symbol, payload)
            return

        if not isinstance(payload, dict):
            return

        try:

            if "regime" in payload:
                self.cache.update_regime(
                    symbol,
                    payload["regime"]
                )

            if (
                "timeframe_policy" in payload
                or "tf_policy" in payload
            ):
                self.cache.update_tf_policy(
                    symbol,
                    payload.get("timeframe_policy")
                    or payload.get("tf_policy")
                )

            if "session" in payload:
                self.cache.update_session(
                    symbol,
                    payload["session"]
                )

            if "rank" in payload:
                self.cache.update_rank(
                    symbol,
                    payload["rank"]
                )

        except Exception:
            pass

    def _cache_update_global_state(
        self,
        payload: Dict[str, Any]
    ):

        if hasattr(self.cache, "update_global_state"):
            try:
                self.cache.update_global_state(payload)
            except Exception:
                pass

    # ============================================================
    # PHASE 1
    # ============================================================

    def _build_jobs(
        self,
        raw_market_data: Dict[str, Dict[str, dict]]
    ) -> List[_Job]:

        jobs: List[_Job] = []

        for symbol, tf_map in raw_market_data.items():

            if not isinstance(tf_map, dict):
                continue

            for timeframe, candle_data in tf_map.items():

                if isinstance(candle_data, dict):

                    jobs.append(
                        _Job(
                            symbol=symbol,
                            timeframe=timeframe,
                            candle_data=candle_data,
                        )
                    )

        return jobs

    def _phase1_extract_features(
        self,
        raw_market_data: Dict[str, Dict[str, dict]]
    ) -> Dict[str, Any]:

        jobs = self._build_jobs(raw_market_data)

        submitted = len(jobs)

        completed = 0
        failed = 0
        timed_out = 0

        symbols_processed = set()

        if submitted == 0:
            return {
                "submitted": 0,
                "completed": 0,
                "failed": 0,
                "timed_out": 0,
                "symbols_processed": set(),
            }

        with cf.ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="extractor"
        ) as executor:

            futures = [
                executor.submit(
                    self._process_single_tf,
                    job
                )
                for job in jobs
            ]

            try:

                for future in cf.as_completed(
                    futures,
                    timeout=self.global_timeout
                ):

                    try:
                        symbol = future.result()

                        completed += 1

                        if symbol:
                            symbols_processed.add(symbol)

                    except Exception as e:
                        failed += 1
                        logger.error(
                            f"Feature extraction failed: {e}"
                        )

            except cf.TimeoutError:

                for future in futures:

                    if not future.done():
                        future.cancel()
                        timed_out += 1

                logger.error(
                    f"Global extraction timeout "
                    f"({timed_out} cancelled)"
                )

        return {
            "submitted": submitted,
            "completed": completed,
            "failed": failed,
            "timed_out": timed_out,
            "symbols_processed": symbols_processed,
        }

    def _get_extractor(
        self,
        payload: dict
    ) -> FeatureExtractor:

        extractor = getattr(
            self._tls,
            "extractor",
            None
        )

        if extractor is None:
            extractor = FeatureExtractor(payload)
            self._tls.extractor = extractor
            return extractor

        try:
            extractor.reset(payload)
            return extractor

        except Exception:
            return FeatureExtractor(payload)

    def _process_single_tf(
        self,
        job: _Job
    ) -> Optional[str]:

        payload = dict(job.candle_data)

        payload["symbol"] = job.symbol
        payload["timeframe"] = job.timeframe

        extractor = self._get_extractor(payload)

        features = extractor.extract()

        bar_ts = self._normalize_bar_ts(
            payload,
            features
        )

        valid = True

        if isinstance(features, dict):
            meta = features.get("meta") or {}

            if "valid" in meta:
                valid = bool(meta["valid"])

        self._cache_update_tf(
            symbol=job.symbol,
            timeframe=job.timeframe,
            bar_ts=bar_ts,
            features=features,
            valid=valid,
        )

        return job.symbol

    # ============================================================
    # PHASE 2
    # ============================================================

    def _phase2_symbol_postprocess(self) -> str:

        if not (
            self.regime_engine
            and self.tf_engine
            and self.session_manager
        ):
            return "SKIPPED_MISSING_ENGINES"

        symbols = self._cache_list_symbols()

        if not symbols:
            return "NO_SYMBOLS"

        symbols.sort()

        for symbol in symbols:

            try:
                tf_features = (
                    self._cache_get_symbol_tf_features(symbol)
                )

                if not tf_features:
                    continue

                # ================================================
                # REGIME
                # ================================================

                regime_out = (
                    self.regime_engine.evaluate(
                        tf_features=tf_features
                    )
                )

                self._cache_update_symbol_state(
                    symbol,
                    {"regime": regime_out}
                )

                # ================================================
                # TF POLICY
                # ================================================

                tf_policy = (
                    self.tf_engine.evaluate(
                        tf_features=tf_features
                    )
                )

                self._cache_update_symbol_state(
                    symbol,
                    {"timeframe_policy": tf_policy}
                )

                # ================================================
                # SESSION
                # ================================================

                bias_tf = None

                if isinstance(tf_policy, dict):
                    bias_tf = tf_policy.get(
                        "bias_timeframe"
                    )

                bias_features = (
                    tf_features.get(bias_tf)
                    if bias_tf
                    else None
                )

                if not bias_features:
                    bias_features = tf_features[
                        sorted(tf_features.keys())[0]
                    ]

                session_out = (
                    self.session_manager.evaluate(
                        features=bias_features,
                        tf_policy=tf_policy,
                    )
                )

                self._cache_update_symbol_state(
                    symbol,
                    {"session": session_out}
                )

            except Exception as e:
                logger.error(
                    f"Phase2 failed "
                    f"for {symbol}: {e}"
                )

        return "OK"

    # ============================================================
    # PHASE 3
    # ============================================================

    def _phase3_global_postprocess(self) -> str:

        if not (
            self.news_filter
            and self.instrument_ranker
        ):
            return "SKIPPED_MISSING_ENGINES"

        try:

            snapshot = self.cache.snapshot()

            # ================================================
            # NEWS FILTER
            # ================================================

            news_decision = (
                self.news_filter.evaluate(
                    snapshot=snapshot
                )
            )

            self._cache_update_global_state({
                "news_decision": news_decision
            })

            # ================================================
            # RANKING
            # ================================================

            rankings = (
                self.instrument_ranker.rank(
                    snapshot=snapshot
                )
            )

            self._cache_update_global_state({
                "instrument_rankings": rankings
            })

            return "OK"

        except Exception as e:
            logger.exception(
                f"Phase3 failed: {e}"
            )
            return "FAILED"

    # ============================================================
    # HELPERS
    # ============================================================

    def _normalize_bar_ts(
        self,
        candle_data: dict,
        features: dict
    ) -> int:

        candles = candle_data.get("candles") or []

        ts = None

        if candles and isinstance(candles[-1], dict):

            ts = (
                candles[-1].get("timestamp")
                or candles[-1].get("time")
                or candles[-1].get("t")
            )

        if ts is None and isinstance(features, dict):

            meta = features.get("meta") or {}

            ts = (
                meta.get("bar_ts")
                or meta.get("timestamp")
            )

        if ts is None:
            return int(time.time())

        ts = int(ts)

        if ts > 10_000_000_000:
            ts //= 1000

        return ts

    # ============================================================
    # RESULT
    # ============================================================

    def _result(
        self,
        *,
        status: str,
        t0: float,
        phase1: Optional[Dict[str, Any]],
        phase2_status: str,
        phase3_status: str,
        strategy_status: str,
        symbols_processed: List[str],
    ) -> Dict[str, Any]:

        elapsed = round(
            time.perf_counter() - t0,
            6
        )

        submitted = int(
            (phase1 or {}).get("submitted", 0)
        )

        completed = int(
            (phase1 or {}).get("completed", 0)
        )

        failed = int(
            (phase1 or {}).get("failed", 0)
        )

        timed_out = int(
            (phase1 or {}).get("timed_out", 0)
        )

        return {
            "status": status,
            "latency_seconds": elapsed,
            "phase1": phase1 or {},
            "submitted": submitted,
            "completed": completed,
            "failed": failed,
            "timed_out": timed_out,
            "phase2_status": phase2_status,
            "phase3_status": phase3_status,
            "strategy_status": strategy_status,
            "symbols_processed": symbols_processed,
            "market_state": self.cache.snapshot(),
        }
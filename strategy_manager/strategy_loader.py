# strategy_manager/strategy_loader.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Dict,
    Any,
    List,
    Optional,
    Set,
    Type,
    Iterable,
)

import logging
import importlib
import inspect
import pkgutil


# ============================================================
# BASE STRATEGY CONTRACT
# ============================================================

class BaseStrategy:
    """
    ============================================================
    BLACKBOT STRATEGY CONTRACT
    ============================================================

    Every strategy MUST inherit from this class.

    Institutional-grade strategy metadata system.
    ============================================================
    """

    # ========================================================
    # CORE IDENTITY
    # ========================================================

    name: str = "UnnamedStrategy"

    enabled: bool = True

    # ========================================================
    # PRIORITY / WEIGHTING
    # ========================================================

    priority: int = 100

    weight: float = 1.0

    min_confidence: float = 0.60

    cooldown_seconds: int = 30

    # ========================================================
    # REGIME CONTROL
    # ========================================================

    allowed_regimes: Set[str] = {
        "TRENDING",
        "RANGING",
        "VOLATILITY_EXPANSION",
    }

    allow_unclear_regime: bool = False

    # ========================================================
    # STRATEGY TAGGING
    # ========================================================

    tags: Set[str] = set()

    # ========================================================
    # DATA REQUIREMENTS
    # ========================================================

    required_timeframes: Set[str] = set()

    required_features: Dict[str, Set[str]] = {}

    required_symbol_state: Set[str] = {
        "regime",
        "tf_policy",
        "session",
    }

    # ========================================================
    # EXECUTION CONTRACT
    # ========================================================

    def evaluate(
        self,
        symbol: str,
        cache,
    ) -> Dict[str, Any]:
        """
        Must return institutional vote payload.

        Example:
        {
            "symbol": "EURUSD",
            "direction": "BUY",
            "confidence": 0.82,
            "strategy": self.name,
        }
        """
        raise NotImplementedError


# ============================================================
# VALIDATION RESULT
# ============================================================

@dataclass(frozen=True)
class StrategyValidationResult:

    name: str

    enabled: bool

    missing: List[str] = field(default_factory=list)

    reason: str = ""

    health_score: float = 1.0


# ============================================================
# STRATEGY REGISTRY
# ============================================================

@dataclass
class StrategyRegistry:
    """
    ============================================================
    ULTRA-FAST STRATEGY REGISTRY
    ============================================================
    """

    # all discovered strategies
    all: Dict[str, BaseStrategy] = field(
        default_factory=dict
    )

    # validated + enabled
    enabled: Dict[str, BaseStrategy] = field(
        default_factory=dict
    )

    # regime indexed
    by_regime: Dict[str, List[BaseStrategy]] = field(
        default_factory=dict
    )

    # validation diagnostics
    validation: Dict[
        str,
        StrategyValidationResult
    ] = field(default_factory=dict)

    # runtime performance metrics
    performance: Dict[
        str,
        Dict[str, Any]
    ] = field(default_factory=dict)


# ============================================================
# STRATEGY LOADER
# ============================================================

class StrategyLoader:
    """
    ============================================================
    INSTITUTION-GRADE STRATEGY LOADER
    ============================================================

    Responsibilities:
    ------------------------------------------------------------
    - Discover strategies
    - Validate requirements
    - Build optimized registry
    - Enable/disable strategies safely
    - Support hot-plug strategy systems
    ============================================================
    """

    VALID_REGIMES = {
        "TRENDING",
        "RANGING",
        "VOLATILITY_EXPANSION",
        "UNCLEAR",
    }

    # ========================================================
    # INIT
    # ========================================================

    def __init__(
        self,
        strict: bool = True,
        logger: Optional[logging.Logger] = None,
    ):

        self.strict = bool(strict)

        self.logger = (
            logger
            or logging.getLogger("StrategyLoader")
        )

    # ========================================================
    # PUBLIC LOAD API
    # ========================================================

    def load(
        self,
        strategy_classes: Iterable[
            Type[BaseStrategy]
        ],
        cache,
        sample_symbol: Optional[str] = None,
    ) -> StrategyRegistry:

        registry = StrategyRegistry()

        # ====================================================
        # PICK SAMPLE SYMBOL
        # ====================================================

        if sample_symbol is None:

            syms = (
                cache.symbols()
                if hasattr(cache, "symbols")
                else []
            )

            sample_symbol = (
                syms[0]
                if syms
                else None
            )

        sample_symbol_state = {}

        sample_tf_features = {}

        if sample_symbol:

            try:

                sample_symbol_state = (
                    cache.get_symbol_state(
                        sample_symbol
                    ) or {}
                )

            except Exception:

                sample_symbol_state = {}

            try:

                sample_tf_features = (
                    cache.get_features_by_tf(
                        sample_symbol
                    ) or {}
                )

            except Exception:

                sample_tf_features = {}

        # ====================================================
        # LOAD STRATEGIES
        # ====================================================

        for cls in strategy_classes:

            try:

                strategy = cls()

            except Exception as e:

                self.logger.exception(
                    f"Failed to instantiate "
                    f"{cls}: {e}"
                )

                continue

            # skip disabled strategies
            if not strategy.enabled:

                self.logger.warning(
                    f"Strategy disabled manually "
                    f"| {strategy.name}"
                )

                continue

            registry.all[strategy.name] = strategy

            validation = self._validate_strategy(
                strategy=strategy,
                sample_symbol_state=(
                    sample_symbol_state
                ),
                sample_tf_features=(
                    sample_tf_features
                ),
                cache=cache,
                cache_has_sample=bool(
                    sample_symbol
                ),
            )

            registry.validation[
                strategy.name
            ] = validation

            if validation.enabled:

                registry.enabled[
                    strategy.name
                ] = strategy

                # initialize performance tracking
                registry.performance[
                    strategy.name
                ] = {
                    "wins": 0,
                    "losses": 0,
                    "pnl": 0.0,
                    "signals": 0,
                    "winrate": 0.0,
                    "health_score": 1.0,
                }

        # ====================================================
        # STRICT MODE
        # ====================================================

        if self.strict:

            failed = [

                v for v in
                registry.validation.values()

                if not v.enabled
            ]

            if failed:

                msg = "\n".join([

                    f"- {x.name}: "
                    f"{x.reason} "
                    f"| missing={x.missing}"

                    for x in failed
                ])

                raise RuntimeError(
                    "StrategyLoader strict "
                    f"validation failed:\n{msg}"
                )

        # ====================================================
        # REGIME INDEXING
        # ====================================================

        registry.by_regime = {

            regime: []

            for regime in self.VALID_REGIMES
        }

        for strategy in registry.enabled.values():

            allowed = (
                strategy.allowed_regimes
                or set()
            )

            if (
                strategy.allow_unclear_regime
                and "UNCLEAR" not in allowed
            ):
                allowed.add("UNCLEAR")

            for regime in allowed:

                if regime not in registry.by_regime:
                    continue

                registry.by_regime[
                    regime
                ].append(strategy)

        # ====================================================
        # DETERMINISTIC PRIORITY SORT
        # ====================================================

        for regime in registry.by_regime:

            registry.by_regime[
                regime
            ].sort(

                key=lambda s: (
                    s.priority,
                    s.name,
                )
            )

        # ====================================================
        # SUMMARY
        # ====================================================

        self.logger.info(
            f"Strategies loaded | "
            f"enabled={len(registry.enabled)} "
            f"total={len(registry.all)}"
        )

        return registry

    # ========================================================
    # AUTO DISCOVERY
    # ========================================================

    def discover_from_package(
        self,
        package_name: str,
    ) -> List[Type[BaseStrategy]]:
        """
        Auto-discovers strategies from package.
        """

        discovered = []

        package = importlib.import_module(
            package_name
        )

        for _, module_name, _ in pkgutil.iter_modules(
            package.__path__
        ):

            try:

                full_module = (
                    f"{package_name}."
                    f"{module_name}"
                )

                module = importlib.import_module(
                    full_module
                )

                for _, obj in inspect.getmembers(
                    module,
                    inspect.isclass,
                ):

                    if (
                        issubclass(
                            obj,
                            BaseStrategy
                        )
                        and obj is not BaseStrategy
                    ):

                        discovered.append(obj)

                        self.logger.info(
                            f"Discovered strategy "
                            f"| {obj.__name__}"
                        )

            except Exception as e:

                self.logger.exception(
                    f"Strategy discovery failed "
                    f"| {module_name} | {e}"
                )

        return discovered

    # ========================================================
    # VALIDATION CORE
    # ========================================================

    def _validate_strategy(
        self,
        strategy: BaseStrategy,
        sample_symbol_state: Dict[
            str,
            Any,
        ],
        sample_tf_features: Dict[
            str,
            Dict[str, Any],
        ],
        cache,
        cache_has_sample: bool,
    ) -> StrategyValidationResult:

        missing: List[str] = []

        # ====================================================
        # NAME
        # ====================================================

        if (
            not getattr(strategy, "name", None)
            or not isinstance(strategy.name, str)
        ):

            return StrategyValidationResult(
                name=str(
                    getattr(
                        strategy,
                        "name",
                        "UNKNOWN",
                    )
                ),
                enabled=False,
                missing=["name"],
                reason="Invalid strategy name",
                health_score=0.0,
            )

        # ====================================================
        # REGIMES
        # ====================================================

        allowed = getattr(
            strategy,
            "allowed_regimes",
            None,
        )

        if (
            not isinstance(allowed, set)
            or not allowed
        ):

            missing.append(
                "allowed_regimes"
            )

        else:

            bad = [

                r for r in allowed

                if r not in self.VALID_REGIMES
            ]

            if bad:

                missing.append(
                    f"allowed_regimes_invalid={bad}"
                )

        # ====================================================
        # SYMBOL STATE
        # ====================================================

        req_symbol_state = getattr(
            strategy,
            "required_symbol_state",
            set(),
        ) or set()

        if (
            cache_has_sample
            and req_symbol_state
        ):

            for key in req_symbol_state:

                if key not in sample_symbol_state:

                    missing.append(
                        f"symbol_state:{key}"
                    )

        # ====================================================
        # TIMEFRAMES
        # ====================================================

        req_tfs = getattr(
            strategy,
            "required_timeframes",
            set(),
        ) or set()

        if req_tfs:

            allowed_tfs = getattr(
                cache,
                "allowed_timeframes",
                None,
            )

            for tf in req_tfs:

                if (
                    allowed_tfs is not None
                    and tf not in allowed_tfs
                ):

                    missing.append(
                        f"timeframe_not_allowed:{tf}"
                    )

                if (
                    cache_has_sample
                    and sample_tf_features
                ):

                    if tf not in sample_tf_features:

                        missing.append(
                            f"timeframe_missing:{tf}"
                        )

        # ====================================================
        # FEATURES
        # ====================================================

        req_features = getattr(
            strategy,
            "required_features",
            {},
        ) or {}

        if not isinstance(req_features, dict):

            missing.append(
                "required_features:not_dict"
            )

        else:

            if cache_has_sample:

                for tf, keys in req_features.items():

                    tf_features = (
                        sample_tf_features.get(tf)
                        or {}
                    )

                    for key in keys:

                        if key not in tf_features:

                            missing.append(
                                f"feature_missing:"
                                f"{tf}:{key}"
                            )

        # ====================================================
        # RESULT
        # ====================================================

        enabled = len(missing) == 0

        if enabled:

            return StrategyValidationResult(
                name=strategy.name,
                enabled=True,
                missing=[],
                reason="OK",
                health_score=1.0,
            )

        self.logger.warning(
            f"Strategy disabled "
            f"| {strategy.name} "
            f"| missing={missing}"
        )

        return StrategyValidationResult(
            name=strategy.name,
            enabled=False,
            missing=missing,
            reason="Requirements missing",
            health_score=0.0,
        )
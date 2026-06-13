"""Pluggable strategies. Ship one reference strategy (SMA crossover)."""

from ..config import StrategyConfig
from .base import MarketData, Strategy
from .sma import SmaCrossoverStrategy
from .vwap_reversion import VwapReversionStrategy

_REGISTRY: dict[str, type[Strategy]] = {
    "sma_crossover": SmaCrossoverStrategy,
    "vwap_reversion": VwapReversionStrategy,
}


def build_strategy(config: StrategyConfig) -> Strategy:
    cls = _REGISTRY.get(config.name)
    if cls is None:
        raise ValueError(f"Unknown strategy: {config.name!r}. Known: {list(_REGISTRY)}")
    return cls(config)


__all__ = ["MarketData", "Strategy", "SmaCrossoverStrategy", "VwapReversionStrategy",
           "build_strategy"]

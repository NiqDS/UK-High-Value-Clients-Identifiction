"""Pluggable strategy interface.

A strategy turns market data into *intents* — never orders. Every intent it
emits is still subject to the full risk engine, fee gate, and (optionally)
human approval before anything reaches the exchange.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..domain import OrderIntent
from ..exchange.models import Candle, Ticker


@dataclass
class MarketData:
    symbol: str
    candles: list[Candle] = field(default_factory=list)
    ticker: Ticker | None = None

    @property
    def closes(self) -> list[float]:
        return [c.close for c in self.candles]

    @property
    def last_price(self) -> float | None:
        if self.ticker and self.ticker.last:
            return self.ticker.last
        return self.candles[-1].close if self.candles else None


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate_signals(self, market: MarketData) -> list[OrderIntent]:
        """Return zero or more trade intents for the given market snapshot."""
        raise NotImplementedError

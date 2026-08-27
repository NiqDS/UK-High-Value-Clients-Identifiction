"""Typed, venue-agnostic data structures returned by the exchange adapter.

These wrap ccxt's unified (dict) responses so the rest of the bot never depends
on ccxt's raw shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Balance:
    currency: str
    free: float
    used: float
    total: float


@dataclass(frozen=True)
class AccountBalance:
    balances: dict[str, Balance] = field(default_factory=dict)

    def free(self, currency: str) -> float:
        b = self.balances.get(currency.upper())
        return b.free if b else 0.0

    def total(self, currency: str) -> float:
        b = self.balances.get(currency.upper())
        return b.total if b else 0.0

    def used(self, currency: str) -> float:
        b = self.balances.get(currency.upper())
        return b.used if b else 0.0


@dataclass(frozen=True)
class Ticker:
    symbol: str
    bid: float | None
    ask: float | None
    last: float | None
    base_volume: float | None
    quote_volume: float | None
    timestamp: int | None

    @property
    def spread_pct(self) -> float | None:
        """Bid/ask spread as a percentage of the mid price."""
        if not self.bid or not self.ask:
            return None
        mid = (self.bid + self.ask) / 2.0
        if mid <= 0:
            return None
        return (self.ask - self.bid) / mid * 100.0


@dataclass(frozen=True)
class OrderBook:
    symbol: str
    bids: list[tuple[float, float]]  # (price, amount), best first
    asks: list[tuple[float, float]]
    timestamp: int | None

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

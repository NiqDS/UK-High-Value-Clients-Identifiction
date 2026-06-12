"""Shared test fixtures, including a network-free fake ccxt client."""

from __future__ import annotations

from typing import Any

import pytest


class FakeCcxtClient:
    """Minimal stand-in for a ccxt async exchange. No network."""

    def __init__(self, *, id: str = "fakeex") -> None:
        self.id = id
        self.sandbox_enabled: bool | None = None
        self.closed = False
        self.markets_loaded = False
        self.support_trading_fees = True
        self.created_orders: list = []

    def set_sandbox_mode(self, enabled: bool) -> None:
        self.sandbox_enabled = enabled

    async def load_markets(self, reload: bool = False) -> dict[str, Any]:
        self.markets_loaded = True
        return {"BTC/USD": {"symbol": "BTC/USD"}, "ETH/USD": {"symbol": "ETH/USD"}}

    async def fetch_balance(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "free": {"USD": 1500.0, "BTC": 0.01},
            "used": {"USD": 200.0, "BTC": 0.0},
            "total": {"USD": 1700.0, "BTC": 0.01},
        }

    async def fetch_ticker(self, symbol: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "bid": 100.0,
            "ask": 100.2,
            "last": 100.1,
            "baseVolume": 1234.5,
            "quoteVolume": 123456.0,
            "timestamp": 1_700_000_000_000,
        }

    async def fetch_order_book(
        self, symbol: str, limit: int | None = None, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "bids": [[100.0, 1.0], [99.9, 2.0]],
            "asks": [[100.2, 1.5], [100.3, 3.0]],
            "timestamp": 1_700_000_000_000,
        }

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1m", since: int | None = None,
        limit: int | None = None, params: dict[str, Any] | None = None,
    ) -> list[list[float]]:
        base = 1_700_000_000_000
        return [[base + i * 60_000, 100.0, 101.0, 99.0, 100.5, 10.0] for i in range(limit or 3)]

    async def fetch_trading_fees(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.support_trading_fees:
            raise NotImplementedError("fetch_trading_fees not supported")
        return {"BTC/USD": {"maker": 0.004, "taker": 0.006}}

    async def create_order(
        self, symbol: str, type: str, side: str, amount: float,
        price: float | None = None, params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.created_orders.append((symbol, type, side, amount, price, params or {}))
        return {
            "id": "EX-1",
            "clientOrderId": (params or {}).get("clientOrderId"),
            "status": "closed",
            "filled": amount,
            "average": price if price is not None else 100.0,
            "fee": {"cost": (price or 100.0) * amount * 0.004, "currency": "USD"},
        }

    async def cancel_order(self, id: str, symbol: str | None = None,
                           params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"id": id, "status": "canceled"}

    async def fetch_order(self, id: str, symbol: str | None = None,
                          params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"id": id, "status": "closed"}

    async def fetch_open_orders(self, symbol: str | None = None,
                                params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return []

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_client() -> FakeCcxtClient:
    return FakeCcxtClient()

"""Thin, venue-agnostic adapter over a ccxt (async) exchange client.

Step 1 surface: READ-ONLY market & account data only — balances, tickers,
OHLCV, order book, and trading fees. Order placement/cancel lands in the
execution layer later and always behind the risk engine.

The client is injected, so the adapter is fully unit-testable without network:
pass any object exposing the ccxt async method names.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from .models import AccountBalance, Balance, Candle, OrderBook, Ticker

logger = logging.getLogger(__name__)


class CcxtClient(Protocol):
    """The subset of the ccxt async API this adapter depends on."""

    id: str

    async def load_markets(self, reload: bool = ...) -> dict[str, Any]: ...
    async def fetch_balance(self, params: dict[str, Any] = ...) -> dict[str, Any]: ...
    async def fetch_ticker(self, symbol: str, params: dict[str, Any] = ...) -> dict[str, Any]: ...
    async def fetch_order_book(
        self, symbol: str, limit: int | None = ..., params: dict[str, Any] = ...
    ) -> dict[str, Any]: ...
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = ..., since: int | None = ...,
        limit: int | None = ..., params: dict[str, Any] = ...,
    ) -> list[list[float]]: ...
    async def fetch_trading_fees(self, params: dict[str, Any] = ...) -> dict[str, Any]: ...
    async def create_order(
        self, symbol: str, type: str, side: str, amount: float,
        price: float | None = ..., params: dict[str, Any] = ...,
    ) -> dict[str, Any]: ...
    async def cancel_order(
        self, id: str, symbol: str | None = ..., params: dict[str, Any] = ...
    ) -> dict[str, Any]: ...
    async def fetch_order(
        self, id: str, symbol: str | None = ..., params: dict[str, Any] = ...
    ) -> dict[str, Any]: ...
    async def fetch_open_orders(
        self, symbol: str | None = ..., params: dict[str, Any] = ...
    ) -> list[dict[str, Any]]: ...
    async def fetch_funding_rate_history(
        self, symbol: str | None = ..., since: int | None = ..., limit: int | None = ...,
        params: dict[str, Any] = ...,
    ) -> list[dict[str, Any]]: ...
    async def close(self) -> None: ...


class ExchangeAdapter:
    def __init__(self, client: CcxtClient) -> None:
        self._client = client
        self._markets_loaded = False

    @property
    def id(self) -> str:
        return getattr(self._client, "id", "unknown")

    async def load_markets(self, reload: bool = False) -> None:
        await self._client.load_markets(reload)
        self._markets_loaded = True
        logger.info("Loaded markets for %s", self.id)

    # ----- read-only market & account data --------------------------------
    async def fetch_balance(self) -> AccountBalance:
        raw = await self._client.fetch_balance()
        return _parse_balance(raw)

    async def fetch_ticker(self, symbol: str) -> Ticker:
        raw = await self._client.fetch_ticker(symbol)
        return _parse_ticker(symbol, raw)

    async def fetch_order_book(self, symbol: str, limit: int = 10) -> OrderBook:
        raw = await self._client.fetch_order_book(symbol, limit)
        return _parse_order_book(symbol, raw)

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1m", limit: int = 100, since: int | None = None
    ) -> list[Candle]:
        raw = await self._client.fetch_ohlcv(symbol, timeframe, since, limit)
        return [_parse_candle(row) for row in raw]

    async def fetch_trading_fees(self) -> dict[str, Any]:
        """Per-market maker/taker fees where the venue exposes them.

        Returns ``{}`` if unsupported so callers fall back to configured fees.
        """
        try:
            return await self._client.fetch_trading_fees()
        except Exception as exc:  # NotSupported and friends
            logger.warning("Exchange does not expose trading fees (%s); using config defaults", exc)
            return {}

    # ----- order management (used by the live broker, behind the risk engine) ---
    async def create_order(
        self, symbol: str, order_type: str, side: str, amount: float,
        price: float | None = None, client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Place an order. client_order_id makes submission idempotent across
        reconnects so a retry never double-places."""
        params: dict[str, Any] = {}
        if client_order_id is not None:
            params["clientOrderId"] = client_order_id
        return await self._client.create_order(symbol, order_type, side, amount, price, params)

    async def cancel_order(self, order_id: str, symbol: str | None = None) -> dict[str, Any]:
        return await self._client.cancel_order(order_id, symbol)

    async def fetch_order(self, order_id: str, symbol: str | None = None) -> dict[str, Any]:
        return await self._client.fetch_order(order_id, symbol)

    async def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return await self._client.fetch_open_orders(symbol)

    async def fetch_funding_rate_history(
        self, symbol: str, since: int | None = None, limit: int = 1000
    ) -> list["FundingRate"]:
        from .funding import FundingRate

        raw = await self._client.fetch_funding_rate_history(symbol, since, limit)
        out: list[FundingRate] = []
        for r in raw:
            rate = r.get("fundingRate")
            ts = r.get("timestamp")
            if rate is not None and ts is not None:
                out.append(FundingRate(int(ts), float(rate)))
        return out

    async def close(self) -> None:
        try:
            await self._client.close()
        except Exception:  # pragma: no cover - best effort on shutdown
            logger.debug("Error closing exchange client", exc_info=True)


# ---------------------------------------------------------------------------
# parsing helpers (ccxt unified dict -> typed models)
# ---------------------------------------------------------------------------
def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_balance(raw: dict[str, Any]) -> AccountBalance:
    free = raw.get("free", {}) or {}
    used = raw.get("used", {}) or {}
    total = raw.get("total", {}) or {}
    currencies = set(free) | set(used) | set(total)
    balances: dict[str, Balance] = {}
    for ccy in currencies:
        balances[ccy.upper()] = Balance(
            currency=ccy.upper(),
            free=_f(free.get(ccy)) or 0.0,
            used=_f(used.get(ccy)) or 0.0,
            total=_f(total.get(ccy)) or 0.0,
        )
    return AccountBalance(balances=balances)


def _parse_ticker(symbol: str, raw: dict[str, Any]) -> Ticker:
    return Ticker(
        symbol=raw.get("symbol") or symbol,
        bid=_f(raw.get("bid")),
        ask=_f(raw.get("ask")),
        last=_f(raw.get("last")),
        base_volume=_f(raw.get("baseVolume")),
        quote_volume=_f(raw.get("quoteVolume")),
        timestamp=raw.get("timestamp"),
    )


def _parse_order_book(symbol: str, raw: dict[str, Any]) -> OrderBook:
    def _levels(rows: Any) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for row in rows or []:
            price, amount = _f(row[0]), _f(row[1])
            if price is not None and amount is not None:
                out.append((price, amount))
        return out

    return OrderBook(
        symbol=symbol,
        bids=_levels(raw.get("bids")),
        asks=_levels(raw.get("asks")),
        timestamp=raw.get("timestamp"),
    )


def _parse_candle(row: list[float]) -> Candle:
    return Candle(
        timestamp=int(row[0]),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
    )

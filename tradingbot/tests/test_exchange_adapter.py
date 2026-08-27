"""Exchange adapter tests — fully network-free via the fake ccxt client."""

from __future__ import annotations

import logging

import pytest

from tradingbot.exchange.adapter import ExchangeAdapter
from tradingbot.logging_setup import RedactionFilter, register_secret


async def test_load_markets(fake_client) -> None:
    adapter = ExchangeAdapter(fake_client)
    await adapter.load_markets()
    assert fake_client.markets_loaded is True


async def test_fetch_balance_parses_currencies(fake_client) -> None:
    adapter = ExchangeAdapter(fake_client)
    bal = await adapter.fetch_balance()
    assert bal.free("USD") == 1500.0
    assert bal.used("USD") == 200.0
    assert bal.total("USD") == 1700.0
    assert bal.free("BTC") == 0.01
    # unknown currency -> 0, never KeyError
    assert bal.free("DOGE") == 0.0


async def test_fetch_ticker_and_spread(fake_client) -> None:
    adapter = ExchangeAdapter(fake_client)
    t = await adapter.fetch_ticker("BTC/USD")
    assert t.symbol == "BTC/USD"
    assert t.bid == 100.0 and t.ask == 100.2
    # spread% = (ask-bid)/mid*100
    assert t.spread_pct == pytest.approx(0.2 / 100.1 * 100, rel=1e-6)


async def test_fetch_order_book(fake_client) -> None:
    adapter = ExchangeAdapter(fake_client)
    ob = await adapter.fetch_order_book("BTC/USD", limit=5)
    assert ob.best_bid == 100.0
    assert ob.best_ask == 100.2
    assert ob.bids[0] == (100.0, 1.0)


async def test_fetch_ohlcv(fake_client) -> None:
    adapter = ExchangeAdapter(fake_client)
    candles = await adapter.fetch_ohlcv("BTC/USD", "1m", limit=4)
    assert len(candles) == 4
    assert candles[0].open == 100.0
    assert candles[0].close == 100.5


async def test_fetch_trading_fees_supported(fake_client) -> None:
    adapter = ExchangeAdapter(fake_client)
    fees = await adapter.fetch_trading_fees()
    assert fees["BTC/USD"]["maker"] == 0.004


async def test_fetch_trading_fees_unsupported_returns_empty(fake_client) -> None:
    fake_client.support_trading_fees = False
    adapter = ExchangeAdapter(fake_client)
    assert await adapter.fetch_trading_fees() == {}  # graceful fallback


async def test_close(fake_client) -> None:
    adapter = ExchangeAdapter(fake_client)
    await adapter.close()
    assert fake_client.closed is True


def test_log_redaction_filter_scrubs_secret() -> None:
    register_secret("super-secret-key")
    rec = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="connecting with key super-secret-key", args=(), exc_info=None,
    )
    RedactionFilter().filter(rec)
    assert "super-secret-key" not in rec.msg
    assert "REDACTED" in rec.msg

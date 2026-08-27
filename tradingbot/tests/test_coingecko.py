"""CoinGecko market_chart parsing (no network)."""

from __future__ import annotations

import pytest

from tradingbot.exchange.coingecko import coin_id, parse_market_chart


def test_coin_id_mapping() -> None:
    assert coin_id("BTC/USD") == "bitcoin"
    assert coin_id("XBT/USD") == "bitcoin"
    assert coin_id("ETH/USDT") == "ethereum"
    assert coin_id("FOO/USD") == "foo"  # fallback to lowercased base


def test_parse_market_chart_builds_daily_candles() -> None:
    data = {
        "prices": [[1000, 100.0], [2000, 110.0], [3000, 105.0]],
        "total_volumes": [[1000, 50.0], [2000, 60.0], [3000, 55.0]],
    }
    candles = parse_market_chart(data)
    assert len(candles) == 3
    # first bar: open == close (no prior); volume from total_volumes
    assert candles[0].open == 100.0 and candles[0].close == 100.0
    assert candles[0].volume == 50.0
    # second bar: open = previous close (100), close 110 -> high 110 low 100
    assert candles[1].open == 100.0 and candles[1].close == 110.0
    assert candles[1].high == 110.0 and candles[1].low == 100.0
    assert candles[1].volume == 60.0


def test_parse_market_chart_missing_volume_defaults_zero() -> None:
    data = {"prices": [[1000, 100.0]]}
    candles = parse_market_chart(data)
    assert candles[0].volume == 0.0


def test_parse_market_chart_empty() -> None:
    assert parse_market_chart({}) == []

"""SMA crossover reference-strategy tests."""

from __future__ import annotations

import pytest

from tradingbot.config import StrategyConfig
from tradingbot.domain import Side
from tradingbot.exchange.models import Candle, Ticker
from tradingbot.strategy.base import MarketData
from tradingbot.strategy.sma import SmaCrossoverStrategy, sma


def candles(closes: list[float]) -> list[Candle]:
    return [Candle(timestamp=i, open=c, high=c, low=c, close=c, volume=1.0)
            for i, c in enumerate(closes)]


def market(closes: list[float]) -> MarketData:
    last = closes[-1]
    return MarketData(
        symbol="BTC/USD",
        candles=candles(closes),
        ticker=Ticker("BTC/USD", bid=last, ask=last, last=last,
                      base_volume=1, quote_volume=1, timestamp=0),
    )


def strat() -> SmaCrossoverStrategy:
    return SmaCrossoverStrategy(StrategyConfig(fast_period=2, slow_period=3))


def test_sma_helper() -> None:
    assert sma([1, 2, 3, 4], 2) == 3.5  # mean of last 2
    assert sma([1], 2) is None


def test_insufficient_history_no_signal() -> None:
    assert strat().generate_signals(market([10, 9, 8])) == []  # < slow+1


def test_bullish_crossover_emits_buy_entry() -> None:
    signals = strat().generate_signals(market([10, 9, 8, 7, 12]))
    assert len(signals) == 1
    s = signals[0]
    assert s.side is Side.BUY and s.is_entry is True
    assert s.take_profit_price == pytest.approx(12 * 1.015)
    assert s.stop_price == pytest.approx(12 * 0.99)
    assert s.amount == pytest.approx(40.0 / 12)  # target_notional / price


def test_bearish_crossover_emits_sell_exit() -> None:
    signals = strat().generate_signals(market([7, 8, 9, 10, 5]))
    assert len(signals) == 1
    s = signals[0]
    assert s.side is Side.SELL and s.is_entry is False
    assert s.take_profit_price is None  # exits don't carry a profit target


def test_no_crossover_no_signal() -> None:
    assert strat().generate_signals(market([1, 2, 3, 4, 5])) == []

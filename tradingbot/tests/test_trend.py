"""Donchian breakout trend-following strategy."""

from __future__ import annotations

from tradingbot.config import StrategyConfig
from tradingbot.domain import Side
from tradingbot.exchange.models import Candle, Ticker
from tradingbot.strategy import build_strategy
from tradingbot.strategy.base import MarketData
from tradingbot.strategy.trend import DonchianBreakoutStrategy

_MIN = 60_000


def cfg(**kw) -> StrategyConfig:
    base = dict(name="donchian_breakout", donchian_entry_period=5, donchian_exit_period=3,
                stop_loss_pct=10.0, target_notional_quote=40.0)
    base.update(kw)
    return StrategyConfig(**base)


def candle(i: int, price: float) -> Candle:
    return Candle(i * _MIN, price, price, price, price, 1.0)


def market(prices: list[float], holding: bool = False) -> MarketData:
    candles = [candle(i, p) for i, p in enumerate(prices)]
    last = prices[-1]
    return MarketData("BTC/USD", candles, Ticker("BTC/USD", last, last, last, 1, 1, 0), holding)


def test_registered_in_factory() -> None:
    assert isinstance(build_strategy(cfg()), DonchianBreakoutStrategy)


def test_breakout_triggers_long_entry() -> None:
    # flat around 100, then a breakout above the prior 5-bar high
    prices = [100, 100, 100, 100, 100, 100, 105]
    sigs = DonchianBreakoutStrategy(cfg()).generate_signals(market(prices))
    assert len(sigs) == 1
    s = sigs[0]
    assert s.side == Side.BUY and s.is_entry
    assert s.take_profit_price is None  # rides the trend, no fixed TP
    assert s.stop_price < 105


def test_no_entry_without_breakout() -> None:
    prices = [100, 101, 100, 99, 100, 100, 100]  # no new high
    assert DonchianBreakoutStrategy(cfg()).generate_signals(market(prices)) == []


def test_channel_exit_when_holding() -> None:
    # holding, price breaks below the prior 3-bar low
    prices = [100, 100, 100, 100, 105, 104, 95]
    sigs = DonchianBreakoutStrategy(cfg()).generate_signals(market(prices, holding=True))
    assert len(sigs) == 1
    assert sigs[0].side == Side.SELL and not sigs[0].is_entry


def test_hold_when_above_exit_channel() -> None:
    prices = [100, 100, 100, 100, 105, 106, 107]  # trending up, stay in
    assert DonchianBreakoutStrategy(cfg()).generate_signals(market(prices, holding=True)) == []

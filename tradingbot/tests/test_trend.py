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


def test_regime_gate_blocks_breakout_below_sma() -> None:
    # a downtrend that consolidates low then stages a relief-rally breakout: price
    # (110) pops above the prior 3-bar high (100) but is still below the 7-bar SMA
    # (~123) -> the regime gate suppresses the long.
    prices = [200, 150, 100, 100, 100, 100, 110]
    base = dict(trend_filter_enabled=True, trend_period=7,
                donchian_entry_period=3, donchian_exit_period=2)
    ungated = dict(donchian_entry_period=3, donchian_exit_period=2)
    # sanity: without the gate the same breakout WOULD fire
    assert DonchianBreakoutStrategy(cfg(**ungated)).generate_signals(market(prices)) != []
    assert DonchianBreakoutStrategy(cfg(**base)).generate_signals(market(prices)) == []


def test_regime_gate_allows_breakout_above_sma() -> None:
    # uptrend: breakout above the prior high AND above the SMA -> long fires
    prices = [100, 101, 102, 103, 104, 105, 112]
    c = cfg(trend_filter_enabled=True, trend_period=6)
    sigs = DonchianBreakoutStrategy(c).generate_signals(market(prices))
    assert len(sigs) == 1 and sigs[0].side == Side.BUY


def test_regime_gate_stands_aside_without_enough_history() -> None:
    prices = [100, 100, 100, 100, 100, 100, 105]  # only 7 bars, need 200
    c = cfg(trend_filter_enabled=True, trend_period=200)
    assert DonchianBreakoutStrategy(c).generate_signals(market(prices)) == []


def test_regime_gate_never_blocks_exit() -> None:
    # holding into a breakdown below the SMA must still EXIT (gate is entry-only)
    prices = [200, 180, 160, 140, 120, 110, 95]
    c = cfg(trend_filter_enabled=True, trend_period=6)
    sigs = DonchianBreakoutStrategy(c).generate_signals(market(prices, holding=True))
    assert len(sigs) == 1 and sigs[0].side == Side.SELL

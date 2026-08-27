"""Trend filter, VWAP mean-reversion strategy, and the comparison harness."""

from __future__ import annotations

import pytest

from tradingbot.backtest.compare import BASELINE, default_experiments, run_comparison
from tradingbot.backtest.engine import BacktestConfig
from tradingbot.backtest.metrics import net_return_over_maxdd
from tradingbot.backtest.synthetic import synthetic_candles
from tradingbot.config import StrategyConfig
from tradingbot.domain import Side
from tradingbot.exchange.models import Candle, Ticker
from tradingbot.strategy.base import MarketData
from tradingbot.strategy.sma import SmaCrossoverStrategy
from tradingbot.strategy.vwap_reversion import VwapReversionStrategy


def md(closes: list[float], holding: bool = False, volumes: list[float] | None = None) -> MarketData:
    vols = volumes or [1.0] * len(closes)
    cs = [Candle(i, c, c, c, c, v) for i, (c, v) in enumerate(zip(closes, vols))]
    last = closes[-1]
    return MarketData("BTC/USD", cs, Ticker("BTC/USD", last, last, last, 1, 1, 0), holding=holding)


# --- trend filter ----------------------------------------------------------
def test_trend_filter_blocks_entry_below_long_sma() -> None:
    # genuine bullish cross at the last bar, but price (85) is below the 5-bar
    # trend SMA (~108) -> trend filter blocks it; without the filter it would buy.
    closes = [200, 100, 80, 78, 85]
    base = dict(fast_period=2, slow_period=3, trend_period=5, vwap_filter_enabled=False)
    with_filter = SmaCrossoverStrategy(StrategyConfig(**base, trend_filter_enabled=True))
    no_filter = SmaCrossoverStrategy(StrategyConfig(**base, trend_filter_enabled=False))
    assert with_filter.generate_signals(md(closes)) == []          # blocked
    assert len(no_filter.generate_signals(md(closes))) == 1        # would have bought


def test_trend_filter_allows_entry_above_long_sma() -> None:
    cfg = StrategyConfig(fast_period=2, slow_period=3, trend_period=5,
                         vwap_filter_enabled=False, trend_filter_enabled=True)
    s = SmaCrossoverStrategy(cfg)
    # rising series: a bullish cross where price is above the 5-bar trend SMA
    sigs = s.generate_signals(md([10, 11, 9, 12, 14]))
    assert len(sigs) == 1 and sigs[0].side is Side.BUY


# --- vwap reversion --------------------------------------------------------
def test_reversion_buys_below_vwap() -> None:
    cfg = StrategyConfig(vwap_window=5, reversion_entry_pct=1.0)
    s = VwapReversionStrategy(cfg)
    # last price well below the average of the window -> undervalued -> buy
    sigs = s.generate_signals(md([100, 100, 100, 100, 90]))
    assert len(sigs) == 1
    assert sigs[0].side is Side.BUY and sigs[0].is_entry is True
    assert sigs[0].take_profit_price > sigs[0].price  # target = VWAP above entry


def test_reversion_no_buy_near_or_above_vwap() -> None:
    s = VwapReversionStrategy(StrategyConfig(vwap_window=5, reversion_entry_pct=1.0))
    assert s.generate_signals(md([100, 100, 100, 100, 101])) == []  # above VWAP


def test_reversion_exits_when_reverted() -> None:
    s = VwapReversionStrategy(StrategyConfig(vwap_window=5, reversion_entry_pct=1.0))
    # holding, price back above VWAP -> sell exit
    sigs = s.generate_signals(md([90, 92, 95, 98, 101], holding=True))
    assert len(sigs) == 1 and sigs[0].side is Side.SELL and sigs[0].is_entry is False


# --- comparison harness ----------------------------------------------------
def test_default_experiments_cover_mechanics() -> None:
    exps = default_experiments(StrategyConfig(fast_period=5, slow_period=15))
    names = {e.name for e in exps}
    assert BASELINE in names
    assert "add_trend_filter" in names
    assert "H2_vwap_reversion" in names
    mechanics = {e.mechanic for e in exps}
    assert "trend_filter" in mechanics
    assert "mean_reversion (thesis)" in mechanics


def test_run_comparison_produces_ranked_report() -> None:
    candles = synthetic_candles(n=1200, seed=5)
    base = StrategyConfig(fast_period=5, slow_period=15)
    exps = default_experiments(base)
    bt = BacktestConfig(fee_pct=0.6, slippage_pct=0.05, oos_ratio=0.4)
    scores, text = run_comparison(candles, exps, bt, net_return_over_maxdd)
    assert len(scores) == len(exps)
    assert "Strategy comparison" in text
    assert "Mechanic attribution" in text
    assert "Best OOS" in text
    # every experiment appears in the table
    for e in exps:
        assert e.name in text

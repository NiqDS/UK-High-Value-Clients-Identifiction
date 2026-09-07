"""Tests for the Phase-A refinements: fee-drag gates, VWAP sizing, ATR exits."""

from __future__ import annotations

import pytest

from tradingbot.config import StrategyConfig
from tradingbot.domain import Side
from tradingbot.exchange.models import Candle, Ticker
from tradingbot.strategy.base import MarketData
from tradingbot.strategy.sma import SmaCrossoverStrategy, atr
from tradingbot.backtest.engine import Backtester, BacktestConfig


def candles(ohlc: list[tuple[float, float, float, float]]) -> list[Candle]:
    return [Candle(timestamp=i * 60_000, open=o, high=h, low=l, close=c, volume=1.0)
            for i, (o, h, l, c) in enumerate(ohlc)]


def market_from_closes(closes: list[float], holding: bool = False) -> MarketData:
    cs = [Candle(i, c, c, c, c, 1.0) for i, c in enumerate(closes)]
    last = closes[-1]
    return MarketData("BTC/USD", cs,
                      Ticker("BTC/USD", last, last, last, 1, 1, 0), holding=holding)


# bullish-cross series where the entry is undervalued vs VWAP (passes the floor)
BULLISH = [120, 118, 90, 88, 100]


def test_atr_helper() -> None:
    cs = candles([(10, 12, 9, 11), (11, 13, 10, 12), (12, 14, 11, 13)])
    assert atr(cs, period=2) is not None
    assert atr(cs, period=10) is None  # not enough bars


def test_min_crossover_strength_filters_weak_signals() -> None:
    # require a very strong crossover -> the modest BULLISH cross is filtered
    s = SmaCrossoverStrategy(StrategyConfig(
        fast_period=2, slow_period=3, vwap_filter_enabled=True, min_crossover_strength_pct=50.0))
    assert s.generate_signals(market_from_closes(BULLISH)) == []


def test_cooldown_blocks_back_to_back_entries() -> None:
    cfg = dict(fast_period=2, slow_period=3, vwap_filter_enabled=True)
    # with a cooldown, the same crossover bar cannot fire a second entry
    cd = SmaCrossoverStrategy(StrategyConfig(**cfg, trade_cooldown_bars=100))
    assert len(cd.generate_signals(market_from_closes(BULLISH))) == 1   # first allowed
    assert cd.generate_signals(market_from_closes(BULLISH)) == []       # blocked by cooldown
    # without a cooldown the second call would emit again (proves the cause)
    no_cd = SmaCrossoverStrategy(StrategyConfig(**cfg, trade_cooldown_bars=0))
    no_cd.generate_signals(market_from_closes(BULLISH))
    assert len(no_cd.generate_signals(market_from_closes(BULLISH))) == 1


def test_vwap_scaled_sizing_grows_with_undervaluation() -> None:
    cfg = StrategyConfig(
        fast_period=2, slow_period=3, vwap_filter_enabled=True, sizing_mode="vwap_scaled",
        vwap_size_min_mult=0.5, vwap_size_max_mult=1.5, vwap_full_size_discount_pct=2.0,
        buy_valuation_floor_pct=5.0,  # allow the entry through regardless of depth
    )
    s = SmaCrossoverStrategy(cfg)
    sig = s.generate_signals(market_from_closes(BULLISH))[0]
    base = cfg.target_notional_quote / sig.price
    # ~3% undervalued, capped at the 2% full-size discount -> max multiplier (1.5x)
    assert sig.amount == pytest.approx(base * 1.5)


def test_atr_exits_set_tp_and_stop_from_volatility() -> None:
    cfg = StrategyConfig(fast_period=2, slow_period=3, vwap_filter_enabled=True,
                         exit_mode="atr", atr_period=2, atr_tp_mult=2.0, atr_sl_mult=1.0)
    s = SmaCrossoverStrategy(cfg)
    # OHLC bullish cross with real ranges so ATR > 0
    cs = candles([(120, 122, 118, 120), (118, 119, 116, 118), (90, 92, 88, 90),
                  (88, 90, 86, 88), (100, 103, 97, 100)])
    md = MarketData("BTC/USD", cs, Ticker("BTC/USD", 100, 100, 100, 1, 1, 0))
    sig = s.generate_signals(md)[0]
    a = atr(cs, 2)
    assert sig.take_profit_price == pytest.approx(100 + 2.0 * a)
    assert sig.stop_price == pytest.approx(100 - 1.0 * a)


def test_trailing_stop_ratchets_in_backtester() -> None:
    # enter at bar1 open=100; price climbs; trailing stop (distance 5) should
    # ratchet up and exit on the pullback rather than round-tripping.
    cs = [
        Candle(0, 100, 100, 100, 100, 1),
        Candle(60_000, 100, 100, 100, 100, 1),    # entry fill here
        Candle(120_000, 100, 120, 100, 118, 1),   # high 120 -> stop ratchets to 115
        Candle(180_000, 118, 119, 114, 116, 1),   # low 114 <= 115 -> trailing stop hit
        Candle(240_000, 116, 117, 110, 112, 1),
    ]

    from tradingbot.domain import OrderIntent, OrderType, Side as S
    from tradingbot.strategy.base import Strategy

    class TrailStrat(Strategy):
        name = "t"
        def generate_signals(self, market):
            if len(market.candles) == 1:
                return [OrderIntent("BTC/USD", S.BUY, 1.0, OrderType.MARKET, is_entry=True,
                                    metadata={"trail_distance": 5.0})]
            return []

    res = Backtester(BacktestConfig(fee_pct=0.0, slippage_pct=0.0)).run(cs, TrailStrat())
    assert res.num_trades == 1
    assert res.trades[0].reason == "stop"
    assert res.trades[0].exit_price == pytest.approx(115.0)  # ratcheted 120 - 5

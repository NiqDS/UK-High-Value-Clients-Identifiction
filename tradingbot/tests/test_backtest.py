"""Backtester tests: zero look-ahead, next-open fills, fee impact, OOS split."""

from __future__ import annotations

import pytest

from tradingbot.backtest.engine import Backtester, BacktestConfig
from tradingbot.backtest.synthetic import synthetic_candles
from tradingbot.config import StrategyConfig
from tradingbot.domain import OrderIntent, OrderType, Side
from tradingbot.exchange.models import Candle
from tradingbot.strategy.base import MarketData, Strategy
from tradingbot.strategy.sma import SmaCrossoverStrategy


def candles(opens_closes: list[tuple[float, float]]) -> list[Candle]:
    out = []
    for i, (o, c) in enumerate(opens_closes):
        out.append(Candle(timestamp=i * 60_000, open=o, high=max(o, c), low=min(o, c),
                          close=c, volume=1.0))
    return out


class ScriptedStrategy(Strategy):
    """Emits BUY when it has seen `buy_at` bars, SELL at `sell_at`. No TP/stop,
    so exits only happen on the SELL signal — isolates engine mechanics."""

    name = "scripted"

    def __init__(self, buy_at: int, sell_at: int) -> None:
        self.buy_at, self.sell_at = buy_at, sell_at

    def generate_signals(self, market: MarketData) -> list[OrderIntent]:
        n = len(market.candles)
        if n == self.buy_at:
            return [OrderIntent(symbol=market.symbol, side=Side.BUY, amount=1.0,
                                order_type=OrderType.MARKET, is_entry=True)]
        if n == self.sell_at:
            return [OrderIntent(symbol=market.symbol, side=Side.SELL, amount=1.0,
                                order_type=OrderType.MARKET, is_entry=False)]
        return []


class RecordingStrategy(Strategy):
    name = "recording"

    def __init__(self) -> None:
        self.seen_lengths: list[int] = []
        self.last_timestamps: list[int] = []

    def generate_signals(self, market: MarketData) -> list[OrderIntent]:
        self.seen_lengths.append(len(market.candles))
        self.last_timestamps.append(market.candles[-1].timestamp)
        return []


def test_no_look_ahead_strategy_sees_only_past() -> None:
    cs = candles([(100, 100)] * 6)
    rec = RecordingStrategy()
    Backtester().run(cs, rec)
    # strategy is called once per bar with a strictly growing prefix 1..N
    assert rec.seen_lengths == [1, 2, 3, 4, 5, 6]
    assert rec.last_timestamps == [c.timestamp for c in cs]


def test_fill_happens_at_next_bar_open_with_slippage() -> None:
    # opens distinct from closes so we can prove fills use the OPEN
    cs = candles([(99, 100), (101, 100), (102, 100), (103, 100), (104, 100)])
    bt = Backtester(BacktestConfig(initial_equity=10_000, fee_pct=0.0, slippage_pct=1.0))
    res = bt.run(cs, ScriptedStrategy(buy_at=1, sell_at=3))
    assert res.num_trades == 1
    t = res.trades[0]
    # signal at bar index 0 (len 1) -> fill at bar index 1 open = 101 * (1+1%)
    assert t.entry_price == pytest.approx(101 * 1.01)
    # signal at bar index 2 (len 3) -> fill at bar index 3 open = 103 * (1-1%)
    assert t.exit_price == pytest.approx(103 * 0.99)


def test_fees_reduce_net_return_but_not_gross() -> None:
    cs = candles([(99, 100), (110, 100), (120, 100), (130, 100), (140, 100)])
    script = lambda: ScriptedStrategy(buy_at=1, sell_at=3)  # noqa: E731
    free = Backtester(BacktestConfig(fee_pct=0.0, slippage_pct=0.0)).run(cs, script())
    costly = Backtester(BacktestConfig(fee_pct=1.0, slippage_pct=0.0)).run(cs, script())
    assert costly.gross_return_pct == pytest.approx(free.gross_return_pct)
    assert costly.net_return_pct < free.net_return_pct
    assert costly.fees_paid > 0


def test_stop_is_hit_intrabar() -> None:
    # enter long at bar1 open=100; bar2 dips to low 90 with stop 95 -> stop fills
    cs = [
        Candle(0, 100, 100, 100, 100, 1),
        Candle(60_000, 100, 101, 99, 100, 1),
        Candle(120_000, 100, 101, 90, 100, 1),  # low 90 triggers stop 95
        Candle(180_000, 100, 101, 99, 100, 1),
    ]

    class StopStrat(Strategy):
        name = "s"
        def generate_signals(self, market):
            if len(market.candles) == 1:
                return [OrderIntent("BTC/USD", Side.BUY, 1.0, OrderType.MARKET,
                                    stop_price=95.0, is_entry=True)]
            return []

    res = Backtester(BacktestConfig(fee_pct=0.0, slippage_pct=0.0)).run(cs, StopStrat())
    assert res.num_trades == 1
    assert res.trades[0].reason == "stop"
    assert res.trades[0].exit_price == pytest.approx(95.0)


def test_out_of_sample_split() -> None:
    cs = synthetic_candles(n=200, seed=1)
    bt = Backtester(BacktestConfig(oos_ratio=0.25))
    factory = lambda: SmaCrossoverStrategy(StrategyConfig(fast_period=5, slow_period=15))  # noqa: E731
    in_s, oos = bt.run_oos(cs, factory)
    # in-sample uses 75% of bars, OOS the rest
    assert len(in_s.equity_curve) == 150
    assert len(oos.equity_curve) == 50


def test_summary_mentions_net_of_fees() -> None:
    cs = synthetic_candles(n=100, seed=3)
    res = Backtester().run(cs, SmaCrossoverStrategy(StrategyConfig(fast_period=5, slow_period=15)))
    text = res.summary("in-sample")
    assert "NET return" in text
    assert "fees paid" in text


class OversizedBuyStrategy(Strategy):
    """Asks to buy far more notional than the account holds, then sells — used to
    prove the engine caps deployment at available cash (no implicit leverage)."""

    name = "oversized"

    def generate_signals(self, market: MarketData) -> list[OrderIntent]:
        n = len(market.candles)
        if n == 1 and not market.holding:
            return [OrderIntent(symbol=market.symbol, side=Side.BUY, amount=1_000_000.0,
                                order_type=OrderType.MARKET, is_entry=True)]
        if n == 3 and market.holding:
            return [OrderIntent(symbol=market.symbol, side=Side.SELL, amount=1_000_000.0,
                                order_type=OrderType.MARKET, is_entry=False)]
        return []


def test_no_leverage_caps_deployment_at_cash() -> None:
    # price 100, equity 1000 -> at most ~10 units affordable, not the 1,000,000 asked
    cs = candles([(100, 100), (100, 100), (100, 50), (100, 50), (100, 50)])
    bt = Backtester(BacktestConfig(initial_equity=1_000, fee_pct=0.0, slippage_pct=0.0))
    res = bt.run(cs, OversizedBuyStrategy())
    assert res.num_trades == 1
    # bought ~10 units (cash/price), so the 50% drop costs ~500, never more than equity
    assert res.trades[0].units == pytest.approx(10.0, rel=1e-6)
    # equity never goes negative and drawdown is bounded at 100%
    assert min(res.equity_curve) >= 0.0
    assert res.max_drawdown_pct <= 100.0


def test_full_deployment_drawdown_bounded() -> None:
    # full-sleeve sizing on a volatile series: equity stays non-negative throughout
    cs = synthetic_candles(n=300, seed=7, drift=-0.003, vol=0.05)
    base = StrategyConfig(name="donchian_breakout", donchian_entry_period=10,
                          donchian_exit_period=5, target_notional_quote=10_000.0)
    from tradingbot.strategy.trend import DonchianBreakoutStrategy
    res = Backtester(BacktestConfig(initial_equity=10_000.0, fee_pct=0.6,
                                    slippage_pct=0.05)).run(cs, DonchianBreakoutStrategy(base))
    assert min(res.equity_curve) >= 0.0
    assert res.max_drawdown_pct <= 100.0

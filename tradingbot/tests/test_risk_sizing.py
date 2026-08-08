"""Risk-based sizing wrapper + the risk-limit sweep."""

from __future__ import annotations

import pytest

from tradingbot.backtest.engine import BacktestConfig
from tradingbot.backtest.portfolio import portfolio_backtest, risk_sweep_report
from tradingbot.backtest.synthetic import synthetic_candles
from tradingbot.config import StrategyConfig
from tradingbot.domain import OrderIntent, OrderType, Side
from tradingbot.exchange.models import Candle, Ticker
from tradingbot.strategy.base import MarketData, Strategy
from tradingbot.strategy.risk_sizing import RiskSizedStrategy


class _Entry(Strategy):
    """Emits a fixed entry with a stop 10 below the price."""
    name = "e"

    def generate_signals(self, market):
        return [OrderIntent(symbol="X", side=Side.BUY, amount=1.0, order_type=OrderType.MARKET,
                            price=100.0, stop_price=90.0, is_entry=True, reason="x")]


def _md():
    c = [Candle(0, 100, 100, 100, 100, 1)]
    return MarketData("X", c, Ticker("X", 100, 100, 100, 1, 1, 0), holding=False)


def test_risk_sizing_sets_amount_from_stop_distance() -> None:
    # risk 2% of 1000 = 20; stop distance = 10 -> units = 20/10 = 2.0
    sized = RiskSizedStrategy(_Entry(), equity=1000.0, risk_pct=2.0)
    s = sized.generate_signals(_md())[0]
    assert s.amount == pytest.approx(2.0)


def test_tighter_stop_bigger_position() -> None:
    class _Tight(_Entry):
        def generate_signals(self, market):
            return [OrderIntent(symbol="X", side=Side.BUY, amount=1.0, order_type=OrderType.MARKET,
                                price=100.0, stop_price=98.0, is_entry=True, reason="x")]
    wide = RiskSizedStrategy(_Entry(), 1000.0, 2.0).generate_signals(_md())[0].amount
    tight = RiskSizedStrategy(_Tight(), 1000.0, 2.0).generate_signals(_md())[0].amount
    assert tight > wide     # same risk, tighter stop -> more units


def test_no_stop_passes_through_untouched() -> None:
    class _NoStop(Strategy):
        name = "n"
        def generate_signals(self, market):
            return [OrderIntent(symbol="X", side=Side.BUY, amount=0.4, order_type=OrderType.MARKET,
                                price=100.0, is_entry=True, reason="x")]
    s = RiskSizedStrategy(_NoStop(), 1000.0, 2.0).generate_signals(_md())[0]
    assert s.amount == 0.4  # unchanged


def test_exits_not_resized() -> None:
    class _Exit(Strategy):
        name = "x"
        def generate_signals(self, market):
            return [OrderIntent(symbol="X", side=Side.SELL, amount=0.4, order_type=OrderType.MARKET,
                                price=100.0, stop_price=90.0, is_entry=False, reason="x")]
    s = RiskSizedStrategy(_Exit(), 1000.0, 2.0).generate_signals(_md())[0]
    assert s.amount == 0.4  # exits untouched


def _assets(n=700):
    return [("A", synthetic_candles(n=n, seed=1, drift=0.001)),
            ("B", synthetic_candles(n=n, seed=2, drift=0.0006))]


def test_portfolio_risk_sizing_runs() -> None:
    base = StrategyConfig(donchian_entry_period=20, donchian_exit_period=10)
    bt = BacktestConfig(initial_equity=6000.0, fee_pct=0.6, slippage_pct=0.05)
    res = portfolio_backtest(_assets(), base, bt, risk_sizing_pct=2.0)
    assert res.bars > 0 and res.trades >= 0


def test_risk_sweep_report_ranks_levels() -> None:
    base = StrategyConfig(donchian_entry_period=20, donchian_exit_period=10)
    bt = BacktestConfig(initial_equity=6000.0, fee_pct=0.6, slippage_pct=0.05)
    report = risk_sweep_report(_assets(), base, bt, [1.0, 3.0, 8.0], label="test")
    assert "Risk-limit sweep" in report
    assert "Best risk-adjusted limit" in report
    for r in ("  1.0 |", "  3.0 |", "  8.0 |"):
        assert r in report

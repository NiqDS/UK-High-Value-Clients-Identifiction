"""Risk-based sizing wrapper + the risk-limit sweep."""

from __future__ import annotations

import pytest

from tradingbot.backtest.engine import BacktestConfig
from tradingbot.backtest.portfolio import (
    deploy_sweep_report,
    portfolio_backtest,
    risk_sweep_report,
)
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


def test_compounding_sizes_off_market_equity() -> None:
    # when the backtester supplies a marked equity, sizing compounds off IT, not
    # the fixed starting equity
    from tradingbot.exchange.models import Candle
    sized = RiskSizedStrategy(_Entry(), equity=1000.0, risk_pct=2.0)
    c = [Candle(0, 100, 100, 100, 100, 1)]
    md = MarketData("X", c, Ticker("X", 100, 100, 100, 1, 1, 0), holding=False, equity=2000.0)
    # risk 2% of the CURRENT 2000 = 40; stop distance 10 -> 4.0 units (not 2.0)
    assert sized.generate_signals(md)[0].amount == pytest.approx(4.0)


def test_risk_sweep_report_reframes_and_lists_levels() -> None:
    base = StrategyConfig(donchian_entry_period=20, donchian_exit_period=10)
    bt = BacktestConfig(initial_equity=6000.0, fee_pct=0.6, slippage_pct=0.05)
    report = risk_sweep_report(_assets(), base, bt, [1.0, 3.0, 8.0], label="test")
    assert "Risk-limit sweep (compounding)" in report
    assert "CHOOSE BY DRAWDOWN TOLERANCE" in report
    assert "NOT 'pick the highest'" in report
    for r in ("  1.0 |", "  3.0 |", "  8.0 |"):
        assert r in report


def test_over_betting_is_capped_not_rewarded() -> None:
    # compounding + no-leverage cap: a very high risk% must NOT produce a wildly
    # larger return than a high-but-reasonable one (it saturates at full deploy)
    base = StrategyConfig(donchian_entry_period=20, donchian_exit_period=10)
    bt = BacktestConfig(initial_equity=6000.0, fee_pct=0.6, slippage_pct=0.05)
    r_hi = portfolio_backtest(_assets(), base, bt, risk_sizing_pct=25.0)
    r_extreme = portfolio_backtest(_assets(), base, bt, risk_sizing_pct=100.0)
    # 4x the risk must not give materially more return once deployment is capped
    assert r_extreme.net_pct <= r_hi.net_pct * 1.05 + 1.0


def test_deploy_pct_scales_return_and_drawdown_together() -> None:
    # halving the deployment fraction leaves the rest as idle cash, so both return%
    # and drawdown% shrink roughly linearly -> return/dd stays about the same.
    base = StrategyConfig(donchian_entry_period=20, donchian_exit_period=10)
    bt = BacktestConfig(initial_equity=6000.0, fee_pct=0.6, slippage_pct=0.05)
    full = portfolio_backtest(_assets(), base, bt, deploy_pct=100.0)
    half = portfolio_backtest(_assets(), base, bt, deploy_pct=50.0)
    # half deployment earns less and draws down less than full
    assert abs(half.net_pct) < abs(full.net_pct)
    assert half.maxdd_pct <= full.maxdd_pct + 1e-6
    # return/dd ratio is roughly preserved (efficiency ~flat, not a free lunch)
    if full.maxdd_pct > 0 and half.maxdd_pct > 0:
        rr_full = full.net_pct / full.maxdd_pct
        rr_half = half.net_pct / half.maxdd_pct
        assert rr_half == pytest.approx(rr_full, rel=0.35)


def test_deploy_sweep_report_lists_levels_and_reads() -> None:
    base = StrategyConfig(donchian_entry_period=20, donchian_exit_period=10)
    bt = BacktestConfig(initial_equity=6000.0, fee_pct=0.6, slippage_pct=0.05)
    report = deploy_sweep_report(_assets(), base, bt, [100.0, 50.0, 25.0], label="test")
    assert "Deployment-fraction sweep" in report
    assert "~real dd%" in report
    for r in ("   100  |", "    50  |", "    25  |"):
        assert r in report
    # the Read section describes efficiency truthfully — either FLAT or NOT flat,
    # never both; and it is data-driven, not a fixed assertion
    assert ("roughly FLAT" in report) ^ ("NOT flat here" in report)

"""Portfolio (trend index) backtest."""

from __future__ import annotations

import math

import pytest

from tradingbot.backtest.engine import BacktestConfig
from tradingbot.backtest.portfolio import (
    align_on_common_timestamps, compute_weights, portfolio_backtest, portfolio_report,
)
from tradingbot.backtest.synthetic import synthetic_candles
from tradingbot.config import StrategyConfig
from tradingbot.exchange.models import Candle


def _assets(n=600):
    return [
        ("BTC", synthetic_candles(n=n, seed=1, drift=0.0008)),
        ("ETH", synthetic_candles(n=n, seed=2, drift=0.0006)),
        ("BNB", synthetic_candles(n=n, seed=3, drift=0.0004)),
    ]


def test_align_intersects_timestamps() -> None:
    a = synthetic_candles(n=100, seed=1)
    # b is shifted: drop the first 10 bars so the common set is 90 bars
    b = synthetic_candles(n=100, seed=2)[10:]
    ts, aligned = align_on_common_timestamps([("A", a), ("B", b)])
    assert len(ts) == 90
    assert len(aligned["A"]) == 90 and len(aligned["B"]) == 90
    # both kept lists share the exact same timestamp axis
    assert [c.timestamp for c in aligned["A"]] == [c.timestamp for c in aligned["B"]]


def test_equal_weights_sum_to_one_and_are_uniform() -> None:
    _, aligned = align_on_common_timestamps(_assets())
    w = compute_weights(aligned, "equal")
    assert math.isclose(sum(w.values()), 1.0)
    assert all(math.isclose(v, 1 / 3) for v in w.values())


def test_invvol_weights_favor_calmer_coins() -> None:
    calm = synthetic_candles(n=400, seed=5, vol=0.005)
    wild = synthetic_candles(n=400, seed=6, vol=0.05)
    _, aligned = align_on_common_timestamps([("CALM", calm), ("WILD", wild)])
    w = compute_weights(aligned, "invvol")
    assert math.isclose(sum(w.values()), 1.0)
    # the lower-volatility coin gets the larger allocation
    assert w["CALM"] > w["WILD"]


def test_portfolio_backtest_combines_sleeves() -> None:
    base = StrategyConfig(donchian_entry_period=20, donchian_exit_period=10)
    bt = BacktestConfig(initial_equity=9_000.0, fee_pct=0.1, slippage_pct=0.05)
    res = portfolio_backtest(_assets(), base, bt, weight_mode="equal")

    assert len(res.sleeves) == 3
    # equal weight: each sleeve allocated 1/3 of the capital
    assert all(math.isclose(s.alloc_weight, 1 / 3, abs_tol=1e-9) for s in res.sleeves)
    assert all(math.isclose(s.initial, 3_000.0, abs_tol=1e-6) for s in res.sleeves)
    # the portfolio start equals the sum of sleeves; final equals sum of sleeve finals
    assert math.isclose(res.initial, 9_000.0)
    assert math.isclose(res.final, sum(s.final for s in res.sleeves), rel_tol=1e-9)
    # final-value weights sum to 1
    assert math.isclose(sum(s.final_weight for s in res.sleeves), 1.0, abs_tol=1e-6)
    # total trades is the sum across sleeves
    assert res.trades == sum(s.trades for s in res.sleeves)
    assert res.bars > 0


def test_contribution_weights_sum_to_one_when_pnl_nonzero() -> None:
    base = StrategyConfig(donchian_entry_period=15, donchian_exit_period=8)
    bt = BacktestConfig(initial_equity=6_000.0, fee_pct=0.1, slippage_pct=0.05)
    res = portfolio_backtest(_assets(), base, bt)
    total_pnl = res.final - res.initial
    if abs(total_pnl) > 1e-6:
        assert math.isclose(sum(s.contrib_weight for s in res.sleeves), 1.0, abs_tol=1e-6)


def test_report_renders_index_row_and_weights() -> None:
    base = StrategyConfig(donchian_entry_period=20, donchian_exit_period=10)
    bt = BacktestConfig(initial_equity=9_000.0, fee_pct=0.1, slippage_pct=0.05)
    res = portfolio_backtest(_assets(), base, bt)
    report = portfolio_report(res, label="3 coins")
    assert "Trend index backtest" in report
    assert "INDEX" in report
    assert "alloc%" in report and "contrib%" in report and "final%" in report
    assert "Diversification" in report
    # the report self-documents the cost model it ran with
    assert "fees 0.1%/side" in report


def test_fee_changes_result() -> None:
    """A higher fee must lower net return (guards against the fee not reaching
    the per-coin sleeves)."""
    base = StrategyConfig(donchian_entry_period=20, donchian_exit_period=10)
    assets = _assets()
    lo = portfolio_backtest(
        assets, base, BacktestConfig(initial_equity=9_000.0, fee_pct=0.1, slippage_pct=0.05))
    hi = portfolio_backtest(
        assets, base, BacktestConfig(initial_equity=9_000.0, fee_pct=0.6, slippage_pct=0.05))
    assert hi.net_pct < lo.net_pct
    assert hi.fee_pct == 0.6 and lo.fee_pct == 0.1


def test_no_common_window_raises() -> None:
    a = [Candle(timestamp=1000 + i * 60_000, open=1, high=1, low=1, close=1, volume=1)
         for i in range(60)]
    b = [Candle(timestamp=999_000_000 + i * 60_000, open=1, high=1, low=1, close=1, volume=1)
         for i in range(60)]
    with pytest.raises(ValueError, match="common timestamps"):
        portfolio_backtest([("A", a), ("B", b)], StrategyConfig(), BacktestConfig())

"""Cross-asset comparison report."""

from __future__ import annotations

from tradingbot.backtest.compare import (
    cross_asset_report, segment_report, trend_sweep_report,
)
from tradingbot.backtest.metrics import METRICS
from tradingbot.backtest.synthetic import synthetic_candles
from tradingbot.config import StrategyConfig


def test_cross_asset_report_tabulates_each_asset() -> None:
    a = synthetic_candles(n=1200, seed=1)
    b = synthetic_candles(n=1200, seed=2)
    report = cross_asset_report(
        [("AAA", a, 0.6), ("BBB", b, 0.01)],
        StrategyConfig(), oos_ratio=0.4, slippage_pct=0.05, metric=METRICS["net_return_over_maxdd"],
    )
    assert "Cross-asset comparison" in report
    assert "AAA" in report and "BBB" in report
    # each asset row carries its own fee column
    assert "0.60" in report and "0.01" in report
    assert "best thesis" in report
    assert "trend net%/score" in report


def test_segment_report_splits_into_n() -> None:
    candles = synthetic_candles(n=1500, seed=3)
    report = segment_report(
        candles, StrategyConfig(), n_segments=5, fee_pct=0.01, slippage_pct=0.05,
        metric=METRICS["net_return_over_maxdd"], label="test",
    )
    # 5 segment rows (seg index 0..4) present
    for i in range(5):
        assert f"\n{i:2d}  |" in report
    assert "net-positive in" in report


def test_trend_sweep_skips_invalid_and_tabulates() -> None:
    candles = synthetic_candles(n=1500, seed=4)
    report = trend_sweep_report(
        candles, StrategyConfig(), entry_periods=[20, 30], exit_periods=[10, 30],
        fee_pct=0.1, slippage_pct=0.05, oos_ratio=0.4,
        metric=METRICS["net_return_over_maxdd"], label="t",
    )
    assert "Donchian param sweep" in report
    # exit>=entry combos (30/30) are skipped; valid combos present
    assert "net-positive in" in report
    assert "   20 |   10" in report

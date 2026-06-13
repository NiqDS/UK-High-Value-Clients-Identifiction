"""Cross-asset comparison report."""

from __future__ import annotations

from tradingbot.backtest.compare import cross_asset_report
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
    assert "reversion wins?" in report

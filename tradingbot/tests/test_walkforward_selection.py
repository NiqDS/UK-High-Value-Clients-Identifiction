"""Walk-forward coin selection: train/test split, ranking, honest verdict."""

from __future__ import annotations

import dataclasses

from tradingbot.analysis.walkforward_selection import (
    _spearman,
    render_walkforward_report,
    walkforward_select,
)
from tradingbot.backtest.engine import BacktestConfig
from tradingbot.backtest.synthetic import synthetic_candles
from tradingbot.config import StrategyConfig

DAY = 86_400_000
START = 1_609_459_200_000  # 2021-01-01


def _daily(n, seed, drift):
    base = synthetic_candles(n=n, seed=seed, drift=drift)
    return [dataclasses.replace(c, timestamp=START + i * DAY) for i, c in enumerate(base)]


def _cfg():
    return StrategyConfig(donchian_entry_period=20, donchian_exit_period=10)


def test_spearman_rank_correlation() -> None:
    import pytest
    assert _spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)    # identical order
    assert _spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)   # inverted
    assert abs(_spearman([1, 2, 3], [2, 2, 2])) < 1e-9                    # no variance -> 0


def test_walkforward_runs_and_reports() -> None:
    assets = [("A", _daily(800, 1, 0.001)), ("B", _daily(800, 2, 0.0008)),
              ("C", _daily(800, 3, 0.0006)), ("D", _daily(800, 4, 0.0003))]
    split_ms = START + 500 * DAY   # train first 500 bars, test the rest
    bt = BacktestConfig(initial_equity=8000.0, fee_pct=0.6, slippage_pct=0.05)
    wf = walkforward_select(assets, _cfg(), bt, split_ms)
    # every coin scored on both sides of the split
    assert set(wf.train) == {"A", "B", "C", "D"}
    assert wf.selected  # non-empty selection
    report = render_walkforward_report(wf, _cfg(), label="test")
    assert "Walk-forward coin selection" in report
    assert "rank stability" in report
    assert "Out-of-sample TEST portfolio" in report
    assert "Verdict" in report
    # the honest framing is always present
    assert "TEST is data the selection never saw" in report


def test_top_k_limits_selection() -> None:
    assets = [("A", _daily(800, 1, 0.001)), ("B", _daily(800, 2, 0.0008)),
              ("C", _daily(800, 3, 0.0006)), ("D", _daily(800, 4, 0.0003))]
    bt = BacktestConfig(initial_equity=8000.0, fee_pct=0.6, slippage_pct=0.05)
    wf = walkforward_select(assets, _cfg(), bt, START + 500 * DAY, top_k=2)
    assert len(wf.selected) == 2

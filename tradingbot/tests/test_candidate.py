"""Candidate-screening for basket admission."""

from __future__ import annotations

from tradingbot.analysis.candidate import (
    MIN_BARS,
    render_candidate_report,
    screen_candidate,
)
from tradingbot.backtest.engine import BacktestConfig
from tradingbot.backtest.synthetic import synthetic_candles
from tradingbot.config import StrategyConfig


def _cfg():
    return StrategyConfig(donchian_entry_period=20, donchian_exit_period=10)


def _bt():
    return BacktestConfig(initial_equity=7000.0, fee_pct=0.6, slippage_pct=0.05)


def _basket(n=700):
    return [("A", synthetic_candles(n=n, seed=1, drift=0.001)),
            ("B", synthetic_candles(n=n, seed=2, drift=0.0008)),
            ("C", synthetic_candles(n=n, seed=3, drift=0.0006))]


def test_short_history_candidate_is_rejected() -> None:
    # a brand-new listing (few bars) cannot be validated -> REJECT
    cand = synthetic_candles(n=40, seed=99, drift=0.001)
    v = screen_candidate(_basket(), "NEW", cand, _cfg(), _bt())
    assert v.enough_history is False
    assert v.promote is False
    assert any("insufficient history" in r for r in v.reasons)
    assert "REJECT" in render_candidate_report(v)


def test_report_lists_verdict_and_windowed_comparison() -> None:
    cand = synthetic_candles(n=700, seed=7, drift=0.0009)
    v = screen_candidate(_basket(), "CAND", cand, _cfg(), _bt())
    report = render_candidate_report(v)
    assert "Candidate screen — CAND" in report
    assert "basket return/dd (same window)" in report
    assert ("PROMOTE" in report) or ("REJECT" in report)
    # comparison ran over a real window with both baskets scored
    assert v.base_rr != 0.0 or v.with_rr != 0.0


def test_min_bars_threshold_is_the_gate() -> None:
    # exactly at the threshold counts as enough; just under does not
    cfg, bt = _cfg(), _bt()
    ok = screen_candidate(_basket(), "OK", synthetic_candles(n=MIN_BARS, seed=5, drift=0.001),
                          cfg, bt)
    short = screen_candidate(_basket(), "NO", synthetic_candles(n=MIN_BARS - 1, seed=5, drift=0.001),
                             cfg, bt)
    assert ok.enough_history is True and short.enough_history is False

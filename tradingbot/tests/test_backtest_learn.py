"""Backtest->learn bridge: analyse daily backtest trades with the payoff lens."""

from __future__ import annotations

import json

from tradingbot.analysis.backtest_learn import (
    export_trades_jsonl,
    render_backtest_learn_report,
    run_backtest_trades,
)
from tradingbot.backtest.engine import BacktestConfig
from tradingbot.backtest.synthetic import synthetic_candles
from tradingbot.config import StrategyConfig


def _cfg():
    return StrategyConfig(donchian_entry_period=20, donchian_exit_period=10)


def _bt():
    return BacktestConfig(initial_equity=10_000.0, fee_pct=0.6, slippage_pct=0.05)


def _assets(n=700):
    return [("BTC", synthetic_candles(n=n, seed=1, drift=0.001)),
            ("ETH", synthetic_candles(n=n, seed=2, drift=0.0007))]


def test_run_backtest_trades_tags_symbols() -> None:
    tagged = run_backtest_trades(_assets(), _cfg(), _bt())
    assert tagged, "expected some closed trades"
    syms = {s for s, _ in tagged}
    assert syms <= {"BTC", "ETH"}
    # each tag is (symbol, Trade) with the fields the report needs
    s, t = tagged[0]
    assert hasattr(t, "net_pnl") and hasattr(t, "entry_price") and hasattr(t, "units")


def test_report_shows_expectancy_and_payoff() -> None:
    tagged = run_backtest_trades(_assets(), _cfg(), _bt())
    report = render_backtest_learn_report(tagged, _cfg(), _bt(), label="test")
    # the breakout-strategy lens the live learn report lacks
    assert "payoff ratio:" in report
    assert "expectancy:" in report
    assert "Per-symbol" in report
    assert "Candidate adjustments" in report


def test_export_jsonl_is_ingestible_by_learn_loader(tmp_path) -> None:
    tagged = run_backtest_trades(_assets(), _cfg(), _bt())
    out = tmp_path / "bt.jsonl"
    n = export_trades_jsonl(tagged, str(out))
    assert n == len(tagged)
    rows = [json.loads(ln) for ln in out.read_text().splitlines() if ln.strip()]
    assert rows and all(r["source"] == "backtest" for r in rows)
    assert all({"symbol", "pnl", "entry_price", "exit_price"} <= r.keys() for r in rows)
    # the learn loader parses it back into TradeSamples
    from tradingbot.learning.samples import load_file
    samples = load_file(out)
    assert len(samples) == n
    assert all(s.source == "backtest" for s in samples)

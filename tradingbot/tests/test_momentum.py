"""Cross-sectional momentum gate (MomentumRank + MomentumGatedStrategy)."""

from __future__ import annotations

import pytest

from tradingbot.backtest.engine import BacktestConfig
from tradingbot.backtest.portfolio import portfolio_backtest, portfolio_report
from tradingbot.backtest.synthetic import synthetic_candles
from tradingbot.config import StrategyConfig
from tradingbot.domain import OrderIntent, OrderType, Side
from tradingbot.exchange.models import Candle, Ticker
from tradingbot.strategy.base import MarketData, Strategy
from tradingbot.strategy.momentum import MomentumGatedStrategy, MomentumRank

_MIN = 60_000


def series(closes: list[float]) -> list[Candle]:
    return [Candle(i * _MIN, c, c, c, c, 1.0) for i, c in enumerate(closes)]


def test_rank_top_k_by_trailing_return() -> None:
    aligned = {
        "UP": series([100, 110, 121, 133, 146]),     # strong up
        "FLAT": series([100, 100, 100, 100, 100]),   # flat
        "DOWN": series([100, 95, 90, 85, 80]),       # down
    }
    rank = MomentumRank.build(aligned, lookback=2, top_k=1)
    ts = 4 * _MIN
    assert rank.allowed("UP", ts) is True
    assert rank.allowed("FLAT", ts) is False
    assert rank.allowed("DOWN", ts) is False


def test_rank_is_causal_and_flips_with_leadership() -> None:
    # A leads early; B overtakes later — membership must flip only AFTER the
    # data shows it, using closes up to the queried bar only.
    a = series([100, 120, 140, 140, 140, 140])   # early runner, then stalls
    b = series([100, 100, 100, 120, 150, 190])   # late runner
    rank = MomentumRank.build({"A": a, "B": b}, lookback=2, top_k=1)
    assert rank.allowed("A", 2 * _MIN) is True    # A's +40% beats B's 0%
    assert rank.allowed("B", 2 * _MIN) is False
    assert rank.allowed("B", 5 * _MIN) is True    # B's +58% beats A's 0%
    assert rank.allowed("A", 5 * _MIN) is False


def test_no_history_means_not_rankable() -> None:
    rank = MomentumRank.build({"A": series([100, 101, 102])}, lookback=5, top_k=1)
    assert rank.allowed("A", 2 * _MIN) is False  # fewer bars than lookback
    # before any timestamp at all
    assert rank.allowed("A", -1) is False


class AlwaysEnter(Strategy):
    name = "always_enter"

    def generate_signals(self, market: MarketData) -> list[OrderIntent]:
        if market.holding:
            return [OrderIntent(symbol=market.symbol, side=Side.SELL, amount=1.0,
                                order_type=OrderType.MARKET, price=100.0, is_entry=False,
                                reason="exit")]
        return [OrderIntent(symbol=market.symbol, side=Side.BUY, amount=1.0,
                            order_type=OrderType.MARKET, price=100.0, is_entry=True,
                            reason="enter")]


def _md(candles: list[Candle], holding: bool) -> MarketData:
    return MarketData("A/USDT", candles,
                      Ticker("A/USDT", 100, 100, 100, 1, 1, 0), holding)


def test_gate_blocks_entries_outside_top_k_but_never_exits() -> None:
    strong = series([100, 110, 121, 133, 146])
    weak = series([100, 99, 98, 97, 96])
    rank = MomentumRank.build({"S": strong, "W": weak}, lookback=2, top_k=1)

    weak_gated = MomentumGatedStrategy(AlwaysEnter(), "W", rank)
    strong_gated = MomentumGatedStrategy(AlwaysEnter(), "S", rank)

    # entries: only the strong coin passes
    assert weak_gated.generate_signals(_md(weak, holding=False)) == []
    assert len(strong_gated.generate_signals(_md(strong, holding=False))) == 1
    # exits: ALWAYS pass, even for the weak coin
    exits = weak_gated.generate_signals(_md(weak, holding=True))
    assert len(exits) == 1 and exits[0].is_entry is False


def test_portfolio_backtest_with_momentum_gate_runs_and_reports() -> None:
    assets = [
        ("AAA", synthetic_candles(n=700, seed=1, drift=0.002)),
        ("BBB", synthetic_candles(n=700, seed=2, drift=0.0)),
        ("CCC", synthetic_candles(n=700, seed=3, drift=-0.001)),
    ]
    base = StrategyConfig(donchian_entry_period=20, donchian_exit_period=10)
    bt = BacktestConfig(initial_equity=9_000.0, fee_pct=0.1, slippage_pct=0.05)
    gated = portfolio_backtest(assets, base, bt, momentum_lookback=30, momentum_top_k=1)
    ungated = portfolio_backtest(assets, base, bt)

    assert gated.momentum_lookback == 30 and gated.momentum_top_k == 1
    assert ungated.momentum_lookback == 0
    # the gate can only reduce or keep total entries — never add
    assert gated.trades <= ungated.trades
    report = portfolio_report(gated, label="gated")
    assert "momentum gate: entries only in the top 1 of 3 by 30-bar return" in report
    assert "momentum gate" not in portfolio_report(ungated, label="raw")


def test_cli_rejects_momentum_without_top_k() -> None:
    # exercised at the CLI layer: --momentum requires --top-k (and vice versa)
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "tradingbot", "portfolio",
         "--asset", "X=nonexistent.csv", "--momentum", "60"],
        capture_output=True, text=True,
        cwd="/home/user/UK-High-Value-Clients-Identifiction/tradingbot",
    )
    assert proc.returncode == 2
    assert "--momentum and --top-k must be set together" in proc.stderr + proc.stdout

"""Walk-forward harness, learners (param optimizer + Q-learning), and CSV I/O."""

from __future__ import annotations

import pytest

from tradingbot.backtest.data import load_csv, save_csv
from tradingbot.backtest.engine import BacktestConfig
from tradingbot.backtest.learners.param_optimizer import ParamOptimizer
from tradingbot.backtest.learners.qlearning import QLearner, QPolicyStrategy, regime_state
from tradingbot.backtest.metrics import net_return_over_maxdd
from tradingbot.backtest.synthetic import synthetic_candles
from tradingbot.backtest.walkforward import split_windows, walk_forward
from tradingbot.config import StrategyConfig
from tradingbot.strategy.base import Strategy


BASE = StrategyConfig(fast_period=5, slow_period=15)
BT = BacktestConfig(fee_pct=0.6, slippage_pct=0.05)


# --- CSV round-trip --------------------------------------------------------
def test_csv_roundtrip(tmp_path) -> None:
    cs = synthetic_candles(n=20, seed=1)
    p = tmp_path / "d.csv"
    save_csv(p, cs)
    back = load_csv(p)
    assert len(back) == 20
    assert back[0].close == pytest.approx(cs[0].close)
    assert back[-1].timestamp == cs[-1].timestamp


# --- window split ----------------------------------------------------------
def test_split_windows_covers_all_bars() -> None:
    cs = synthetic_candles(n=403, seed=1)
    wins = split_windows(cs, 4)
    assert len(wins) == 4
    assert sum(len(w) for w in wins) == 403  # last window absorbs remainder


def test_split_windows_rejects_zero() -> None:
    with pytest.raises(ValueError):
        split_windows([], 0)


# --- metric ----------------------------------------------------------------
def test_metric_zero_trades_is_zero() -> None:
    from tradingbot.backtest.engine import Backtester
    flat = synthetic_candles(n=100, seed=1)

    class Never(Strategy):
        name = "never"
        def generate_signals(self, market):
            return []

    res = Backtester(BT).run(flat, Never())
    assert net_return_over_maxdd(res) == 0.0


# --- param optimizer -------------------------------------------------------
def test_param_optimizer_returns_strategy_and_is_deterministic() -> None:
    cs = synthetic_candles(n=400, seed=2)
    o1 = ParamOptimizer(BT, net_return_over_maxdd, n_samples=15, seed=7)
    o2 = ParamOptimizer(BT, net_return_over_maxdd, n_samples=15, seed=7)
    f1, f2 = o1.learn(cs, BASE), o2.learn(cs, BASE)
    assert isinstance(f1(), Strategy)
    # same seed -> same best config found
    assert o1.best_config.model_dump() == o2.best_config.model_dump()
    # best score is at least the baseline (baseline is always a candidate)
    assert o1.best_score >= net_return_over_maxdd(
        __import__("tradingbot.backtest.engine", fromlist=["Backtester"]).Backtester(BT)
        .run(cs, __import__("tradingbot.strategy.sma", fromlist=["SmaCrossoverStrategy"])
             .SmaCrossoverStrategy(BASE))
    )


# --- Q-learning ------------------------------------------------------------
def test_regime_state_buckets() -> None:
    assert regime_state(-1.0, 0.2) == ("under", "lo")
    assert regime_state(1.0, 2.0) == ("over", "hi")
    assert regime_state(0.0, 1.0) == ("fair", "mid")


def test_qlearner_trains_and_policy_generates_signals() -> None:
    cs = synthetic_candles(n=500, seed=3)
    learner = QLearner(BT, net_return_over_maxdd, episodes=3, seed=1)
    factory = learner.learn(cs, BASE)
    policy = factory()
    assert isinstance(policy, QPolicyStrategy)
    assert learner.q  # learned at least one state
    # policy produces valid (possibly empty) signal lists without error
    from tradingbot.strategy.base import MarketData
    out = policy.generate_signals(MarketData("BTC/USD", cs[:60], cs[59:60] and None))  # type: ignore
    assert isinstance(out, list)


def test_qpolicy_skip_action_suppresses_entry() -> None:
    cs = synthetic_candles(n=120, seed=4)
    # force every state's best action to "skip"
    q = {}
    policy = QPolicyStrategy(BASE, q)
    # monkeypatch: make _best_action return skip by seeding q for any state with skip high
    from tradingbot.backtest.learners import qlearning
    orig = qlearning._best_action
    qlearning._best_action = lambda q, s: "skip"
    try:
        from tradingbot.strategy.base import MarketData
        from tradingbot.exchange.models import Ticker
        md = MarketData("BTC/USD", cs, Ticker("BTC/USD", cs[-1].close, cs[-1].close,
                                              cs[-1].close, 1, 1, 0))
        out = policy.generate_signals(md)
        assert all(s.side.value != "buy" for s in out)  # entries suppressed
    finally:
        qlearning._best_action = orig


# --- full walk-forward -----------------------------------------------------
def test_walk_forward_scores_each_window_oos() -> None:
    cs = synthetic_candles(n=1200, seed=5)
    learner = ParamOptimizer(BT, net_return_over_maxdd, n_samples=10, seed=1)
    rep = walk_forward(cs, BASE, learner, BT, net_return_over_maxdd, n_windows=4)
    assert len(rep.windows) == 4
    assert rep.windows[0].learned_oos_score is None  # nothing learned before window 0
    assert all(w.learned_oos_score is not None for w in rep.windows[1:])
    text = rep.render()
    assert "param_optimizer" in text and "baseline" in text

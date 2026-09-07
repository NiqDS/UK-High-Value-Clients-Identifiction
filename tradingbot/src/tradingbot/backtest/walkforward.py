"""Walk-forward evaluation with per-window learning.

The workflow you asked for: split the history into N chronological windows
(e.g. 4 quarters over 12 months). For each window:
  - score the *baseline* params on it (a fixed reference),
  - score the params *learned on the previous window* on it — this is the honest
    out-of-sample test: did last window's learning generalise to unseen data?
  - then learn on this window and carry that forward.

Because every window is scored on data the learner never saw, overfitting shows
up as a gap between a learner's in-sample score and its next-window OOS score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..config import StrategyConfig
from ..strategy.base import Strategy
from ..strategy.sma import SmaCrossoverStrategy
from .engine import Backtester, BacktestConfig, BacktestResult
from .metrics import Metric


class Learner(Protocol):
    name: str
    def learn(self, candles, base_config: StrategyConfig) -> Callable[[], Strategy]: ...


@dataclass
class WindowReport:
    index: int
    n_bars: int
    baseline_score: float
    learned_oos_score: float | None  # params learned on the PREVIOUS window, scored here (unseen)
    in_sample_score: float           # best the learner found ON this window (optimistic)
    baseline_result: BacktestResult
    learned_result: BacktestResult | None


@dataclass
class LearnerReport:
    learner: str
    windows: list[WindowReport] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"### Learner: {self.learner}",
                 "win |  bars | baseline | learned(OOS) | in-sample | verdict"]
        for w in self.windows:
            learned = "    n/a    " if w.learned_oos_score is None else f"{w.learned_oos_score:+9.3f}"
            verdict = ""
            if w.learned_oos_score is not None:
                verdict = "better" if w.learned_oos_score > w.baseline_score else "worse/equal"
            lines.append(
                f" {w.index:2d} | {w.n_bars:5d} | {w.baseline_score:+8.3f} | {learned} | "
                f"{w.in_sample_score:+9.3f} | {verdict}"
            )
        oos = [w.learned_oos_score for w in self.windows if w.learned_oos_score is not None]
        if oos:
            wins = sum(1 for w in self.windows
                       if w.learned_oos_score is not None and w.learned_oos_score > w.baseline_score)
            lines.append(f"OOS windows where learning beat baseline: {wins}/{len(oos)} "
                         f"(mean OOS score {sum(oos) / len(oos):+.3f})")
        return "\n".join(lines)


def split_windows(candles, n_windows: int) -> list[list]:
    if n_windows <= 0:
        raise ValueError("n_windows must be > 0")
    size = len(candles) // n_windows
    return [candles[i * size: (i + 1) * size if i < n_windows - 1 else len(candles)]
            for i in range(n_windows)]


def walk_forward(
    candles, base_config: StrategyConfig, learner: Learner,
    bt_config: BacktestConfig, metric: Metric, n_windows: int = 4,
) -> LearnerReport:
    windows = split_windows(candles, n_windows)
    report = LearnerReport(learner=learner.name)
    prev_factory: Callable[[], Strategy] | None = None

    for i, w in enumerate(windows):
        bt = Backtester(bt_config)
        baseline_result = bt.run(w, SmaCrossoverStrategy(base_config))
        baseline_score = metric(baseline_result)

        learned_oos_score = None
        learned_result = None
        if prev_factory is not None:  # OOS: last window's learner on this unseen window
            learned_result = bt.run(w, prev_factory())
            learned_oos_score = metric(learned_result)

        factory = learner.learn(w, base_config)  # learn on this window (logs/state fresh)
        in_sample_score = metric(bt.run(w, factory()))

        report.windows.append(WindowReport(
            index=i, n_bars=len(w), baseline_score=baseline_score,
            learned_oos_score=learned_oos_score, in_sample_score=in_sample_score,
            baseline_result=baseline_result, learned_result=learned_result,
        ))
        prev_factory = factory
    return report

"""Scoring metrics for backtests / the walk-forward learner.

The default is risk-adjusted (net-of-fees return divided by max drawdown), which
aligns with the capital-preservation mandate.
"""

from __future__ import annotations

from typing import Callable

from .engine import BacktestResult

Metric = Callable[[BacktestResult], float]


def net_return_over_maxdd(result: BacktestResult) -> float:
    """Net-of-fees return penalised by max drawdown. No trades => 0 (neither
    rewarded nor punished — capital preserved, but no edge demonstrated)."""
    if result.num_trades == 0:
        return 0.0
    return result.net_return_pct / max(result.max_drawdown_pct, 0.1)


def net_return(result: BacktestResult) -> float:
    return result.net_return_pct


def net_vs_benchmark(benchmark_pct: float) -> Metric:
    def _m(result: BacktestResult) -> float:
        return result.net_return_pct - benchmark_pct
    return _m


METRICS: dict[str, Metric] = {
    "net_return_over_maxdd": net_return_over_maxdd,
    "net_return": net_return,
}

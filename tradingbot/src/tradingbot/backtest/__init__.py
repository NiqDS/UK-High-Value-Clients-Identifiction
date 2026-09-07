"""Honest, zero-look-ahead backtesting with out-of-sample + net-of-fees report."""

from .data import load_csv, save_csv
from .engine import Backtester, BacktestConfig, BacktestResult, Trade
from .metrics import METRICS, net_return_over_maxdd
from .walkforward import LearnerReport, split_windows, walk_forward

__all__ = [
    "Backtester", "BacktestConfig", "BacktestResult", "Trade",
    "load_csv", "save_csv", "METRICS", "net_return_over_maxdd",
    "LearnerReport", "split_windows", "walk_forward",
]

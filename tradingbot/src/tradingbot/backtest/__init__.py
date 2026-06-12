"""Honest, zero-look-ahead backtesting with out-of-sample + net-of-fees report."""

from .engine import Backtester, BacktestConfig, BacktestResult, Trade

__all__ = ["Backtester", "BacktestConfig", "BacktestResult", "Trade"]

"""Tabular Q-learning (contextual bandit) over market regime → sizing action.

State = (valuation regime vs VWAP, volatility regime via ATR). Action = how much
to size a bullish-crossover entry (skip / half / full / double). Reward = the
net-of-fees outcome of the resulting trade. With gamma=0 this is a contextual
bandit — appropriate for sparse, independent entry decisions and far less
overfit-prone than deep RL on a year of bars.

``learn`` trains the table on a window and returns a strategy factory whose
:class:`QPolicyStrategy` applies the learned policy; the walk-forward then scores
it on the next, unseen window so any overfitting is visible.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

from ...config import StrategyConfig
from ...domain import Side
from ...strategy.base import MarketData, Strategy
from ...strategy.sma import SmaCrossoverStrategy, atr, sma, vwap
from ..engine import BacktestConfig

ACTIONS: dict[str, float] = {"skip": 0.0, "half": 0.5, "full": 1.0, "double": 2.0}


def regime_state(valuation_pct: float, atr_pct: float) -> tuple[str, str]:
    val = "under" if valuation_pct <= -0.5 else ("over" if valuation_pct >= 0.5 else "fair")
    vol = "lo" if atr_pct < 0.5 else ("hi" if atr_pct > 1.5 else "mid")
    return (val, vol)


def _best_action(q: dict, state: tuple[str, str]) -> str:
    row = q.get(state)
    if not row:
        return "full"  # unseen state -> trade normally
    return max(row, key=row.get)


@dataclass
class QLearner:
    bt_config: BacktestConfig
    metric: object  # Metric — kept for interface symmetry (scoring done by walk-forward)
    episodes: int = 6
    alpha: float = 0.3
    epsilon: float = 0.2
    horizon: int = 40
    seed: int = 0
    name: str = "qlearning"
    q: dict = field(default_factory=dict)

    def _ensure(self, state) -> dict:
        return self.q.setdefault(state, {a: 0.0 for a in ACTIONS})

    def learn(self, candles, base_config: StrategyConfig) -> Callable[[], Strategy]:
        rng = random.Random(self.seed)
        fast, slow = base_config.fast_period, base_config.slow_period
        fee = self.bt_config.fee_pct / 100.0
        self.q = {}
        for _ in range(self.episodes):
            for i in range(slow + 1, len(candles) - 1):
                closes = [c.close for c in candles[: i + 1]]
                f_now, s_now = sma(closes, fast), sma(closes, slow)
                f_prev, s_prev = sma(closes[:-1], fast), sma(closes[:-1], slow)
                if None in (f_now, s_now, f_prev, s_prev):
                    continue
                if not (f_prev <= s_prev and f_now > s_now):  # bullish crossover only
                    continue
                price = closes[-1]
                wac = vwap(candles[: i + 1], base_config.vwap_window) or price
                a = atr(candles[: i + 1], base_config.atr_period) or 0.0
                state = regime_state((price - wac) / wac * 100.0, a / price * 100.0 if price else 0.0)
                row = self._ensure(state)
                action = rng.choice(list(ACTIONS)) if rng.random() < self.epsilon else _best_action(self.q, state)
                reward = self._simulate(candles, i, base_config, ACTIONS[action], fee)
                row[action] += self.alpha * (reward - row[action])
        return lambda: QPolicyStrategy(base_config, dict(self.q))

    def _simulate(self, candles, i: int, cfg: StrategyConfig, size_mult: float, fee: float) -> float:
        """Net-of-fees % outcome of entering at bar i+1 and exiting on TP/SL or
        after the horizon. Skip (size 0) yields 0."""
        if size_mult <= 0:
            return 0.0
        entry = candles[i + 1].open
        tp = entry * (1 + cfg.take_profit_pct / 100.0)
        sl = entry * (1 - cfg.stop_loss_pct / 100.0)
        exit_price = candles[min(i + self.horizon, len(candles) - 1)].close
        for j in range(i + 1, min(i + self.horizon, len(candles))):
            if candles[j].low <= sl:
                exit_price = sl
                break
            if candles[j].high >= tp:
                exit_price = tp
                break
        gross_pct = (exit_price - entry) / entry * 100.0
        return (gross_pct - 2 * fee * 100.0) * size_mult


class QPolicyStrategy(Strategy):
    name = "q_policy"

    def __init__(self, base_config: StrategyConfig, q: dict) -> None:
        self.base_config = base_config
        self.q = q
        self._base = SmaCrossoverStrategy(base_config)

    def generate_signals(self, market: MarketData):
        out = []
        for s in self._base.generate_signals(market):
            if s.side == Side.BUY and s.is_entry:
                price = market.last_price or s.price or 0.0
                a = atr(market.candles, self.base_config.atr_period) or 0.0
                state = regime_state(
                    (s.metadata or {}).get("valuation_pct", 0.0),
                    a / price * 100.0 if price else 0.0,
                )
                mult = ACTIONS[_best_action(self.q, state)]
                if mult <= 0:
                    continue  # policy says skip
                import dataclasses
                out.append(dataclasses.replace(s, amount=s.amount * mult))
            else:
                out.append(s)
        return out

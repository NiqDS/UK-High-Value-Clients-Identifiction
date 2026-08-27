"""Risk-based position sizing wrapper.

Instead of deploying a fixed notional, size each entry so the loss IF THE STOP
FILLS is a fixed fraction of capital:

    units = (risk_pct/100 * equity) / |entry_price - stop_price|

Tighter stop -> bigger position for the same risk; wider stop -> smaller. This
is the standard "risk a constant % per trade" sizing, and it's what a risk-limit
sweep varies to find the level with the best risk-adjusted return. Entries
without a stop, or with a zero stop distance, are passed through untouched (the
base sizing stands). Over-sizing is still bounded by the engine's no-leverage
cash cap.
"""

from __future__ import annotations

import dataclasses

from ..domain import OrderIntent
from .base import MarketData, Strategy


class RiskSizedStrategy(Strategy):
    name = "risk_sized"

    def __init__(self, base: Strategy, equity: float, risk_pct: float) -> None:
        self.base = base
        self.equity = equity
        self.risk_pct = risk_pct

    def generate_signals(self, market: MarketData) -> list[OrderIntent]:
        # COMPOUND: size off the current marked equity when the backtester supplies
        # it (drawdowns shrink the base, over-betting is punished); fall back to the
        # fixed starting equity otherwise. Equity can't go below 0.
        equity = market.equity if market.equity is not None else self.equity
        budget = max(equity, 0.0) * self.risk_pct / 100.0
        out: list[OrderIntent] = []
        for s in self.base.generate_signals(market):
            if s.is_entry and s.stop_price is not None and s.price:
                dist = abs(s.price - s.stop_price)
                if dist > 0:
                    s = dataclasses.replace(s, amount=budget / dist)
            out.append(s)
        return out

"""Core domain types shared across strategy, risk, execution, and approval.

Kept dependency-free so every layer can import it without cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass(frozen=True)
class OrderIntent:
    """A strategy's *request* to trade. Not an order until the risk engine
    approves it and the execution layer places it.

    - ``amount`` is in base units (e.g. BTC).
    - ``price`` is the limit price, or the strategy's best estimate of the entry
      price for market orders / sizing. If ``None`` the risk engine falls back
      to the live ticker.
    - ``take_profit_price`` is the target exit used by the fee gate to prove the
      move clears round-trip fees. Required for entries.
    - ``stop_price`` enables the per-trade risk-cap check (risk = distance to stop).
    """

    symbol: str
    side: Side
    amount: float
    order_type: OrderType = OrderType.LIMIT
    price: float | None = None
    take_profit_price: float | None = None
    stop_price: float | None = None
    is_entry: bool = True
    client_order_id: str | None = None
    reason: str = ""
    metadata: dict = field(default_factory=dict)

    def notional(self, est_price: float) -> float:
        """Quote-currency notional at the given price."""
        return abs(self.amount) * est_price

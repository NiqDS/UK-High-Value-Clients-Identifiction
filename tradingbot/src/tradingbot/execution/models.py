"""Execution-layer data structures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from ..domain import OrderType, Side


class LiquidityRole(str, Enum):
    MAKER = "maker"
    TAKER = "taker"


class ExecStatus(str, Enum):
    FILLED = "filled"
    OPEN = "open"  # resting (live maker order not yet filled)
    REJECTED = "rejected"  # execution-layer guard rejected (e.g. slippage, taker not allowed)
    SKIPPED = "skipped"


@dataclass
class Order:
    client_order_id: str
    symbol: str
    side: Side
    amount: float
    order_type: OrderType
    role: LiquidityRole
    price: float | None  # limit price (None => pure market)
    reference_price: float  # mid/decision price used for slippage accounting


@dataclass
class Fill:
    client_order_id: str
    symbol: str
    side: Side
    price: float
    amount: float
    fee_quote: float
    role: LiquidityRole
    cost_quote: float
    timestamp: datetime

    @property
    def net_cost_quote(self) -> float:
        """Quote out the door for a buy (cost + fee); quote received for a sell
        is computed by the caller. Always includes the fee."""
        return self.cost_quote + self.fee_quote


@dataclass
class ExecutionResult:
    status: ExecStatus
    order: Order | None
    fill: Fill | None = None
    reason: str = ""
    exchange_order_id: str | None = None

    @property
    def filled(self) -> bool:
        return self.status is ExecStatus.FILLED


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

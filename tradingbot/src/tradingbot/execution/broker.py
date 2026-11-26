"""Brokers turn an :class:`Order` into a fill (or resting order).

- :class:`PaperBroker` simulates fills locally — no exchange contact. Default in
  dry-run mode. Submission is idempotent by client order id.
- :class:`LiveBroker` places real orders via the exchange adapter, also keyed by
  client order id for idempotency across reconnects.

Paper fills are intentionally optimistic (a maker limit is assumed to fill at
its limit price). The honest, zero-look-ahead model lives in the backtester
(later milestone); paper mode is for wiring and smoke-testing the pipeline.
"""

from __future__ import annotations

import logging
from typing import Protocol

from ..config import FeeConfig
from ..domain import OrderType, Side
from .models import ExecStatus, ExecutionResult, Fill, LiquidityRole, Order, utcnow

logger = logging.getLogger(__name__)


class Broker(Protocol):
    async def place_order(self, order: Order) -> ExecutionResult: ...
    async def cancel_order(self, client_order_id: str, symbol: str) -> bool: ...


class PaperBroker:
    """Local fill simulator. No network."""

    def __init__(self, fees: FeeConfig) -> None:
        self._fees = fees
        self._seen: dict[str, ExecutionResult] = {}

    def _fee_pct(self, role: LiquidityRole) -> float:
        return self._fees.maker_fee_pct if role is LiquidityRole.MAKER else self._fees.taker_fee_pct

    async def place_order(self, order: Order) -> ExecutionResult:
        # idempotency: a re-submit of the same client order id never double-fills
        if order.client_order_id in self._seen:
            logger.debug("PaperBroker: duplicate client_order_id %s ignored", order.client_order_id)
            return self._seen[order.client_order_id]

        fill_price = order.price if order.price is not None else order.reference_price
        cost = order.amount * fill_price
        fee = cost * self._fee_pct(order.role) / 100.0
        fill = Fill(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            price=fill_price,
            amount=order.amount,
            fee_quote=fee,
            role=order.role,
            cost_quote=cost,
            timestamp=utcnow(),
        )
        result = ExecutionResult(status=ExecStatus.FILLED, order=order, fill=fill)
        self._seen[order.client_order_id] = result
        logger.info(
            "PAPER FILL %s %s %.8f @ %.4f (fee %.4f, %s)",
            order.side.value, order.symbol, order.amount, fill_price, fee, order.role.value,
        )
        return result

    async def cancel_order(self, client_order_id: str, symbol: str) -> bool:
        return True


class LiveBroker:
    """There is no certified Bybit EU live adapter in this build.

    The durable venue protocol is tested through JournalBroker with fake venues.
    Do not restore the old acknowledgement-based live path.
    """

    def __init__(self, adapter) -> None:
        raise RuntimeError('Live execution blocked: no certified durable venue/protection adapter')

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
    """Places real orders via the exchange adapter. Not exercised in dry-run."""

    def __init__(self, adapter) -> None:  # adapter: ExchangeAdapter
        self._adapter = adapter
        self._submitted: set[str] = set()

    async def place_order(self, order: Order) -> ExecutionResult:
        if order.client_order_id in self._submitted:
            logger.warning("LiveBroker: %s already submitted; skipping", order.client_order_id)
            return ExecutionResult(ExecStatus.SKIPPED, order, reason="duplicate client_order_id")

        ccxt_type = "market" if order.order_type is OrderType.MARKET else "limit"
        raw = await self._adapter.create_order(
            symbol=order.symbol,
            order_type=ccxt_type,
            side=order.side.value,
            amount=order.amount,
            price=order.price,
            client_order_id=order.client_order_id,
        )
        self._submitted.add(order.client_order_id)

        status = ExecStatus.OPEN
        fill = None
        if (raw.get("status") == "closed") or raw.get("filled"):
            avg = raw.get("average") or raw.get("price") or order.price or order.reference_price
            amount = raw.get("filled") or order.amount
            fee_info = raw.get("fee") or {}
            fill = Fill(
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                price=float(avg),
                amount=float(amount),
                fee_quote=float(fee_info.get("cost") or 0.0),
                role=order.role,
                cost_quote=float(avg) * float(amount),
                timestamp=utcnow(),
            )
            status = ExecStatus.FILLED
        return ExecutionResult(status=status, order=order, fill=fill,
                               exchange_order_id=str(raw.get("id")) if raw.get("id") else None)

    async def cancel_order(self, client_order_id: str, symbol: str) -> bool:
        try:
            await self._adapter.cancel_order(client_order_id, symbol)
            return True
        except Exception:
            logger.exception("LiveBroker: cancel failed for %s", client_order_id)
            return False

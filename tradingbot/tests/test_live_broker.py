"""LiveBroker fill parsing — especially fees charged in the BASE coin (Bybit
spot deducts BUY fees from the received asset; tracking gross units would make
the eventual exit oversell and get rejected)."""

from __future__ import annotations

import pytest

from tradingbot.domain import OrderType, Side
from tradingbot.execution.broker import LiveBroker
from tradingbot.execution.models import ExecStatus, LiquidityRole, Order


class FakeAdapter:
    def __init__(self, raw: dict) -> None:
        self.raw = raw
        self.calls: list[dict] = []

    async def create_order(self, **kw):
        self.calls.append(kw)
        return self.raw


def order(side: Side = Side.BUY, cid: str = "tb-test1") -> Order:
    return Order(client_order_id=cid, symbol="ETH/USDT", side=side, amount=0.023,
                 order_type=OrderType.MARKET, role=LiquidityRole.TAKER,
                 price=2000.0, reference_price=2000.0)


async def test_buy_fee_in_base_reduces_units() -> None:
    raw = {"id": "1", "status": "closed", "filled": 0.023, "average": 2000.0,
           "fee": {"cost": 0.000023, "currency": "ETH"}}
    result = await LiveBroker(FakeAdapter(raw)).place_order(order())
    assert result.status is ExecStatus.FILLED
    assert result.fill.amount == pytest.approx(0.023 - 0.000023)  # NET units
    assert result.fill.fee_quote == pytest.approx(0.000023 * 2000.0)  # quote terms


async def test_buy_fee_in_quote_keeps_units() -> None:
    raw = {"id": "1", "status": "closed", "filled": 0.023, "average": 2000.0,
           "fee": {"cost": 0.046, "currency": "USDT"}}
    result = await LiveBroker(FakeAdapter(raw)).place_order(order())
    assert result.fill.amount == pytest.approx(0.023)
    assert result.fill.fee_quote == pytest.approx(0.046)


async def test_sell_fee_never_reduces_units() -> None:
    raw = {"id": "1", "status": "closed", "filled": 0.023, "average": 2000.0,
           "fee": {"cost": 0.046, "currency": "USDT"}}
    result = await LiveBroker(FakeAdapter(raw)).place_order(order(side=Side.SELL))
    assert result.fill.amount == pytest.approx(0.023)


async def test_resting_order_returns_open_no_fill() -> None:
    raw = {"id": "1", "status": "open", "filled": 0.0}
    result = await LiveBroker(FakeAdapter(raw)).place_order(order())
    assert result.status is ExecStatus.OPEN
    assert result.fill is None

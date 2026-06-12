"""Execution-layer tests: maker-first pricing, slippage guard, paper fills,
idempotency, and the live broker (mocked)."""

from __future__ import annotations

import pytest

from tradingbot.config import Config
from tradingbot.domain import OrderIntent, OrderType, Side
from tradingbot.exchange.adapter import ExchangeAdapter
from tradingbot.exchange.models import Ticker
from tradingbot.execution.broker import LiveBroker, PaperBroker
from tradingbot.execution.executor import Executor
from tradingbot.execution.models import ExecStatus, LiquidityRole, Order
from tradingbot.risk.engine import RiskDecision, RiskGate


def cfg(**fees) -> Config:
    base = {"fees": {"maker_offset_pct": 0.05, "max_slippage_pct": 0.20}}
    base["fees"].update(fees)
    return Config(**base)


def ticker(bid=100.0, ask=100.2) -> Ticker:
    return Ticker("BTC/USD", bid=bid, ask=ask, last=(bid + ask) / 2,
                  base_volume=1, quote_volume=1, timestamp=0)


def intent(side=Side.BUY, order_type=OrderType.LIMIT, price=100.1) -> OrderIntent:
    return OrderIntent(symbol="BTC/USD", side=side, amount=0.4,
                       order_type=order_type, price=price)


def decision(est_price=100.1) -> RiskDecision:
    return RiskDecision(approved=True, gate=RiskGate.OK, reason="ok",
                        est_price=est_price, notional=0.4 * est_price)


# --- maker-first pricing ---------------------------------------------------
async def test_maker_buy_rests_below_bid() -> None:
    ex = Executor(cfg(maker_first=True), PaperBroker(cfg().fees))
    res = await ex.execute(intent(side=Side.BUY), decision(), ticker())
    assert res.filled
    assert res.order.role is LiquidityRole.MAKER
    assert res.order.price == pytest.approx(100.0 * (1 - 0.0005))  # bid * (1-offset)


async def test_maker_sell_rests_above_ask() -> None:
    ex = Executor(cfg(maker_first=True), PaperBroker(cfg().fees))
    res = await ex.execute(intent(side=Side.SELL), decision(), ticker())
    assert res.order.role is LiquidityRole.MAKER
    assert res.order.price == pytest.approx(100.2 * (1 + 0.0005))  # ask * (1+offset)


# --- taker policy ----------------------------------------------------------
async def test_taker_rejected_when_not_allowed() -> None:
    ex = Executor(cfg(maker_first=False, allow_taker_fallback=False), PaperBroker(cfg().fees))
    res = await ex.execute(intent(), decision(), ticker())
    assert res.status is ExecStatus.REJECTED
    assert "taker" in res.reason


async def test_market_order_rejected_when_taker_disallowed() -> None:
    ex = Executor(cfg(maker_first=True, allow_taker_fallback=False), PaperBroker(cfg().fees))
    res = await ex.execute(intent(order_type=OrderType.MARKET), decision(), ticker())
    assert res.status is ExecStatus.REJECTED


async def test_taker_allowed_crosses_spread() -> None:
    c = cfg(maker_first=False, allow_taker_fallback=True)
    ex = Executor(c, PaperBroker(c.fees))
    res = await ex.execute(intent(side=Side.BUY), decision(), ticker())
    assert res.order.role is LiquidityRole.TAKER
    assert res.order.price == pytest.approx(100.2)  # best ask


# --- slippage guard --------------------------------------------------------
async def test_slippage_guard_skips_when_market_moved() -> None:
    c = cfg(maker_first=True, max_slippage_pct=0.20)
    ex = Executor(c, PaperBroker(c.fees))
    # decision priced at 110 but market maker price ~100 -> ~9% deviation
    res = await ex.execute(intent(side=Side.BUY), decision(est_price=110.0), ticker())
    assert res.status is ExecStatus.SKIPPED
    assert "slippage" in res.reason


# --- paper fills + fees ----------------------------------------------------
async def test_paper_fill_applies_maker_fee() -> None:
    c = cfg(maker_first=True, maker_fee_pct=0.40)
    ex = Executor(c, PaperBroker(c.fees))
    res = await ex.execute(intent(side=Side.BUY), decision(), ticker())
    fill = res.fill
    assert fill is not None
    expected_price = 100.0 * (1 - 0.0005)
    assert fill.price == pytest.approx(expected_price)
    assert fill.cost_quote == pytest.approx(0.4 * expected_price)
    assert fill.fee_quote == pytest.approx(0.4 * expected_price * 0.004)
    assert fill.role is LiquidityRole.MAKER


async def test_paper_broker_idempotent_on_client_order_id() -> None:
    broker = PaperBroker(cfg().fees)
    order = Order(
        client_order_id="dup-1", symbol="BTC/USD", side=Side.BUY, amount=0.4,
        order_type=OrderType.LIMIT, role=LiquidityRole.MAKER, price=100.0,
        reference_price=100.0,
    )
    r1 = await broker.place_order(order)
    r2 = await broker.place_order(order)
    assert r1 is r2  # same result; never double-filled


# --- live broker (mocked) --------------------------------------------------
async def test_live_broker_places_and_fills(fake_client) -> None:
    broker = LiveBroker(ExchangeAdapter(fake_client))
    order = Order(
        client_order_id="cid-9", symbol="BTC/USD", side=Side.BUY, amount=0.4,
        order_type=OrderType.LIMIT, role=LiquidityRole.MAKER, price=100.0,
        reference_price=100.0,
    )
    res = await broker.place_order(order)
    assert res.status is ExecStatus.FILLED
    assert res.exchange_order_id == "EX-1"
    assert res.fill.price == pytest.approx(100.0)
    # idempotency guard: a second submit is skipped
    res2 = await broker.place_order(order)
    assert res2.status is ExecStatus.SKIPPED

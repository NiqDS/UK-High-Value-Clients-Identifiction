"""Approval manager (approve / reject / timeout) + message/callback helpers."""

from __future__ import annotations

import asyncio

import pytest

from tradingbot.config import Config
from tradingbot.domain import OrderIntent, Side
from tradingbot.approval.manager import ApprovalManager
from tradingbot.approval.messages import (
    callback_data,
    parse_callback,
    render_approval_message,
)
from tradingbot.risk.engine import RiskDecision, RiskGate


class FakeNotifier:
    def __init__(self) -> None:
        self.requests = []
        self.messages = []

    async def send_approval_request(self, req) -> None:
        self.requests.append(req)

    async def send_message(self, text) -> None:
        self.messages.append(text)


def intent() -> OrderIntent:
    return OrderIntent(symbol="BTC/USD", side=Side.BUY, amount=0.4, price=100.0,
                       take_profit_price=102.0)


def decision() -> RiskDecision:
    return RiskDecision(approved=True, gate=RiskGate.OK, reason="ok", requires_approval=True,
                        est_price=100.0, notional=40.0, projected_free_quote=2960.0,
                        round_trip_fee_quote=0.32, net_after_fees_quote=0.48)


async def test_approve_returns_true() -> None:
    n = FakeNotifier()
    mgr = ApprovalManager(n, timeout_seconds=5)
    task = asyncio.create_task(mgr.request(intent(), decision()))
    await asyncio.sleep(0.02)  # let it register + notify
    req = n.requests[0]
    assert mgr.resolve(req.id, True) is True
    assert await task is True
    assert mgr.pending == []  # cleaned up


async def test_reject_returns_false() -> None:
    n = FakeNotifier()
    mgr = ApprovalManager(n, timeout_seconds=5)
    task = asyncio.create_task(mgr.request(intent(), decision()))
    await asyncio.sleep(0.02)
    assert mgr.resolve(n.requests[0].id, False) is True
    assert await task is False


async def test_timeout_auto_rejects() -> None:
    n = FakeNotifier()
    mgr = ApprovalManager(n, timeout_seconds=0)  # immediate timeout
    result = await mgr.request(intent(), decision())
    assert result is False
    assert any("timed out" in m for m in n.messages)


async def test_resolve_unknown_id_is_false() -> None:
    mgr = ApprovalManager(FakeNotifier(), timeout_seconds=5)
    assert mgr.resolve("nope", True) is False


async def test_double_resolve_is_false() -> None:
    n = FakeNotifier()
    mgr = ApprovalManager(n, timeout_seconds=5)
    task = asyncio.create_task(mgr.request(intent(), decision()))
    await asyncio.sleep(0.02)
    rid = n.requests[0].id
    assert mgr.resolve(rid, True) is True
    assert mgr.resolve(rid, True) is False  # already resolved
    await task


# --- pure helpers ----------------------------------------------------------
def test_callback_roundtrip() -> None:
    assert parse_callback(callback_data("approve", "abc123")) == ("approve", "abc123")
    assert parse_callback(callback_data("reject", "x")) == ("reject", "x")


def test_callback_rejects_malformed() -> None:
    assert parse_callback("garbage") is None
    assert parse_callback("approve:") is None
    assert parse_callback("frobnicate:abc") is None


def test_render_approval_message_includes_key_fields() -> None:
    msg = render_approval_message(Config(), intent(), decision())
    assert "BUY BTC/USD" in msg
    assert "notional" in msg
    assert "round-trip fees" in msg
    assert "floor" in msg


def test_render_flags_below_floor() -> None:
    d = decision()
    d.projected_free_quote = 10.0  # below default floor 1000
    msg = render_approval_message(Config(), intent(), d)
    assert "BELOW FLOOR" in msg

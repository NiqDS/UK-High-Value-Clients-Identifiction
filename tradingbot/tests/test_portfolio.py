"""Position tracker: open/close, realised P&L, holding flag, averaging."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingbot.app.portfolio import PositionTracker
from tradingbot.domain import Side

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def test_buy_opens_position() -> None:
    pt = PositionTracker()
    realized = pt.on_fill("BTC/USD", Side.BUY, 100.0, 0.4, NOW, take_profit=102.0, stop=99.0)
    assert realized == 0.0
    assert pt.holding("BTC/USD") is True
    pos = pt.get("BTC/USD")
    assert pos.units == 0.4 and pos.entry_price == 100.0
    assert pos.take_profit == 102.0 and pos.stop == 99.0


def test_sell_closes_and_realizes_pnl() -> None:
    pt = PositionTracker()
    pt.on_fill("BTC/USD", Side.BUY, 100.0, 0.4, NOW)
    realized = pt.on_fill("BTC/USD", Side.SELL, 105.0, 0.4, NOW)
    assert realized == pytest.approx((105 - 100) * 0.4)
    assert pt.holding("BTC/USD") is False


def test_partial_close() -> None:
    pt = PositionTracker()
    pt.on_fill("BTC/USD", Side.BUY, 100.0, 1.0, NOW)
    realized = pt.on_fill("BTC/USD", Side.SELL, 110.0, 0.4, NOW)
    assert realized == pytest.approx(10 * 0.4)
    assert pt.holding("BTC/USD") is True
    assert pt.get("BTC/USD").units == pytest.approx(0.6)


def test_average_up() -> None:
    pt = PositionTracker()
    pt.on_fill("BTC/USD", Side.BUY, 100.0, 1.0, NOW)
    pt.on_fill("BTC/USD", Side.BUY, 120.0, 1.0, NOW)
    assert pt.get("BTC/USD").entry_price == pytest.approx(110.0)
    assert pt.get("BTC/USD").units == pytest.approx(2.0)


def test_sell_without_position_is_noop() -> None:
    pt = PositionTracker()
    assert pt.on_fill("BTC/USD", Side.SELL, 100.0, 0.4, NOW) == 0.0

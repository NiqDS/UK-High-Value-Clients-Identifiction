"""JsonPositionStore: positions must survive restarts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingbot.app.portfolio import JsonPositionStore, PositionTracker
from tradingbot.domain import Side

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def test_roundtrip(tmp_path) -> None:
    store = JsonPositionStore(tmp_path / "positions.json")
    tracker = PositionTracker()
    tracker.on_fill("BTC/USDT", Side.BUY, 100.0, 0.4, NOW, take_profit=120.0,
                    stop=90.0, trail_distance=5.0)
    tracker.on_fill("ETH/USDT", Side.BUY, 2000.0, 0.01, NOW)
    store.save(tracker)

    loaded = store.load()
    assert set(loaded.positions) == {"BTC/USDT", "ETH/USDT"}
    pos = loaded.get("BTC/USDT")
    assert pos.units == pytest.approx(0.4)
    assert pos.entry_price == pytest.approx(100.0)
    assert pos.stop == pytest.approx(90.0)
    assert pos.take_profit == pytest.approx(120.0)
    assert pos.trail_distance == pytest.approx(5.0)
    assert pos.entry_ts == NOW


def test_missing_file_loads_empty(tmp_path) -> None:
    store = JsonPositionStore(tmp_path / "nope.json")
    assert store.load().positions == {}


def test_corrupt_file_loads_empty_not_crash(tmp_path) -> None:
    path = tmp_path / "positions.json"
    path.write_text("{not json")
    assert JsonPositionStore(path).load().positions == {}


def test_closed_position_not_persisted(tmp_path) -> None:
    store = JsonPositionStore(tmp_path / "positions.json")
    tracker = PositionTracker()
    tracker.on_fill("BTC/USDT", Side.BUY, 100.0, 0.4, NOW)
    tracker.on_fill("BTC/USDT", Side.SELL, 110.0, 0.4, NOW)  # closed
    store.save(tracker)
    assert store.load().positions == {}

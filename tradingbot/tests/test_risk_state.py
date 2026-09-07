"""Tests for persistent risk state: trading flag + rolling daily counters."""

from __future__ import annotations

from datetime import datetime, timezone

from tradingbot.risk.state import InMemoryRiskStateStore, trading_day


def _dt(y, m, d, h=12, mi=0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_trading_day_default_reset_is_utc_date() -> None:
    assert trading_day(_dt(2026, 1, 2, 0, 30), reset_utc_hour=0) == _dt(2026, 1, 2).date()


def test_trading_day_with_reset_hour_shifts_boundary() -> None:
    # 00:30 UTC with a 01:00 reset boundary still belongs to the previous day
    assert trading_day(_dt(2026, 1, 2, 0, 30), reset_utc_hour=1) == _dt(2026, 1, 1).date()
    assert trading_day(_dt(2026, 1, 2, 1, 30), reset_utc_hour=1) == _dt(2026, 1, 2).date()


def test_naive_datetime_treated_as_utc() -> None:
    naive = datetime(2026, 1, 2, 0, 30)
    assert trading_day(naive, reset_utc_hour=0) == datetime(2026, 1, 2).date()


def test_trading_enabled_toggle_and_reason() -> None:
    store = InMemoryRiskStateStore()
    assert store.is_trading_enabled() is True
    store.set_trading_enabled(False, "floor breach")
    assert store.is_trading_enabled() is False
    assert store.disabled_reason() == "floor breach"
    store.set_trading_enabled(True)
    assert store.is_trading_enabled() is True
    assert store.disabled_reason() == ""


def test_counters_accumulate_within_day() -> None:
    store = InMemoryRiskStateStore()
    now = _dt(2026, 1, 2)
    store.record_trade(now, 25.0)
    store.record_trade(now, 30.0)
    store.record_realized_pnl(now, -5.0)
    c = store.get_daily_counters(now)
    assert c.trades == 2
    assert c.traded_notional == 55.0
    assert c.realized_pnl == -5.0


def test_counters_reset_on_new_day() -> None:
    store = InMemoryRiskStateStore()
    store.record_trade(_dt(2026, 1, 2), 25.0)
    store.record_realized_pnl(_dt(2026, 1, 2), -5.0)
    c = store.get_daily_counters(_dt(2026, 1, 3))  # next day
    assert c.trades == 0
    assert c.traded_notional == 0.0
    assert c.realized_pnl == 0.0

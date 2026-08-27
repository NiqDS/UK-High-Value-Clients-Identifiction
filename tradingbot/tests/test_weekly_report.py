"""Weekly performance report + trade-only-key self-check."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingbot.app.self_check import trade_only_key_self_check
from tradingbot.reporting.weekly import WeeklyReporter
from tradingbot.store import TradeLog, make_session_factory

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def _seed_log(tmp_path) -> TradeLog:
    log = TradeLog(make_session_factory(f"sqlite:///{tmp_path / 'tb.db'}"))
    log.record(ts=NOW, symbol="BTC/USD", side="buy", price=100.0, amount=0.4,
               cost_quote=40.0, fee_quote=0.16, role="maker", is_entry=True)
    log.record(ts=NOW, symbol="BTC/USD", side="sell", price=110.0, amount=0.4,
               cost_quote=44.0, fee_quote=0.18, role="maker", is_entry=False, realized_pnl=4.0)
    log.record(ts=NOW, symbol="ETH/USD", side="buy", price=50.0, amount=1.0,
               cost_quote=50.0, fee_quote=0.20, role="maker", is_entry=True)
    log.record(ts=NOW, symbol="ETH/USD", side="sell", price=48.0, amount=1.0,
               cost_quote=48.0, fee_quote=0.19, role="maker", is_entry=False, realized_pnl=-2.0)
    return log


def test_weekly_stats(tmp_path) -> None:
    reporter = WeeklyReporter(_seed_log(tmp_path))
    stats = reporter.compute(NOW.replace(day=5), NOW)
    assert stats.num_fills == 4
    assert stats.num_exits == 2
    assert stats.realized_pnl == pytest.approx(2.0)  # +4 - 2
    assert stats.fees_paid == pytest.approx(0.16 + 0.18 + 0.20 + 0.19)
    assert stats.win_rate_pct == pytest.approx(50.0)  # one winning exit of two
    assert stats.per_symbol_net["BTC/USD"] == pytest.approx(4.0 - 0.16 - 0.18)


def test_weekly_render_includes_market_benchmark(tmp_path) -> None:
    reporter = WeeklyReporter(_seed_log(tmp_path))
    stats = reporter.compute(NOW.replace(day=5), NOW, market_returns_pct={"BTC/USD": 8.0})
    text = reporter.render(stats)
    assert "Weekly performance report" in text
    assert "NET P&L" in text
    assert "vs market +8.00%" in text


# --- trade-only-key self-check --------------------------------------------
class _Adapter:
    def __init__(self, client) -> None:
        self._client = client


class _NoPerms:
    pass


class _WithPerms:
    def __init__(self, payload) -> None:
        self._payload = payload

    async def fetch_permissions(self):
        return self._payload


async def test_self_check_passes_when_no_permission_api() -> None:
    assert await trade_only_key_self_check(_Adapter(_NoPerms())) is True


async def test_self_check_passes_trade_only_key() -> None:
    adapter = _Adapter(_WithPerms({"trade": True, "withdraw": False}))
    assert await trade_only_key_self_check(adapter) is True


async def test_self_check_fails_on_withdraw_permission() -> None:
    adapter = _Adapter(_WithPerms({"withdraw": "enabled"}))
    assert await trade_only_key_self_check(adapter) is False

"""Persistent risk state: the trading-enabled flag and rolling daily counters.

Step 2 ships an in-memory implementation so the risk engine is fully testable.
A SQLite/SQLAlchemy-backed implementation of the same :class:`RiskStateStore`
protocol lands with the ``store/`` milestone — the engine depends only on the
protocol, so swapping it in requires no engine changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Protocol


def trading_day(now: datetime, reset_utc_hour: int) -> date:
    """The logical trading day for ``now``, given a daily reset hour (UTC).

    With ``reset_utc_hour=0`` this is simply the UTC calendar date.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    return (now - timedelta(hours=reset_utc_hour)).date()


@dataclass
class DailyCounters:
    day: date
    trades: int = 0
    traded_notional: float = 0.0
    realized_pnl: float = 0.0


class RiskStateStore(Protocol):
    """State the risk engine reads/writes. Implementations must persist."""

    def is_trading_enabled(self) -> bool: ...
    def set_trading_enabled(self, enabled: bool, reason: str = "") -> None: ...
    def disabled_reason(self) -> str: ...
    def get_daily_counters(self, now: datetime) -> DailyCounters: ...
    def record_trade(self, now: datetime, notional: float) -> None: ...
    def record_realized_pnl(self, now: datetime, delta: float) -> None: ...


@dataclass
class InMemoryRiskStateStore:
    """Non-persistent reference implementation for tests and dry runs."""

    daily_reset_utc_hour: int = 0
    _trading_enabled: bool = True
    _disabled_reason: str = ""
    _counters: DailyCounters | None = field(default=None)

    def is_trading_enabled(self) -> bool:
        return self._trading_enabled

    def set_trading_enabled(self, enabled: bool, reason: str = "") -> None:
        self._trading_enabled = enabled
        self._disabled_reason = "" if enabled else reason

    def disabled_reason(self) -> str:
        return self._disabled_reason

    def _roll(self, now: datetime) -> DailyCounters:
        day = trading_day(now, self.daily_reset_utc_hour)
        if self._counters is None or self._counters.day != day:
            self._counters = DailyCounters(day=day)
        return self._counters

    def get_daily_counters(self, now: datetime) -> DailyCounters:
        return self._roll(now)

    def record_trade(self, now: datetime, notional: float) -> None:
        c = self._roll(now)
        c.trades += 1
        c.traded_notional += notional

    def record_realized_pnl(self, now: datetime, delta: float) -> None:
        c = self._roll(now)
        c.realized_pnl += delta

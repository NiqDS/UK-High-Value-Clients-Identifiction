"""SQLite-backed RiskStateStore: persistent trading-enabled flag + daily counters.

Implements the same protocol as the in-memory store, so the risk engine and the
rest of the bot use it unchanged — but the trading-enabled flag and daily
counters now survive a restart (so a floor halt or manual pause is not silently
forgotten).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from ..risk.state import DailyCounters, trading_day
from .models import DailyCounterRow, KeyValue

_TRADING_ENABLED = "trading_enabled"
_DISABLED_REASON = "disabled_reason"


class SqliteRiskStateStore:
    def __init__(self, session_factory: sessionmaker[Session], daily_reset_utc_hour: int = 0) -> None:
        self._sf = session_factory
        self.daily_reset_utc_hour = daily_reset_utc_hour

    # -- kv helpers ---------------------------------------------------------
    def _get(self, key: str, default: str | None = None) -> str | None:
        with self._sf() as s:
            row = s.get(KeyValue, key)
            return row.value if row else default

    def _set(self, key: str, value: str) -> None:
        with self._sf() as s:
            row = s.get(KeyValue, key)
            if row is None:
                s.add(KeyValue(key=key, value=value))
            else:
                row.value = value
            s.commit()

    # -- trading flag -------------------------------------------------------
    def is_trading_enabled(self) -> bool:
        return self._get(_TRADING_ENABLED, "1") != "0"

    def set_trading_enabled(self, enabled: bool, reason: str = "") -> None:
        self._set(_TRADING_ENABLED, "1" if enabled else "0")
        self._set(_DISABLED_REASON, "" if enabled else reason)

    def disabled_reason(self) -> str:
        return self._get(_DISABLED_REASON, "") or ""

    # -- daily counters -----------------------------------------------------
    def get_daily_counters(self, now: datetime) -> DailyCounters:
        day = trading_day(now, self.daily_reset_utc_hour)
        with self._sf() as s:
            row = s.get(DailyCounterRow, day.isoformat())
            if row is None:
                return DailyCounters(day=day)
            return DailyCounters(day=day, trades=row.trades,
                                 traded_notional=row.traded_notional, realized_pnl=row.realized_pnl)

    def _row(self, s: Session, now: datetime) -> DailyCounterRow:
        key = trading_day(now, self.daily_reset_utc_hour).isoformat()
        row = s.get(DailyCounterRow, key)
        if row is None:
            row = DailyCounterRow(day=key, trades=0, traded_notional=0.0, realized_pnl=0.0)
            s.add(row)
        return row

    def record_trade(self, now: datetime, notional: float) -> None:
        with self._sf() as s:
            row = self._row(s, now)
            row.trades += 1
            row.traded_notional += notional
            s.commit()

    def record_realized_pnl(self, now: datetime, delta: float) -> None:
        with self._sf() as s:
            row = self._row(s, now)
            row.realized_pnl += delta
            s.commit()

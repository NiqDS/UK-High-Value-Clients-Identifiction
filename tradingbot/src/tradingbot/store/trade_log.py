"""Persistent trade log — every executed fill, queryable by time window."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .models import TradeRecord


class TradeLog:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def record(
        self, *, ts: datetime, symbol: str, side: str, price: float, amount: float,
        cost_quote: float, fee_quote: float, role: str, is_entry: bool,
        realized_pnl: float = 0.0, reason: str = "", valuation_pct: float | None = None,
        client_order_id: str | None = None,
    ) -> int:
        rec = TradeRecord(
            ts=ts, symbol=symbol, side=side, price=price, amount=amount,
            cost_quote=cost_quote, fee_quote=fee_quote, role=role, is_entry=is_entry,
            realized_pnl=realized_pnl, reason=reason, valuation_pct=valuation_pct,
            client_order_id=client_order_id,
        )
        with self._sf() as s:
            s.add(rec)
            s.commit()
            return rec.id

    def between(self, start: datetime, end: datetime) -> list[TradeRecord]:
        with self._sf() as s:
            stmt = (
                select(TradeRecord)
                .where(TradeRecord.ts >= start, TradeRecord.ts <= end)
                .order_by(TradeRecord.ts)
            )
            return list(s.scalars(stmt))

    def all(self) -> list[TradeRecord]:
        with self._sf() as s:
            return list(s.scalars(select(TradeRecord).order_by(TradeRecord.ts)))

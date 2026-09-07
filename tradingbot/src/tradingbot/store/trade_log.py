"""Persistent trade log (executed fills) + decision log (every decision)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .models import DecisionRecord, TradeRecord


class TradeLog:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def record(
        self, *, ts: datetime, symbol: str, side: str, price: float, amount: float,
        cost_quote: float, fee_quote: float, role: str, is_entry: bool,
        realized_pnl: float = 0.0, reason: str = "", valuation_pct: float | None = None,
        client_order_id: str | None = None, risk_pct: float | None = None,
    ) -> int:
        rec = TradeRecord(
            ts=ts, symbol=symbol, side=side, price=price, amount=amount,
            cost_quote=cost_quote, fee_quote=fee_quote, role=role, is_entry=is_entry,
            realized_pnl=realized_pnl, reason=reason, valuation_pct=valuation_pct,
            client_order_id=client_order_id, risk_pct=risk_pct,
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


class DecisionLog:
    """Every decision the algo made (executed or not) — the RL / analysis feed."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def record(
        self, *, ts: datetime, symbol: str, side: str, is_entry: bool, outcome: str,
        gate: str = "", approved: bool = False, notional: float | None = None,
        est_price: float | None = None, risk_pct: float | None = None,
        stop_distance_pct: float | None = None, reason: str = "", source: str = "live",
    ) -> int:
        rec = DecisionRecord(
            ts=ts, symbol=symbol, side=side, is_entry=is_entry, outcome=outcome,
            gate=gate, approved=approved, notional=notional, est_price=est_price,
            risk_pct=risk_pct, stop_distance_pct=stop_distance_pct, reason=reason,
            source=source,
        )
        with self._sf() as s:
            s.add(rec)
            s.commit()
            return rec.id

    def between(self, start: datetime, end: datetime) -> list[DecisionRecord]:
        with self._sf() as s:
            stmt = (select(DecisionRecord)
                    .where(DecisionRecord.ts >= start, DecisionRecord.ts <= end)
                    .order_by(DecisionRecord.ts))
            return list(s.scalars(stmt))

    def all(self) -> list[DecisionRecord]:
        with self._sf() as s:
            return list(s.scalars(select(DecisionRecord).order_by(DecisionRecord.ts)))

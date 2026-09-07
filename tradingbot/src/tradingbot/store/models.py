"""SQLAlchemy models for the persistent store (trade log, counters, flags)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TradeRecord(Base):
    """One executed fill (entry or exit). The weekly report reads from here."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    price: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    cost_quote: Mapped[float] = mapped_column(Float)
    fee_quote: Mapped[float] = mapped_column(Float)
    role: Mapped[str] = mapped_column(String(8))
    is_entry: Mapped[bool] = mapped_column(Boolean, default=True)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(String(120), default="")
    valuation_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # % of equity risked to the stop at entry (nullable; exits/no-stop have none)
    risk_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


class DecisionRecord(Base):
    """EVERY decision the algo made — executed OR rejected/paused/held. This is
    the full training substrate: what the algo wanted to do, the risk it carried,
    and what happened. The weekly RL loop and the "which risk levels lose most"
    analysis read from here; TradeRecord stays the ground truth for fills/PnL."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    is_entry: Mapped[bool] = mapped_column(Boolean, default=True)
    outcome: Mapped[str] = mapped_column(String(24), index=True)  # Outcome enum value
    gate: Mapped[str] = mapped_column(String(24), default="")     # RiskGate value
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    notional: Mapped[float | None] = mapped_column(Float, nullable=True)
    est_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_pct: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    stop_distance_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String(160), default="")
    # optional label for the environment/source (e.g. our bot vs an external one
    # replayed through the same pipeline for comparison)
    source: Mapped[str] = mapped_column(String(32), default="live", index=True)


class KeyValue(Base):
    """Small persistent flags, e.g. the trading-enabled flag + reason."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256))


class DailyCounterRow(Base):
    __tablename__ = "daily_counters"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # ISO date
    trades: Mapped[int] = mapped_column(Integer, default=0)
    traded_notional: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)

"""Engine / session helpers for the SQLite store."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def make_session_factory(db_url: str = "sqlite:///tradingbot.db") -> sessionmaker[Session]:
    """Create the engine, create tables if needed, and return a session factory."""
    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)

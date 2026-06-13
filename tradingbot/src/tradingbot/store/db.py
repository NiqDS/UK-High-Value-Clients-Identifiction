"""Engine / session helpers for the SQLite store."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def make_session_factory(db_url: str = "sqlite:///data/tradingbot.db") -> sessionmaker[Session]:
    """Create the engine, create tables if needed, and return a session factory.

    For a file-based SQLite URL the parent directory is created if missing (so a
    mounted ``data/`` volume or a fresh checkout works without manual mkdir)."""
    if db_url.startswith("sqlite:///") and ":memory:" not in db_url:
        path = Path(db_url.replace("sqlite:///", "", 1))
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)

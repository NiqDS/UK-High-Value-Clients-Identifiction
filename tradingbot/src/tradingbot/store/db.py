"""Engine / session helpers for the SQLite store."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

logger = logging.getLogger(__name__)

# Columns added to existing tables after their first release. create_all() only
# creates MISSING tables, never alters existing ones, so a db from an earlier
# version needs these back-filled (all nullable => a plain ADD COLUMN is safe).
_ADDED_COLUMNS = {"trades": {"risk_pct": "FLOAT"}}


def _migrate(engine) -> None:
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    for table, cols in _ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue  # create_all made it fresh with every column
        have = {c["name"] for c in insp.get_columns(table)}
        with engine.begin() as conn:
            for name, sqltype in cols.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}"))
                    logger.info("migrated: added %s.%s", table, name)


def make_session_factory(db_url: str = "sqlite:///data/tradingbot.db") -> sessionmaker[Session]:
    """Create the engine, create/upgrade tables if needed, return a session factory.

    For a file-based SQLite URL the parent directory is created if missing (so a
    mounted ``data/`` volume or a fresh checkout works without manual mkdir)."""
    if db_url.startswith("sqlite:///") and ":memory:" not in db_url:
        path = Path(db_url.replace("sqlite:///", "", 1))
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(engine)  # new tables (e.g. decisions)
    _migrate(engine)                   # new columns on existing tables
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)

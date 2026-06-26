"""Async SQLite persistence layer for monitored items."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from config import DATABASE_PATH
from models import MonitoredItem

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS monitored_items (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id                INTEGER NOT NULL,
    url                    TEXT    NOT NULL,
    item_name              TEXT    NOT NULL,
    last_price             REAL,
    currency_symbol        TEXT    NOT NULL DEFAULT '',
    check_interval_minutes INTEGER NOT NULL,
    created_at             TEXT    NOT NULL,
    last_checked_at        TEXT,
    is_active              INTEGER NOT NULL DEFAULT 1
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_monitored_active
    ON monitored_items (is_active);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _row_to_item(row: aiosqlite.Row) -> MonitoredItem:
    return MonitoredItem(
        id=row["id"],
        chat_id=row["chat_id"],
        url=row["url"],
        item_name=row["item_name"],
        last_price=row["last_price"],
        currency_symbol=row["currency_symbol"] or "",
        check_interval_minutes=row["check_interval_minutes"],
        created_at=_parse_dt(row["created_at"]),
        last_checked_at=_parse_dt(row["last_checked_at"]),
        is_active=bool(row["is_active"]),
    )


class Database:
    """Thin async wrapper around an aiosqlite connection."""

    def __init__(self, path: str = DATABASE_PATH) -> None:
        self._path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(_CREATE_TABLE)
        await self._db.execute(_CREATE_INDEX)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        return self._db

    # --- Writes -------------------------------------------------------------

    async def add_item(
        self,
        *,
        chat_id: int,
        url: str,
        item_name: str,
        last_price: Optional[float],
        currency_symbol: str,
        check_interval_minutes: int,
    ) -> MonitoredItem:
        created_at = _now_iso()
        cursor = await self.conn.execute(
            """
            INSERT INTO monitored_items (
                chat_id, url, item_name, last_price, currency_symbol,
                check_interval_minutes, created_at, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                chat_id,
                url,
                item_name,
                last_price,
                currency_symbol,
                check_interval_minutes,
                created_at,
            ),
        )
        await self.conn.commit()
        item_id = cursor.lastrowid
        assert item_id is not None
        item = await self.get_item(item_id)
        assert item is not None
        return item

    async def update_price(
        self, item_id: int, new_price: float, checked_at: Optional[str] = None
    ) -> None:
        await self.conn.execute(
            "UPDATE monitored_items SET last_price = ?, last_checked_at = ? WHERE id = ?",
            (round(new_price, 2), checked_at or _now_iso(), item_id),
        )
        await self.conn.commit()

    async def touch_checked(self, item_id: int) -> None:
        """Record that we checked the item even if the price did not change."""
        await self.conn.execute(
            "UPDATE monitored_items SET last_checked_at = ? WHERE id = ?",
            (_now_iso(), item_id),
        )
        await self.conn.commit()

    async def set_interval_for_chat(self, chat_id: int, minutes: int) -> int:
        """Update the interval for all of a chat's items. Returns rows affected."""
        cursor = await self.conn.execute(
            "UPDATE monitored_items SET check_interval_minutes = ? WHERE chat_id = ? AND is_active = 1",
            (minutes, chat_id),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def deactivate_item(self, item_id: int) -> None:
        await self.conn.execute(
            "UPDATE monitored_items SET is_active = 0 WHERE id = ?", (item_id,)
        )
        await self.conn.commit()

    async def deactivate_all_for_chat(self, chat_id: int) -> int:
        cursor = await self.conn.execute(
            "UPDATE monitored_items SET is_active = 0 WHERE chat_id = ? AND is_active = 1",
            (chat_id,),
        )
        await self.conn.commit()
        return cursor.rowcount

    # --- Reads --------------------------------------------------------------

    async def get_item(self, item_id: int) -> Optional[MonitoredItem]:
        async with self.conn.execute(
            "SELECT * FROM monitored_items WHERE id = ?", (item_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_item(row) if row else None

    async def list_active_for_chat(self, chat_id: int) -> list[MonitoredItem]:
        async with self.conn.execute(
            "SELECT * FROM monitored_items WHERE chat_id = ? AND is_active = 1 ORDER BY id",
            (chat_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_item(r) for r in rows]

    async def list_all_active(self) -> list[MonitoredItem]:
        async with self.conn.execute(
            "SELECT * FROM monitored_items WHERE is_active = 1 ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_item(r) for r in rows]

    async def count_active_for_chat(self, chat_id: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) FROM monitored_items WHERE chat_id = ? AND is_active = 1",
            (chat_id,),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def count_all_active(self) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) FROM monitored_items WHERE is_active = 1"
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

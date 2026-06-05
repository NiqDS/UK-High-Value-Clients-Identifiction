import logging
from datetime import datetime
from pathlib import Path

import aiosqlite

from config.settings import settings

logger = logging.getLogger(__name__)

DB_PATH: Path = settings.DB_PATH


async def init_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                language    TEXT    NOT NULL DEFAULT 'en',
                team_name   TEXT,
                active      INTEGER NOT NULL DEFAULT 1,
                created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS news_cache (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name           TEXT    NOT NULL,
                original_lang       TEXT    NOT NULL,
                original_title      TEXT    NOT NULL,
                original_content    TEXT,
                translated_title    TEXT,
                translated_content  TEXT,
                target_lang         TEXT    NOT NULL,
                source_url          TEXT    NOT NULL,
                source_name         TEXT,
                published_at        TIMESTAMP,
                fetched_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_url, target_lang)
            );

            CREATE TABLE IF NOT EXISTS sent_news (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  INTEGER NOT NULL,
                news_id  INTEGER NOT NULL,
                sent_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, news_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                FOREIGN KEY (news_id) REFERENCES news_cache(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_news_team_lang
                ON news_cache(team_name, target_lang);
            CREATE INDEX IF NOT EXISTS idx_sent_user
                ON sent_news(user_id);
        """)
        await db.commit()

    logger.info("Database initialized at %s", DB_PATH)


# ──────────────────────────── Users ────────────────────────────

async def upsert_user(user_id: int, username: str, first_name: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = excluded.username,
                first_name = excluded.first_name,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, username, first_name))
        await db.commit()


async def get_user(user_id: int) -> aiosqlite.Row | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            return await cur.fetchone()


async def get_active_users() -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE active = 1 AND team_name IS NOT NULL"
        ) as cur:
            return await cur.fetchall()


async def set_user_language(user_id: int, language: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users
            SET language = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (language, user_id))
        await db.commit()


async def set_user_team(user_id: int, team_name: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users
            SET team_name = ?, active = 1, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (team_name, user_id))
        await db.commit()


async def set_user_active(user_id: int, active: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users
            SET active = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (1 if active else 0, user_id))
        await db.commit()


# ──────────────────────────── News cache ────────────────────────────

async def save_news_item(
    team_name: str,
    original_lang: str,
    original_title: str,
    original_content: str,
    translated_title: str,
    translated_content: str,
    target_lang: str,
    source_url: str,
    source_name: str,
    published_at: datetime,
) -> int | None:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("""
                INSERT OR IGNORE INTO news_cache (
                    team_name, original_lang, original_title, original_content,
                    translated_title, translated_content, target_lang,
                    source_url, source_name, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                team_name.lower(), original_lang, original_title, original_content,
                translated_title, translated_content, target_lang,
                source_url, source_name, published_at,
            ))
            await db.commit()
            return cur.lastrowid if cur.rowcount else None
    except Exception:
        logger.exception("Error saving news item (url=%s)", source_url)
        return None


async def get_unsent_news(
    user_id: int,
    team_name: str,
    target_lang: str,
    limit: int = 3,
) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT nc.*
            FROM   news_cache nc
            WHERE  nc.team_name  = ?
              AND  nc.target_lang = ?
              AND  nc.fetched_at  > datetime('now', '-7 days')
              AND  nc.id NOT IN (
                       SELECT news_id FROM sent_news WHERE user_id = ?
                   )
            ORDER BY nc.published_at DESC
            LIMIT ?
        """, (team_name.lower(), target_lang, user_id, limit)) as cur:
            return await cur.fetchall()


async def mark_news_sent(user_id: int, news_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO sent_news (user_id, news_id) VALUES (?, ?)",
            (user_id, news_id),
        )
        await db.commit()


# ──────────────────────────── Cleanup ────────────────────────────

async def delete_old_news(days: int = 7) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        # sent_news rows are removed via ON DELETE CASCADE
        cur = await db.execute("""
            DELETE FROM news_cache
            WHERE fetched_at < datetime('now', ? || ' days')
        """, (f"-{days}",))
        await db.commit()
        deleted = cur.rowcount
        logger.info("Cleanup: deleted %d news records older than %d days", deleted, days)
        return deleted

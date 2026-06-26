"""APScheduler job management for periodic price checks.

Each monitored item gets its own interval job. On each tick we fetch the URL,
extract the price, compare to the last known price, and on a change we send an
alert and update the DB. Failures are retried once, then counted; after a
threshold we warn the user once.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.constants import ParseMode

from config import MAX_CONSECUTIVE_FAILURES
from database import Database
from models import MonitoredItem
from scraper import ScrapeError, scrape

if TYPE_CHECKING:
    from telegram.ext import Application

logger = logging.getLogger(__name__)


class MonitorScheduler:
    """Owns the AsyncIOScheduler and the per-item check logic."""

    def __init__(self, db: Database, application: "Application") -> None:
        self._db = db
        self._app = application
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        # In-memory count of consecutive failures per item id.
        self._failures: dict[int, int] = {}

    # --- Lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    async def reload_jobs(self) -> int:
        """Re-create jobs for every active item in the DB. Returns count."""
        items = await self._db.list_all_active()
        for item in items:
            self.schedule_item(item)
        logger.info("Reloaded %d monitoring job(s) from the database", len(items))
        return len(items)

    # --- Job management -----------------------------------------------------

    def schedule_item(self, item: MonitoredItem) -> None:
        """Add (or replace) the recurring job for an item."""
        self._scheduler.add_job(
            self._check_item,
            trigger="interval",
            minutes=item.check_interval_minutes,
            id=item.job_id,
            args=[item.id],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    def reschedule_item(self, item: MonitoredItem) -> None:
        """Change the interval of an existing job (re-adds if missing)."""
        if self._scheduler.get_job(item.job_id):
            self._scheduler.reschedule_job(
                item.job_id,
                trigger="interval",
                minutes=item.check_interval_minutes,
            )
        else:
            self.schedule_item(item)

    def remove_item(self, item_id: int) -> None:
        job_id = f"monitor:{item_id}"
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
        self._failures.pop(item_id, None)

    def remove_all(self) -> None:
        self._scheduler.remove_all_jobs()
        self._failures.clear()

    # --- The actual check ---------------------------------------------------

    async def _check_item(self, item_id: int) -> None:
        item = await self._db.get_item(item_id)
        if item is None or not item.is_active:
            self.remove_item(item_id)
            return

        result = await self._scrape_with_retry(item)
        if result is None:
            await self._handle_failure(item)
            return

        # Success — reset failure counter.
        self._failures.pop(item_id, None)

        if result.price is None:
            await self._db.touch_checked(item_id)
            return

        old_price = item.last_price
        new_price = round(result.price, 2)

        if old_price is None:
            # First successful read after an unknown baseline — just store it.
            await self._db.update_price(item_id, new_price)
            return

        if round(old_price, 2) != new_price:
            await self._send_alert(item, old_price, new_price)
            await self._db.update_price(item_id, new_price)
        else:
            await self._db.touch_checked(item_id)

    async def _scrape_with_retry(self, item: MonitoredItem):
        """Scrape, retrying once on failure. Returns ScrapeResult or None."""
        for attempt in (1, 2):
            try:
                return await scrape(item.url)
            except ScrapeError as exc:
                logger.warning(
                    "Scrape attempt %d failed for item %d (%s): %s",
                    attempt,
                    item.id,
                    item.url,
                    exc,
                )
            except Exception:  # noqa: BLE001 - never let a check crash the loop
                logger.exception("Unexpected error scraping item %d", item.id)
        return None

    async def _handle_failure(self, item: MonitoredItem) -> None:
        count = self._failures.get(item.id, 0) + 1
        self._failures[item.id] = count
        logger.info("Item %d now has %d consecutive failure(s)", item.id, count)

        if count == MAX_CONSECUTIVE_FAILURES:
            text = (
                f"⚠️ Cannot reach {item.url} — will keep trying."
            )
            await self._safe_send(item.chat_id, text)

    # --- Messaging ----------------------------------------------------------

    async def _send_alert(
        self, item: MonitoredItem, old_price: float, new_price: float
    ) -> None:
        diff = round(new_price - old_price, 2)
        pct = round((diff / old_price) * 100, 2) if old_price else 0.0
        arrow = "📈" if diff > 0 else "📉"
        sign = "+" if diff > 0 else ""
        sym = item.currency_symbol

        text = (
            "🔔 *Price Alert!*\n"
            f"📦 Item: {_md(item.item_name)}\n"
            f"💰 New price: {sym}{new_price:.2f}\n"
            f"{arrow} Change: {sign}{sym}{diff:.2f} ({sign}{pct:.2f}%)"
        )
        await self._safe_send(item.chat_id, text)

    async def _safe_send(self, chat_id: int, text: str) -> None:
        try:
            await self._app.bot.send_message(
                chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send message to chat %d", chat_id)


def _md(text: str) -> str:
    """Escape Markdown-significant characters in dynamic text."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text

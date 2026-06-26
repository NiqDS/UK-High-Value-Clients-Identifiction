"""Data classes / types shared across the bot."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class MonitoredItem:
    """A single item being monitored for one chat."""

    id: int
    chat_id: int
    url: str
    item_name: str
    last_price: Optional[float]
    currency_symbol: str
    check_interval_minutes: int
    created_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    is_active: bool = True

    @property
    def job_id(self) -> str:
        """Stable APScheduler job id for this item."""
        return f"monitor:{self.id}"


@dataclass(slots=True)
class ScrapeResult:
    """Outcome of scraping a page for a price."""

    item_name: str
    price: float
    currency_symbol: str

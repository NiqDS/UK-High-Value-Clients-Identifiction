"""Economic-calendar ingestion for scheduled macro events (FOMC, CPI, PPI, NFP…).

Timestamps in config are treated as UTC (the ``timestamp_utc`` key). The
protective window for an event is ``[ts - pre, ts + post]`` — it opens *before*
the release to account for pre-announcement drift.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import EventConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledEvent:
    name: str
    timestamp: datetime  # UTC

    def window(self, pre_minutes: int, post_minutes: int) -> tuple[datetime, datetime]:
        return (
            self.timestamp - timedelta(minutes=pre_minutes),
            self.timestamp + timedelta(minutes=post_minutes),
        )


def load_calendar_csv(path: str | Path) -> list[ScheduledEvent]:
    """Load scheduled events from a CSV feed: columns name, timestamp_utc.

    e.g. FOMC/CPI/NFP release times exported from an economic calendar.
    """
    p = Path(path)
    if not p.exists():
        logger.warning("event calendar CSV not found: %s", p)
        return []
    out: list[ScheduledEvent] = []
    with p.open(newline="") as f:
        for row in csv.DictReader(f):
            ts = row.get("timestamp_utc") or row.get("timestamp") or row.get("date")
            if not ts:
                continue
            out.append(ScheduledEvent(name=row.get("name", "event"), timestamp=_parse_ts(ts)))
    return out


def _parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        s = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class EventCalendar:
    def __init__(self, events: list[ScheduledEvent]) -> None:
        self.events = sorted(events, key=lambda e: e.timestamp)

    @classmethod
    def from_config(cls, config: EventConfig) -> "EventCalendar":
        events: list[ScheduledEvent] = []
        for raw in config.calendar:
            ts = raw.get("timestamp_utc") or raw.get("timestamp")
            if ts is None:
                continue
            events.append(ScheduledEvent(name=str(raw.get("name", "event")), timestamp=_parse_ts(ts)))
        if config.calendar_csv:
            events += load_calendar_csv(config.calendar_csv)
        if config.include_halvings:
            from ..regime.cycle import BUILTIN_HALVINGS
            events += [ScheduledEvent(name="BTC halving", timestamp=h) for h in BUILTIN_HALVINGS]
        return cls(events)

    def active_events(
        self, now: datetime, pre_minutes: int, post_minutes: int
    ) -> list[ScheduledEvent]:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        out = []
        for e in self.events:
            start, end = e.window(pre_minutes, post_minutes)
            if start <= now <= end:
                out.append(e)
        return out

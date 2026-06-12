"""Economic-calendar ingestion for scheduled macro events (FOMC, CPI, PPI, NFP…).

Timestamps in config are treated as UTC (the ``timestamp_utc`` key). The
protective window for an event is ``[ts - pre, ts + post]`` — it opens *before*
the release to account for pre-announcement drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import EventConfig


@dataclass(frozen=True)
class ScheduledEvent:
    name: str
    timestamp: datetime  # UTC

    def window(self, pre_minutes: int, post_minutes: int) -> tuple[datetime, datetime]:
        return (
            self.timestamp - timedelta(minutes=pre_minutes),
            self.timestamp + timedelta(minutes=post_minutes),
        )


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

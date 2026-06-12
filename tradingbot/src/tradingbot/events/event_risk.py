"""Event-risk posture around scheduled macro releases.

Inside an event window the module applies a configurable protective posture —
pause entries, reduce size, widen maker spreads, tighten stops — rather than
betting on direction. It also reports window open/close transitions so the app
can send a Telegram heads-up and log the posture change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..config import EventConfig
from .calendar import EventCalendar


@dataclass
class EventPosture:
    in_window: bool = False
    pause_entries: bool = False
    size_multiplier: float = 1.0
    widen_offset_pct: float = 0.0
    tighten_stops: bool = False
    events: list[str] = field(default_factory=list)


class EventRiskModule:
    def __init__(self, config: EventConfig, calendar: EventCalendar) -> None:
        self.config = config
        self.calendar = calendar
        self._active_names: set[str] = set()

    def posture(self, now: datetime) -> EventPosture:
        if not self.config.enabled:
            return EventPosture()
        active = self.calendar.active_events(
            now, self.config.pre_event_window_minutes, self.config.post_event_window_minutes
        )
        if not active:
            return EventPosture()
        return EventPosture(
            in_window=True,
            pause_entries=self.config.pause_entries,
            size_multiplier=max(0.0, 1.0 - self.config.reduce_size_pct / 100.0),
            widen_offset_pct=self.config.widen_maker_offset_pct,
            tighten_stops=self.config.tighten_stops,
            events=[e.name for e in active],
        )

    def transitions(self, now: datetime) -> list[tuple[str, str]]:
        """Return ('open'|'close', event_name) transitions since the last call.

        Drives Telegram heads-ups. Call once per loop tick.
        """
        if not self.config.enabled:
            return []
        current = {
            e.name
            for e in self.calendar.active_events(
                now, self.config.pre_event_window_minutes, self.config.post_event_window_minutes
            )
        }
        opened = current - self._active_names
        closed = self._active_names - current
        self._active_names = current
        return [("open", n) for n in sorted(opened)] + [("close", n) for n in sorted(closed)]

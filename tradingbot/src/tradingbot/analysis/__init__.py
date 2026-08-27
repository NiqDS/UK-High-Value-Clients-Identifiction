"""Exploratory analysis (event/news correspondence with chart extremes)."""

from .event_study import (
    BUILTIN_EVENTS,
    EventStudy,
    KnownEvent,
    load_events_csv,
    study_events,
    swing_points,
)

__all__ = [
    "BUILTIN_EVENTS", "EventStudy", "KnownEvent", "load_events_csv",
    "study_events", "swing_points",
]

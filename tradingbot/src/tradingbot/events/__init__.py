"""Event-risk (scheduled macro events) and the news/volatility kill-switch."""

from .calendar import EventCalendar, ScheduledEvent
from .event_risk import EventPosture, EventRiskModule
from .kill_switch import KillSwitch, KillSwitchState, Observation
from .posture import CombinedPosture, PostureProvider

__all__ = [
    "EventCalendar",
    "ScheduledEvent",
    "EventPosture",
    "EventRiskModule",
    "KillSwitch",
    "KillSwitchState",
    "Observation",
    "CombinedPosture",
    "PostureProvider",
]

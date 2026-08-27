"""Combine the event-risk posture and the kill-switch into one signal the
pipeline consults before executing entries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .event_risk import EventRiskModule
from .kill_switch import KillSwitch


@dataclass
class CombinedPosture:
    pause_entries: bool = False
    size_multiplier: float = 1.0
    widen_offset_pct: float = 0.0
    tighten_stops: bool = False
    reasons: list[str] = field(default_factory=list)


class PostureProvider:
    def __init__(
        self,
        event_module: EventRiskModule | None = None,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        self.event_module = event_module
        self.kill_switch = kill_switch

    def posture(self, now: datetime) -> CombinedPosture:
        out = CombinedPosture()
        if self.event_module is not None:
            ep = self.event_module.posture(now)
            if ep.in_window:
                out.pause_entries |= ep.pause_entries
                out.size_multiplier *= ep.size_multiplier
                out.widen_offset_pct = max(out.widen_offset_pct, ep.widen_offset_pct)
                out.tighten_stops |= ep.tighten_stops
                out.reasons.append(f"event-risk: {', '.join(ep.events)}")
        if self.kill_switch is not None and self.kill_switch.is_paused(now):
            out.pause_entries = True
            st = self.kill_switch.status(now)
            out.reasons.append(f"kill-switch: {st.reason or 'manual pause'}")
        return out

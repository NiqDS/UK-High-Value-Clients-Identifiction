"""Do known events line up with BTC chart highs/lows?

Detects swing highs/lows in an OHLC series and measures, for each event, the
distance (in days) to the nearest swing extreme and whether that extreme is a
high or a low. Includes built-in BTC halvings + notable macro/geopolitical dates;
extra events can be supplied via CSV (date,label[,kind]).

This is exploratory analysis, not a trading signal — it quantifies correspondence
so we can decide whether an event/news overlay is worth wiring into the strategy.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..exchange.models import Candle

_DAY_MS = 86_400_000


@dataclass(frozen=True)
class KnownEvent:
    date: str  # ISO YYYY-MM-DD (UTC)
    label: str
    kind: str  # halving | macro | geopolitical

    @property
    def ts(self) -> int:
        return int(datetime.fromisoformat(self.date).replace(tzinfo=timezone.utc).timestamp() * 1000)


# Curated, well-documented dates. Halvings + major macro/geopolitical shocks.
BUILTIN_EVENTS: list[KnownEvent] = [
    KnownEvent("2012-11-28", "1st halving", "halving"),
    KnownEvent("2016-07-09", "2nd halving", "halving"),
    KnownEvent("2020-03-12", "COVID crash (Black Thursday)", "macro"),
    KnownEvent("2020-05-11", "3rd halving", "halving"),
    KnownEvent("2022-02-24", "Russia invades Ukraine", "geopolitical"),
    KnownEvent("2022-05-09", "Terra/LUNA collapse", "macro"),
    KnownEvent("2022-11-08", "FTX collapse", "macro"),
    KnownEvent("2023-03-10", "SVB / US banking crisis", "macro"),
    KnownEvent("2024-01-11", "US spot BTC ETFs launch", "macro"),
    KnownEvent("2024-04-20", "4th halving", "halving"),
]


@dataclass
class EventHit:
    event: KnownEvent
    nearest_kind: str  # "high" | "low" | "none"
    distance_days: float
    extreme_price: float | None


@dataclass
class EventStudy:
    swing_window: int
    match_window_days: int
    hits: list[EventHit]

    @property
    def in_range(self) -> list[EventHit]:
        return [h for h in self.hits if h.nearest_kind != "none"]

    def render(self) -> str:
        lines = [
            "## Event ↔ chart-extreme correspondence",
            f"swing window: ±{self.swing_window} bars | match window: ±{self.match_window_days} days",
            "",
            "event date  | label                          | kind         | nearest | dist(d) | price",
        ]
        for h in self.hits:
            price = f"{h.extreme_price:,.0f}" if h.extreme_price is not None else "—"
            dist = f"{h.distance_days:6.1f}" if h.nearest_kind != "none" else "   —  "
            lines.append(
                f"{h.event.date} | {h.event.label:30s} | {h.event.kind:12s} | "
                f"{h.nearest_kind:7s} | {dist} | {price}"
            )
        matched = self.in_range
        if matched:
            highs = sum(1 for h in matched if h.nearest_kind == "high")
            lows = sum(1 for h in matched if h.nearest_kind == "low")
            mean_d = sum(h.distance_days for h in matched) / len(matched)
            lines += [
                "",
                f"events within ±{self.match_window_days}d of a swing extreme: "
                f"{len(matched)}/{len([h for h in self.hits if _covered(h)])} covered "
                f"(near highs: {highs}, near lows: {lows}, mean dist {mean_d:.1f}d)",
                "> Correspondence ≠ causation or tradability — extremes are only known in "
                "hindsight. Treat as a prompt for hypotheses, not a signal.",
            ]
        return "\n".join(lines)


def _covered(hit: EventHit) -> bool:
    return hit.extreme_price is not None or hit.nearest_kind != "none"


def swing_points(candles: list[Candle], window: int) -> tuple[list[int], list[int]]:
    """Indices of swing highs and swing lows (local extreme over ±window bars)."""
    highs, lows = [], []
    n = len(candles)
    for i in range(n):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)
        seg = candles[lo:hi]
        if candles[i].high == max(c.high for c in seg):
            highs.append(i)
        if candles[i].low == min(c.low for c in seg):
            lows.append(i)
    return highs, lows


def _nearest(candles, event_ts, idxs, attr):
    best_i, best_d = None, None
    for i in idxs:
        d = abs(candles[i].timestamp - event_ts)
        if best_d is None or d < best_d:
            best_d, best_i = d, i
    if best_i is None:
        return None, None, None
    return best_i, best_d / _DAY_MS, getattr(candles[best_i], attr)


def study_events(
    candles: list[Candle], events: list[KnownEvent],
    swing_window: int = 20, match_window_days: int = 7,
) -> EventStudy:
    if not candles:
        return EventStudy(swing_window, match_window_days, [])
    highs, lows = swing_points(candles, swing_window)
    start, end = candles[0].timestamp, candles[-1].timestamp
    hits: list[EventHit] = []
    for ev in events:
        if not (start - match_window_days * _DAY_MS <= ev.ts <= end + match_window_days * _DAY_MS):
            hits.append(EventHit(ev, "none", float("inf"), None))  # outside data range
            continue
        hi_i, hi_d, hi_p = _nearest(candles, ev.ts, highs, "high")
        lo_i, lo_d, lo_p = _nearest(candles, ev.ts, lows, "low")
        # choose the closer of the two extremes, if within the match window
        cand = []
        if hi_d is not None:
            cand.append(("high", hi_d, hi_p))
        if lo_d is not None:
            cand.append(("low", lo_d, lo_p))
        cand = [c for c in cand if c[1] <= match_window_days]
        if not cand:
            hits.append(EventHit(ev, "none", float("inf"), None))
            continue
        kind, dist, price = min(cand, key=lambda c: c[1])
        hits.append(EventHit(ev, kind, dist, price))
    return EventStudy(swing_window, match_window_days, hits)


def load_events_csv(path: str | Path) -> list[KnownEvent]:
    out: list[KnownEvent] = []
    with Path(path).open(newline="") as f:
        for row in csv.DictReader(f):
            out.append(KnownEvent(date=row["date"], label=row.get("label", "event"),
                                  kind=row.get("kind", "macro")))
    return out

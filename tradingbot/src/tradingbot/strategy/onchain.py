"""MVRV Z-score valuation overlay + a strategy wrapper.

Low MVRV-Z (cheap vs realized value) => favorable for new longs => full size;
high Z (euphoria) => scale down / hard-gate. Like the funding overlay it
modulates *size, never direction*, and wraps any base strategy so the same
honest backtest/compare measures its contribution.
"""

from __future__ import annotations

import bisect
import dataclasses
from dataclasses import dataclass

from ..config import MvrvConfig
from ..domain import Side
from ..exchange.onchain import MvrvPoint
from .base import MarketData, Strategy


@dataclass
class MvrvSeries:
    points: list[MvrvPoint]

    def __post_init__(self) -> None:
        self.points = sorted(self.points, key=lambda p: p.timestamp)
        self._ts = [p.timestamp for p in self.points]

    def latest_before(self, ts: int) -> MvrvPoint | None:
        i = bisect.bisect_right(self._ts, ts)
        return self.points[i - 1] if i > 0 else None


@dataclass
class MvrvState:
    multiplier: float
    z: float
    rich: bool


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class MvrvOverlay:
    def __init__(self, config: MvrvConfig) -> None:
        self.config = config

    def assess(self, point: MvrvPoint | None) -> MvrvState:
        c = self.config
        if point is None:
            return MvrvState(1.0, 0.0, False)
        z = point.z
        favorability = _clamp((c.high_z - z) / (c.high_z - c.low_z), 0.0, 1.0)
        mult = _clamp(c.min_mult + favorability * (c.max_mult - c.min_mult), c.min_mult, c.max_mult)
        return MvrvState(round(mult, 4), z, z >= c.high_z)


class MvrvFilteredStrategy(Strategy):
    def __init__(
        self, base: Strategy, series: MvrvSeries, overlay: MvrvOverlay,
        gate_when_rich: bool = False,
    ) -> None:
        self.base = base
        self.series = series
        self.overlay = overlay
        self.gate_when_rich = gate_when_rich
        self.name = f"{base.name}+mvrv"

    def generate_signals(self, market: MarketData):
        sigs = self.base.generate_signals(market)
        if not market.candles:
            return sigs
        state = self.overlay.assess(self.series.latest_before(market.candles[-1].timestamp))
        out = []
        for s in sigs:
            if s.side == Side.BUY and s.is_entry:
                if self.gate_when_rich and state.rich:
                    continue
                meta = {**(s.metadata or {}), "mvrv_z": state.z}
                out.append(dataclasses.replace(s, amount=s.amount * state.multiplier, metadata=meta))
            else:
                out.append(s)
        return out

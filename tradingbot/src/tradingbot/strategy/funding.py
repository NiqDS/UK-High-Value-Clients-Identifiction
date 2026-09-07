"""Funding-rate positioning overlay (contrarian) + a strategy wrapper.

The overlay reads the funding-rate z-score over a rolling window: crowded longs
(high positive funding) => unfavorable for new longs => scale down / skip;
crowded shorts / capitulation (very negative funding) => favorable => scale up.
It never flips direction — it modulates the *size* of the base strategy's long
entries (and can hard-gate them when funding is extreme), so it stacks with the
'≥2 signals must agree' discipline.

``FundingFilteredStrategy`` wraps any base strategy so the same honest backtest /
walk-forward / compare tooling measures funding's out-of-sample contribution.
"""

from __future__ import annotations

import bisect
import dataclasses
import statistics
from dataclasses import dataclass

from ..config import FundingConfig
from ..domain import Side
from ..exchange.funding import FundingRate
from .base import MarketData, Strategy


@dataclass
class FundingSeries:
    rates: list[FundingRate]

    def __post_init__(self) -> None:
        self.rates = sorted(self.rates, key=lambda r: r.timestamp)
        self._ts = [r.timestamp for r in self.rates]

    def up_to(self, ts: int) -> list[FundingRate]:
        """Funding observations at or before ``ts`` (no look-ahead)."""
        return self.rates[: bisect.bisect_right(self._ts, ts)]


@dataclass
class FundingState:
    multiplier: float
    z: float
    latest_rate: float
    crowded: bool


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class FundingOverlay:
    def __init__(self, config: FundingConfig) -> None:
        self.config = config

    def assess(self, rates: list[FundingRate]) -> FundingState:
        c = self.config
        if len(rates) < c.z_window + 1:
            return FundingState(1.0, 0.0, rates[-1].rate if rates else 0.0, False)
        latest = rates[-1].rate
        hist = [r.rate for r in rates[-c.z_window - 1 : -1]]
        mean = statistics.fmean(hist)
        std = statistics.pstdev(hist)
        if std > 0:
            z = (latest - mean) / std
        elif abs(latest - mean) < 1e-12:
            z = 0.0
        else:  # flat history, then a jump -> treat as an extreme
            z = (c.extreme_z * 2) if latest > mean else (-c.extreme_z * 2)
        # z = +extreme (crowded longs) -> favorability 0; z = -extreme -> 1
        favorability = _clamp(0.5 - z / (2 * c.extreme_z), 0.0, 1.0)
        mult = _clamp(c.min_mult + favorability * (c.max_mult - c.min_mult), c.min_mult, c.max_mult)
        return FundingState(round(mult, 4), round(z, 3), latest, z >= c.extreme_z)


class FundingFilteredStrategy(Strategy):
    def __init__(
        self, base: Strategy, series: FundingSeries, overlay: FundingOverlay,
        gate_when_crowded: bool = False,
    ) -> None:
        self.base = base
        self.series = series
        self.overlay = overlay
        self.gate_when_crowded = gate_when_crowded
        self.name = f"{base.name}+funding"

    def generate_signals(self, market: MarketData):
        sigs = self.base.generate_signals(market)
        if not market.candles:
            return sigs
        now_ts = market.candles[-1].timestamp
        state = self.overlay.assess(self.series.up_to(now_ts))
        out = []
        for s in sigs:
            if s.side == Side.BUY and s.is_entry:
                if self.gate_when_crowded and state.crowded:
                    continue  # skip new longs into crowded leverage
                meta = {**(s.metadata or {}), "funding_z": state.z, "funding_rate": state.latest_rate}
                out.append(dataclasses.replace(s, amount=s.amount * state.multiplier, metadata=meta))
            else:
                out.append(s)
        return out

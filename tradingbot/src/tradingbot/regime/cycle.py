"""Cycle / volatility regime overlay — scales the risk BUDGET, not direction.

Evidence-aligned use of the long-term BTC research: instead of predicting price,
size exposure by slow regime. Two inputs:
  - position vs the 200-week SMA (historically the "generational" accumulation
    floor): below it => cheap => scale up; far above => extended => scale down;
  - halving phase: 0–18 months post-halving has historically been the expansion
    leg; the late-cycle/distribution window scales down.

Produces a multiplier in [min_risk_multiplier, max_risk_multiplier] applied to
entry size, on a daily-or-slower cadence. OFF by default (RegimeConfig.enabled)
— it is a slow risk governor, not a trade trigger, and the cycle is a tiny
sample, so treat it as fragile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import RegimeConfig
from ..exchange.models import Candle

# BTC halving dates (UTC)
BUILTIN_HALVINGS: list[datetime] = [
    datetime(2012, 11, 28, tzinfo=timezone.utc),
    datetime(2016, 7, 9, tzinfo=timezone.utc),
    datetime(2020, 5, 11, tzinfo=timezone.utc),
    datetime(2024, 4, 20, tzinfo=timezone.utc),
]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _sma(values: list[float], period: int) -> float | None:
    if not values:
        return None
    period = min(period, len(values))
    return sum(values[-period:]) / period


def _phase_score(months: float) -> tuple[float, str]:
    """Map months-since-last-halving to a favorability score [0,1] + a label."""
    if months < 18:
        return 1.0, "post-halving expansion"
    if months < 30:
        return _clamp(1.0 - (months - 18) / 12 * 0.8, 0.2, 1.0), "late cycle / distribution"
    if months < 42:
        return _clamp(0.2 + (months - 30) / 12 * 0.5, 0.2, 0.7), "bear / accumulation"
    return 0.9, "pre-halving accumulation"


@dataclass
class CycleRegime:
    multiplier: float
    phase: str
    price_to_200w: float | None  # price / 200-week SMA
    months_since_halving: float | None
    reasons: list[str] = field(default_factory=list)


class CycleRegimeOverlay:
    def __init__(self, config: RegimeConfig, halvings: list[datetime] | None = None) -> None:
        self.config = config
        self.halvings = halvings or BUILTIN_HALVINGS

    def assess(self, daily_candles: list[Candle], now: datetime | None = None) -> CycleRegime:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if not self.config.enabled:
            return CycleRegime(1.0, "disabled", None, None, ["regime overlay disabled"])

        reasons: list[str] = []
        # --- value vs 200-week SMA (1 week ~ 7 daily bars) ---
        closes = [c.close for c in daily_candles]
        sma_period = self.config.sma_weeks * 7
        sma = _sma(closes, sma_period)
        ratio = None
        value_score = 0.5
        if sma and closes:
            ratio = closes[-1] / sma
            # below the SMA (ratio<1) => 1.0; ~2x => 0.5; >=3x => 0.0
            value_score = _clamp(1.5 - ratio / 2.0, 0.0, 1.0)
            note = "below" if ratio < 1 else "above"
            reasons.append(f"price {ratio:.2f}x 200w-SMA ({note})")
            if len(closes) < sma_period:
                reasons.append(f"SMA is a proxy ({len(closes)}/{sma_period} daily bars)")
        else:
            reasons.append("no price history for 200w-SMA; value score neutral")

        # --- halving phase ---
        months = None
        phase = "unknown"
        phase_score = 0.5
        last = max((h for h in self.halvings if h <= now), default=None)
        if last is not None:
            months = (now - last).days / 30.4
            phase_score, phase = _phase_score(months)
            reasons.append(f"{months:.1f} months since halving -> {phase}")

        favorability = 0.5 * value_score + 0.5 * phase_score
        lo, hi = self.config.min_risk_multiplier, self.config.max_risk_multiplier
        mult = _clamp(lo + favorability * (hi - lo), lo, hi)
        return CycleRegime(multiplier=round(mult, 4), phase=phase,
                           price_to_200w=ratio, months_since_halving=months, reasons=reasons)

"""News / volatility kill-switch for unscheduled shocks.

DEFENSIVE, not predictive. A retail bot cannot out-react a sudden repricing, so
on an abnormal price-velocity or volume spike (N-sigma over a rolling window)
this immediately pauses new entries — it never auto-trades the direction of the
shock. It resumes only after volatility normalises for a cooldown, or on a
manual resume.

Designed to live in its own coroutine, independent of the strategy loop, so it
can halt trading even mid-decision. Tests feed observations directly.
"""

from __future__ import annotations

import logging
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..config import KillSwitchConfig

logger = logging.getLogger(__name__)

# Minimum samples before the sigma test is meaningful.
_MIN_SAMPLES = 5


@dataclass(frozen=True)
class Observation:
    timestamp: datetime
    price: float
    volume: float = 0.0
    symbol: str = ""  # observations are grouped PER SYMBOL — mixing coins of
                      # different price scales (BTC ~100k, ADA ~0.5) into one
                      # returns series manufactures spurious 100σ "moves"


@dataclass
class KillSwitchState:
    paused: bool = False
    triggered: bool = False
    manual_pause: bool = False
    reason: str = ""
    triggered_at: datetime | None = None
    cooldown_until: datetime | None = None


def _zscore(latest: float, history: list[float]) -> float:
    """|z| of ``latest`` against ``history``. A jump out of a flat series
    (zero variance) returns +inf so it always trips the threshold."""
    if len(history) < 2:
        return 0.0
    mean = statistics.fmean(history)
    std = statistics.pstdev(history)
    if std == 0.0:
        return float("inf") if abs(latest - mean) > 1e-12 else 0.0
    return abs(latest - mean) / std


@dataclass
class KillSwitch:
    config: KillSwitchConfig
    # per-symbol rolling windows of recent observations (keyed by symbol)
    _series: dict[str, deque[Observation]] = field(default_factory=dict)
    _last_ts: dict[str, datetime] = field(default_factory=dict)
    _triggered: bool = False
    _manual_pause: bool = False
    _reason: str = ""
    _triggered_at: datetime | None = None
    _cooldown_until: datetime | None = None

    # -- abnormality detection ---------------------------------------------
    def _window(self) -> int:
        """Number of recent bars kept per symbol. Count-based (not wall-clock
        minutes) so it is cadence-agnostic — the same config works for a 1m
        intraday feed and a daily-bar feed without mis-trimming."""
        return max(self.config.rolling_window_minutes, _MIN_SAMPLES + 2)

    def _abnormal_one(self, obs_list: deque[Observation]) -> tuple[bool, str]:
        if len(obs_list) < _MIN_SAMPLES + 1:
            return False, ""
        prices = [o.price for o in obs_list]
        returns = [
            (prices[i] - prices[i - 1]) / prices[i - 1]
            for i in range(1, len(prices))
            if prices[i - 1] != 0
        ]
        if len(returns) >= _MIN_SAMPLES:
            z = _zscore(returns[-1], returns[:-1])
            if z >= self.config.price_velocity_sigma:
                return True, f"price velocity {z:.1f}σ"
        volumes = [o.volume for o in obs_list]
        if any(volumes):
            zv = _zscore(volumes[-1], volumes[:-1])
            if zv >= self.config.volume_spike_sigma:
                return True, f"volume spike {zv:.1f}σ"
        return False, ""

    def _abnormal(self, symbol: str) -> tuple[bool, str]:
        """Evaluate ONLY the symbol that just received an observation (its series
        is the one that changed). A shock on any single symbol pauses new entries
        basket-wide — crypto sells off together — but the sigma is measured
        against that symbol's OWN history."""
        abnormal, reason = self._abnormal_one(self._series[symbol])
        if abnormal and symbol:
            reason = f"{symbol} {reason}"
        return abnormal, reason

    # -- ingestion ----------------------------------------------------------
    def observe(self, obs: Observation) -> KillSwitchState:
        """Feed a market observation; update trigger/cooldown state."""
        series = self._series.setdefault(obs.symbol, deque(maxlen=self._window()))
        # dedupe: the runner re-feeds the same recent bars every poll — only a
        # strictly newer bar for this symbol advances the window
        last = self._last_ts.get(obs.symbol)
        if last is not None and obs.timestamp <= last:
            return self.status(obs.timestamp)
        series.append(obs)
        self._last_ts[obs.symbol] = obs.timestamp

        if not self.config.enabled:
            return self.status(obs.timestamp)

        abnormal, reason = self._abnormal(obs.symbol)
        if abnormal:
            first = not self._triggered
            self._triggered = True
            self._reason = reason
            self._triggered_at = obs.timestamp
            self._cooldown_until = obs.timestamp + timedelta(minutes=self.config.cooldown_minutes)
            if first:
                logger.warning("KILL SWITCH TRIGGERED: %s — pausing new entries", reason)
        elif self._triggered and self._cooldown_until and obs.timestamp >= self._cooldown_until:
            logger.info("Kill switch: volatility normalised — resuming")
            self._triggered = False
            self._reason = ""
            self._triggered_at = None
            self._cooldown_until = None
        return self.status(obs.timestamp)

    # -- manual control -----------------------------------------------------
    def manual_pause(self) -> None:
        self._manual_pause = True

    def manual_resume(self) -> None:
        self._manual_pause = False
        self._triggered = False
        self._reason = ""
        self._triggered_at = None
        self._cooldown_until = None

    # -- queries ------------------------------------------------------------
    def is_paused(self, now: datetime | None = None) -> bool:
        return self._manual_pause or self._triggered

    def status(self, now: datetime | None = None) -> KillSwitchState:
        return KillSwitchState(
            paused=self.is_paused(now),
            triggered=self._triggered,
            manual_pause=self._manual_pause,
            reason=self._reason,
            triggered_at=self._triggered_at,
            cooldown_until=self._cooldown_until,
        )

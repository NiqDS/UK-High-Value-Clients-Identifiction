"""Cycle/volatility regime overlay + event-calendar CSV/halving feed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingbot.config import EventConfig, RegimeConfig
from tradingbot.events.calendar import EventCalendar, load_calendar_csv
from tradingbot.exchange.models import Candle
from tradingbot.regime.cycle import BUILTIN_HALVINGS, CycleRegimeOverlay

_DAY = 86_400_000


def daily(closes: list[float]) -> list[Candle]:
    return [Candle(i * _DAY, c, c, c, c, 1.0) for i, c in enumerate(closes)]


def cfg(**kw) -> RegimeConfig:
    base = dict(enabled=True, min_risk_multiplier=0.25, max_risk_multiplier=1.0, sma_weeks=4)
    base.update(kw)
    return RegimeConfig(**base)


# --- regime overlay --------------------------------------------------------
def test_disabled_overlay_is_neutral() -> None:
    r = CycleRegimeOverlay(RegimeConfig(enabled=False)).assess(daily([100] * 40))
    assert r.multiplier == 1.0
    assert r.phase == "disabled"


def test_below_200w_sma_scales_up() -> None:
    # last price well below the long SMA (value cheap) + recent halving => high mult
    closes = [200] * 27 + [80]  # sma_weeks=4 -> 28-bar SMA; last 80 << avg
    now = BUILTIN_HALVINGS[-1] + timedelta(days=60)  # post-halving expansion
    r = CycleRegimeOverlay(cfg()).assess(daily(closes), now)
    assert r.price_to_200w < 1.0
    assert r.multiplier > 0.8  # toward max


def test_far_above_sma_scales_down() -> None:
    closes = [100] * 27 + [400]  # 4x the average -> extended
    now = BUILTIN_HALVINGS[-1] + timedelta(days=24 * 30)  # ~24 months: late cycle
    r = CycleRegimeOverlay(cfg()).assess(daily(closes), now)
    assert r.price_to_200w > 2.0
    assert r.multiplier < 0.6  # toward min


def test_multiplier_within_configured_bounds() -> None:
    o = CycleRegimeOverlay(cfg(min_risk_multiplier=0.4, max_risk_multiplier=0.9))
    for closes in ([50] * 28, [100] * 28, [500] * 28):
        m = o.assess(daily(closes), BUILTIN_HALVINGS[-1] + timedelta(days=100)).multiplier
        assert 0.4 <= m <= 0.9


def test_phase_labels_change_with_halving_distance() -> None:
    o = CycleRegimeOverlay(cfg())
    near = o.assess(daily([100] * 28), BUILTIN_HALVINGS[-1] + timedelta(days=30))
    late = o.assess(daily([100] * 28), BUILTIN_HALVINGS[-1] + timedelta(days=24 * 30))
    assert near.phase == "post-halving expansion"
    assert "late cycle" in late.phase or "distribution" in late.phase


# --- event calendar feed ---------------------------------------------------
def test_calendar_csv_loads(tmp_path) -> None:
    p = tmp_path / "cal.csv"
    p.write_text("name,timestamp_utc\nFOMC,2026-01-28T19:00:00Z\nCPI,2026-02-12T13:30:00Z\n")
    events = load_calendar_csv(p)
    assert len(events) == 2
    assert events[0].name == "FOMC"


def test_from_config_merges_csv_and_halvings(tmp_path) -> None:
    p = tmp_path / "cal.csv"
    p.write_text("name,timestamp_utc\nFOMC,2026-01-28T19:00:00Z\n")
    cfg = EventConfig(calendar=[{"name": "CPI", "timestamp_utc": "2026-02-12T13:30:00Z"}],
                      calendar_csv=str(p), include_halvings=True)
    cal = EventCalendar.from_config(cfg)
    names = {e.name for e in cal.events}
    assert {"CPI", "FOMC", "BTC halving"} <= names


def test_halvings_excluded_when_disabled() -> None:
    cal = EventCalendar.from_config(EventConfig(include_halvings=False))
    assert all(e.name != "BTC halving" for e in cal.events)

"""Event-risk module + kill-switch tests (feeds mocked)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradingbot.config import EventConfig, KillSwitchConfig
from tradingbot.events.calendar import EventCalendar
from tradingbot.events.event_risk import EventRiskModule
from tradingbot.events.kill_switch import KillSwitch, Observation
from tradingbot.events.posture import PostureProvider

EVT = datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc)


def event_cfg(**kw) -> EventConfig:
    base = dict(enabled=True, pre_event_window_minutes=60, post_event_window_minutes=30,
                pause_entries=True, reduce_size_pct=50.0, widen_maker_offset_pct=0.10,
                calendar=[{"name": "FOMC", "timestamp_utc": "2026-06-12T18:00:00Z"}])
    base.update(kw)
    return EventConfig(**base)


def module(**kw) -> EventRiskModule:
    cfg = event_cfg(**kw)
    return EventRiskModule(cfg, EventCalendar.from_config(cfg))


# --- event-risk windows ----------------------------------------------------
def test_no_posture_outside_window() -> None:
    p = module().posture(EVT - timedelta(minutes=90))
    assert p.in_window is False and p.pause_entries is False and p.size_multiplier == 1.0


def test_pre_event_window_opens_before_timestamp() -> None:
    # 30 min before the release, within the 60-min pre window (pre-drift)
    p = module().posture(EVT - timedelta(minutes=30))
    assert p.in_window is True
    assert p.pause_entries is True
    assert p.size_multiplier == 0.5
    assert p.widen_offset_pct == 0.10
    assert "FOMC" in p.events


def test_post_event_window_closes_after() -> None:
    assert module().posture(EVT + timedelta(minutes=20)).in_window is True
    assert module().posture(EVT + timedelta(minutes=40)).in_window is False


def test_disabled_module_is_neutral() -> None:
    assert module(enabled=False).posture(EVT).in_window is False


def test_transitions_open_then_close() -> None:
    m = module()
    assert m.transitions(EVT - timedelta(minutes=90)) == []
    assert m.transitions(EVT - timedelta(minutes=30)) == [("open", "FOMC")]
    assert m.transitions(EVT - timedelta(minutes=20)) == []  # still open, no transition
    assert m.transitions(EVT + timedelta(minutes=40)) == [("close", "FOMC")]


# --- kill switch -----------------------------------------------------------
def ks_cfg(**kw) -> KillSwitchConfig:
    base = dict(enabled=True, rolling_window_minutes=60, price_velocity_sigma=4.0,
                volume_spike_sigma=4.0, cooldown_minutes=10)
    base.update(kw)
    return KillSwitchConfig(**base)


def _feed(ks: KillSwitch, prices, volumes=None, start_min=0):
    base = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
    vols = volumes or [10.0] * len(prices)
    state = None
    for i, (p, v) in enumerate(zip(prices, vols)):
        state = ks.observe(Observation(base + timedelta(minutes=start_min + i), p, v))
    return state


def test_kill_switch_quiet_market_not_triggered() -> None:
    ks = KillSwitch(ks_cfg())
    state = _feed(ks, [100, 100.1, 99.9, 100.05, 99.95, 100.0, 100.1])
    assert state.triggered is False
    assert state.paused is False


def test_kill_switch_triggers_on_price_spike() -> None:
    ks = KillSwitch(ks_cfg())
    # quiet, then a violent jump
    state = _feed(ks, [100, 100.1, 99.9, 100.05, 99.95, 100.0, 110.0])
    assert state.triggered is True
    assert state.paused is True
    assert "price velocity" in state.reason


def test_kill_switch_triggers_on_volume_spike() -> None:
    ks = KillSwitch(ks_cfg())
    state = _feed(
        ks,
        prices=[100] * 7,  # flat price => no price trigger
        volumes=[10, 11, 9, 10, 10, 11, 5000],  # volume explosion
    )
    assert state.triggered is True
    assert "volume spike" in state.reason


def test_kill_switch_resumes_after_cooldown() -> None:
    ks = KillSwitch(ks_cfg(cooldown_minutes=5))
    _feed(ks, [100, 100.1, 99.9, 100.05, 99.95, 100.0, 110.0])  # spike at minute 6
    assert ks.is_paused() is True
    # quiet observations at ~110, past the 5-min cooldown
    state = _feed(ks, [110.1, 110.0, 110.05, 109.95, 110.1, 110.0], start_min=7)
    assert state.triggered is False
    assert ks.is_paused() is False


def test_kill_switch_manual_pause_and_resume() -> None:
    ks = KillSwitch(ks_cfg())
    ks.manual_pause()
    assert ks.is_paused() is True
    ks.manual_resume()
    assert ks.is_paused() is False


def test_disabled_kill_switch_never_pauses() -> None:
    ks = KillSwitch(ks_cfg(enabled=False))
    state = _feed(ks, [100, 100.1, 99.9, 100.05, 99.95, 100.0, 110.0])
    assert state.triggered is False


# --- combined posture ------------------------------------------------------
def test_combined_posture_merges_event_and_kill_switch() -> None:
    m = module()
    ks = KillSwitch(ks_cfg())
    provider = PostureProvider(event_module=m, kill_switch=ks)
    # inside event window -> pause + half size
    p = provider.posture(EVT - timedelta(minutes=30))
    assert p.pause_entries is True
    assert p.size_multiplier == 0.5
    assert any("event-risk" in r for r in p.reasons)

    ks.manual_pause()
    p2 = provider.posture(EVT - timedelta(minutes=200))  # outside event window
    assert p2.pause_entries is True
    assert any("kill-switch" in r for r in p2.reasons)

"""SettingsController, override persistence, auth, and StatusReporter tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingbot.config import Config, KillSwitchConfig
from tradingbot.approval.controls import JsonOverrideStore, SettingsController, is_authorized
from tradingbot.approval.status import StatusReporter
from tradingbot.events.kill_switch import KillSwitch
from tradingbot.risk.state import InMemoryRiskStateStore

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def make() -> tuple[SettingsController, Config, InMemoryRiskStateStore, KillSwitch]:
    cfg = Config()
    store = InMemoryRiskStateStore()
    ks = KillSwitch(KillSwitchConfig())
    return SettingsController(cfg, store, kill_switch=ks), cfg, store, ks


# --- auth ------------------------------------------------------------------
def test_is_authorized_empty_allowlist_denies_all() -> None:
    assert is_authorized(123, []) is False
    assert is_authorized(123, [123]) is True
    assert is_authorized(999, [123]) is False


# --- settings mutations + validation --------------------------------------
def test_set_threshold_updates_live_config() -> None:
    ctrl, cfg, _, _ = make()
    ctrl.set_approval_threshold(250.0)
    assert cfg.telegram.approval_threshold_quote == 250.0


def test_set_threshold_rejects_negative() -> None:
    ctrl, _, _, _ = make()
    with pytest.raises(ValueError):
        ctrl.set_approval_threshold(-1)


def test_set_max_notional_rejects_below_min() -> None:
    ctrl, cfg, _, _ = make()
    cfg.risk.min_notional_per_trade_quote = 20
    with pytest.raises(ValueError):
        ctrl.set_max_notional(10)


def test_set_buy_valuation_floor_allows_negative() -> None:
    ctrl, cfg, _, _ = make()
    ctrl.set_buy_valuation_floor(-0.5)  # require 0.5% discount to VWAP
    assert cfg.strategy.buy_valuation_floor_pct == -0.5


def test_set_force_exit_updates_and_rejects_non_positive() -> None:
    ctrl, cfg, _, _ = make()
    ctrl.set_force_exit_overvaluation(2.5)
    assert cfg.strategy.force_exit_overvaluation_pct == 2.5
    with pytest.raises(ValueError):
        ctrl.set_force_exit_overvaluation(0)


def test_pause_and_resume_toggle_trading_and_kill_switch() -> None:
    ctrl, _, store, ks = make()
    ctrl.pause_trading()
    assert store.is_trading_enabled() is False
    assert ks.is_paused() is True
    ctrl.resume_trading()
    assert store.is_trading_enabled() is True
    assert ks.is_paused() is False


def test_current_settings_snapshot() -> None:
    ctrl, _, _, _ = make()
    s = ctrl.current_settings()
    assert set(s) >= {"approval_threshold_quote", "max_notional_per_trade_quote",
                      "max_trades_per_day", "max_daily_loss_quote", "trading_enabled"}


# --- persistence -----------------------------------------------------------
def test_overrides_persist_and_reapply(tmp_path) -> None:
    store_file = tmp_path / "overrides.json"
    overrides = JsonOverrideStore(store_file)
    cfg = Config()
    state = InMemoryRiskStateStore()
    ctrl = SettingsController(cfg, state, overrides=overrides)
    ctrl.set_approval_threshold(123.0)
    ctrl.set_max_trades_per_day(7)
    ctrl.set_buy_valuation_floor(-0.5)
    ctrl.set_force_exit_overvaluation(4.0)

    # a fresh config re-applies the saved overrides (survives "restart")
    fresh = Config()
    JsonOverrideStore(store_file).apply(fresh)
    assert fresh.telegram.approval_threshold_quote == 123.0
    assert fresh.risk.max_trades_per_day == 7
    assert fresh.strategy.buy_valuation_floor_pct == -0.5
    assert fresh.strategy.force_exit_overvaluation_pct == 4.0


def test_pause_persists_disabled_flag(tmp_path) -> None:
    overrides = JsonOverrideStore(tmp_path / "ov.json")
    cfg = Config()
    state = InMemoryRiskStateStore()
    SettingsController(cfg, state, overrides=overrides).pause_trading()
    fresh = Config()
    overrides.apply(fresh)
    assert fresh.app.trading_enabled is False  # no silent resume on restart


# --- status reporter -------------------------------------------------------
async def test_status_report_contains_core_fields() -> None:
    cfg = Config()
    store = InMemoryRiskStateStore()
    store.record_trade(NOW, 30.0)
    ks = KillSwitch(KillSwitchConfig())
    reporter = StatusReporter(cfg, store, kill_switch=ks)
    text = await reporter.build(NOW)
    assert "Status" in text
    assert "trading enabled: True" in text
    assert "today: 1 trades" in text
    assert "floor:" in text
    assert "kill-switch: clear" in text


async def test_status_uses_balance_provider() -> None:
    async def balance() -> tuple[float, float]:
        return (5000.0, 4200.0)

    reporter = StatusReporter(Config(), InMemoryRiskStateStore(), balance_provider=balance)
    text = await reporter.build(NOW)
    assert "equity: 5000.00" in text
    assert "free: 4200.00" in text


async def test_status_survives_a_hanging_balance_provider() -> None:
    # A wedged exchange call must NOT stall /status: it should time out fast and
    # degrade to "balance: unavailable", still returning the rest of the report.
    import asyncio

    async def hangs() -> tuple[float, float]:
        await asyncio.sleep(60)  # never resolves within the test
        return (1.0, 1.0)

    reporter = StatusReporter(
        Config(), InMemoryRiskStateStore(), balance_provider=hangs, balance_timeout_s=0.05
    )
    text = await asyncio.wait_for(reporter.build(NOW), timeout=5.0)
    assert "balance: unavailable" in text
    assert "trading enabled: True" in text  # rest of the report is intact

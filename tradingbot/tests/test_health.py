"""Latency/heartbeat monitor + pipeline auto-suspend tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingbot.config import AppConfig, Config
from tradingbot.app.health import HealthMonitor
from tradingbot.domain import OrderIntent, OrderType, Side
from tradingbot.exchange.models import Ticker
from tradingbot.execution.broker import PaperBroker
from tradingbot.execution.executor import Executor
from tradingbot.execution.pipeline import Outcome, TradingPipeline
from tradingbot.risk.engine import AccountSnapshot, RiskEngine
from tradingbot.risk.state import InMemoryRiskStateStore

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def cfg() -> AppConfig:
    return AppConfig(max_api_latency_ms=2000, max_consecutive_failures=3, health_recovery_samples=3)


class Hooks:
    def __init__(self) -> None:
        self.alerts: list[str] = []
        self.suspends = 0
        self.resumes = 0

    async def alert(self, text: str) -> None:
        self.alerts.append(text)

    async def suspend(self) -> None:
        self.suspends += 1

    async def resume(self) -> None:
        self.resumes += 1


async def test_healthy_pings_never_suspend() -> None:
    mon = HealthMonitor(cfg())
    for _ in range(5):
        await mon.record(50.0)
    assert mon.trading_allowed() is True


async def test_suspends_after_consecutive_failures() -> None:
    h = Hooks()
    mon = HealthMonitor(cfg(), on_alert=h.alert, on_suspend=h.suspend)
    await mon.record(None)
    await mon.record(None)
    assert mon.trading_allowed() is True  # not yet at threshold
    state = await mon.record(None)  # third failure
    assert state.suspended is True
    assert mon.trading_allowed() is False
    assert h.suspends == 1
    assert any("auto-suspended" in a for a in h.alerts)


async def test_high_latency_counts_as_degraded() -> None:
    mon = HealthMonitor(cfg())
    for _ in range(3):
        await mon.record(5000.0)  # over the 2000ms cap
    assert mon.trading_allowed() is False


async def test_resumes_after_recovery_samples() -> None:
    h = Hooks()
    mon = HealthMonitor(cfg(), on_alert=h.alert, on_resume=h.resume)
    for _ in range(3):
        await mon.record(None)
    assert mon.trading_allowed() is False
    await mon.record(10.0)
    await mon.record(10.0)
    assert mon.trading_allowed() is False  # not enough healthy samples yet
    await mon.record(10.0)  # third healthy
    assert mon.trading_allowed() is True
    assert h.resumes == 1


async def test_one_failure_resets_recovery_streak() -> None:
    mon = HealthMonitor(cfg())
    for _ in range(3):
        await mon.record(None)
    await mon.record(10.0)
    await mon.record(None)  # blip resets streak
    await mon.record(10.0)
    await mon.record(10.0)
    assert mon.trading_allowed() is False  # only 2 consecutive healthy after the blip


# --- pipeline integration --------------------------------------------------
class FakeHealth:
    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed

    def trading_allowed(self) -> bool:
        return self._allowed


def build_pipeline(allowed: bool) -> TradingPipeline:
    c = Config(exchange={"symbols_allowlist": ["BTC/USD"]},
               telegram={"approval_threshold_quote": 1000.0})
    store = InMemoryRiskStateStore()
    engine = RiskEngine(c, store)
    executor = Executor(c, PaperBroker(c.fees))
    return TradingPipeline(c, engine, executor, store, health=FakeHealth(allowed))


def snapshot() -> AccountSnapshot:
    return AccountSnapshot(
        equity_quote=3000.0, free_quote=3000.0,
        ticker=Ticker("BTC/USD", bid=99.95, ask=100.05, last=100.0,
                      base_volume=1, quote_volume=1, timestamp=0),
        now=NOW,
    )


def buy() -> OrderIntent:
    return OrderIntent(symbol="BTC/USD", side=Side.BUY, amount=0.4, order_type=OrderType.LIMIT,
                       price=100.0, take_profit_price=102.0, stop_price=99.0, is_entry=True)


async def test_pipeline_suspends_entry_when_unhealthy() -> None:
    res = await build_pipeline(allowed=False).process(buy(), snapshot())
    assert res.outcome is Outcome.SUSPENDED


async def test_pipeline_executes_when_healthy() -> None:
    res = await build_pipeline(allowed=True).process(buy(), snapshot())
    assert res.outcome is Outcome.EXECUTED

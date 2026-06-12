"""Pipeline integration with event-risk / kill-switch posture."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingbot.config import Config, EventConfig
from tradingbot.domain import OrderIntent, OrderType, Side
from tradingbot.events.calendar import EventCalendar
from tradingbot.events.event_risk import EventRiskModule
from tradingbot.events.kill_switch import KillSwitch
from tradingbot.events.posture import PostureProvider
from tradingbot.exchange.models import Ticker
from tradingbot.execution.broker import PaperBroker
from tradingbot.execution.executor import Executor
from tradingbot.execution.pipeline import Outcome, TradingPipeline
from tradingbot.risk.engine import AccountSnapshot, RiskEngine
from tradingbot.risk.state import InMemoryRiskStateStore

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def make_config() -> Config:
    return Config(
        exchange={"symbols_allowlist": ["BTC/USD"], "quote_currency": "USD"},
        telegram={"approval_threshold_quote": 1000.0},  # auto-execute
    )


def event_module(pause_entries: bool, reduce_pct: float = 0.0) -> EventRiskModule:
    cfg = EventConfig(
        enabled=True, pre_event_window_minutes=60, post_event_window_minutes=30,
        pause_entries=pause_entries, reduce_size_pct=reduce_pct,
        calendar=[{"name": "CPI", "timestamp_utc": "2026-06-12T12:00:00Z"}],  # NOW
    )
    return EventRiskModule(cfg, EventCalendar.from_config(cfg))


def build(posture_provider) -> TradingPipeline:
    cfg = make_config()
    store = InMemoryRiskStateStore()
    engine = RiskEngine(cfg, store)
    executor = Executor(cfg, PaperBroker(cfg.fees))
    return TradingPipeline(cfg, engine, executor, store, posture_provider=posture_provider)


def buy(**kw) -> OrderIntent:
    params = dict(symbol="BTC/USD", side=Side.BUY, amount=0.4, order_type=OrderType.LIMIT,
                  price=100.0, take_profit_price=102.0, stop_price=99.0, is_entry=True)
    params.update(kw)
    return OrderIntent(**params)


def snapshot() -> AccountSnapshot:
    return AccountSnapshot(
        equity_quote=3000.0, free_quote=3000.0,
        ticker=Ticker("BTC/USD", bid=99.95, ask=100.05, last=100.0,
                      base_volume=1, quote_volume=1, timestamp=0),
        now=NOW,
    )


async def test_event_window_pauses_entry() -> None:
    provider = PostureProvider(event_module=event_module(pause_entries=True))
    res = await build(provider).process(buy(), snapshot())
    assert res.outcome is Outcome.PAUSED
    assert any("event-risk" in r for r in res.posture_reasons)


async def test_kill_switch_pause_blocks_entry() -> None:
    from tradingbot.config import KillSwitchConfig

    ks = KillSwitch(KillSwitchConfig())
    ks.manual_pause()
    provider = PostureProvider(kill_switch=ks)
    res = await build(provider).process(buy(), snapshot())
    assert res.outcome is Outcome.PAUSED


async def test_event_window_reduces_size_when_not_paused() -> None:
    # pause_entries False, reduce 50% -> entry executes at half size
    provider = PostureProvider(event_module=event_module(pause_entries=False, reduce_pct=50.0))
    res = await build(provider).process(buy(), snapshot())
    assert res.outcome is Outcome.EXECUTED
    assert res.execution.fill.amount == pytest.approx(0.2)  # 0.4 * 0.5


async def test_exit_not_paused_by_posture() -> None:
    # Even when the event window pauses *entries*, an exit must not be PAUSED
    # (it still flows through risk + execution). Use an event config that does
    # not widen spreads so the exit cleanly executes here.
    cfg = EventConfig(
        enabled=True, pre_event_window_minutes=60, post_event_window_minutes=30,
        pause_entries=True, reduce_size_pct=0.0, widen_maker_offset_pct=0.0,
        calendar=[{"name": "CPI", "timestamp_utc": "2026-06-12T12:00:00Z"}],
    )
    provider = PostureProvider(event_module=EventRiskModule(cfg, EventCalendar.from_config(cfg)))
    res = await build(provider).process(
        buy(side=Side.SELL, is_entry=False, take_profit_price=None, stop_price=None),
        snapshot(),
    )
    assert res.outcome is not Outcome.PAUSED  # exits are never paused by posture
    assert res.outcome is Outcome.EXECUTED


async def test_no_provider_behaves_normally() -> None:
    res = await build(None).process(buy(), snapshot())
    assert res.outcome is Outcome.EXECUTED

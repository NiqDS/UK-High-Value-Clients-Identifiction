"""Full pipeline tests: strategy intent → risk → approval → paper execution."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingbot.config import Config
from tradingbot.domain import OrderIntent, OrderType, Side
from tradingbot.exchange.models import Ticker
from tradingbot.execution.executor import Executor
from tradingbot.execution.broker import PaperBroker
from tradingbot.execution.pipeline import Outcome, TradingPipeline, auto_approve
from tradingbot.risk.engine import AccountSnapshot, RiskEngine
from tradingbot.risk.state import InMemoryRiskStateStore

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def make_config(approval_threshold=1000.0) -> Config:
    return Config(
        exchange={"symbols_allowlist": ["BTC/USD"], "quote_currency": "USD"},
        telegram={"approval_threshold_quote": approval_threshold},
    )


def build(approval_threshold=1000.0, approver=None):
    cfg = make_config(approval_threshold)
    store = InMemoryRiskStateStore()
    engine = RiskEngine(cfg, store)
    executor = Executor(cfg, PaperBroker(cfg.fees))
    pipeline = TradingPipeline(cfg, engine, executor, store, approver=approver)
    return pipeline, store


def buy_intent(**kw) -> OrderIntent:
    params = dict(symbol="BTC/USD", side=Side.BUY, amount=0.4,
                  order_type=OrderType.LIMIT, price=100.0,
                  take_profit_price=102.0, stop_price=99.0, is_entry=True)
    params.update(kw)
    return OrderIntent(**params)


def snapshot(**kw) -> AccountSnapshot:
    params = dict(
        equity_quote=3000.0, free_quote=3000.0,
        ticker=Ticker("BTC/USD", bid=99.95, ask=100.05, last=100.0,
                      base_volume=1, quote_volume=1, timestamp=0),
        now=NOW,
    )
    params.update(kw)
    return AccountSnapshot(**params)


async def test_approved_below_threshold_executes_and_counts() -> None:
    pipeline, store = build(approval_threshold=1000.0)  # 40 < 1000 -> auto
    res = await pipeline.process(buy_intent(), snapshot())
    assert res.outcome is Outcome.EXECUTED
    assert res.execution.filled
    assert store.get_daily_counters(NOW).trades == 1  # fill recorded


async def test_rejected_by_risk_is_not_executed() -> None:
    pipeline, store = build()
    res = await pipeline.process(buy_intent(symbol="DOGE/USD"), snapshot())
    assert res.outcome is Outcome.REJECTED_BY_RISK
    assert res.execution is None
    assert store.get_daily_counters(NOW).trades == 0


async def test_requires_approval_held_without_approver() -> None:
    pipeline, store = build(approval_threshold=0.0)  # every trade needs approval
    res = await pipeline.process(buy_intent(), snapshot())
    assert res.outcome is Outcome.PENDING_APPROVAL
    assert store.get_daily_counters(NOW).trades == 0


async def test_requires_approval_auto_approved_executes() -> None:
    pipeline, _ = build(approval_threshold=0.0, approver=auto_approve)
    res = await pipeline.process(buy_intent(), snapshot())
    assert res.outcome is Outcome.EXECUTED


async def test_requires_approval_denied() -> None:
    async def deny(intent, decision) -> bool:
        return False

    pipeline, store = build(approval_threshold=0.0, approver=deny)
    res = await pipeline.process(buy_intent(), snapshot())
    assert res.outcome is Outcome.REJECTED_BY_APPROVAL
    assert store.get_daily_counters(NOW).trades == 0


async def test_emergency_exit_bypasses_approval_and_alerts() -> None:
    # threshold 0 => normally everything needs approval and would be HELD (no
    # approver); an emergency force-exit must execute anyway and fire an alert.
    cfg = make_config(approval_threshold=0.0)
    store = InMemoryRiskStateStore()
    engine = RiskEngine(cfg, store)
    executor = Executor(cfg, PaperBroker(cfg.fees))
    alerts: list[str] = []

    async def alert(text: str) -> None:
        alerts.append(text)

    pipeline = TradingPipeline(cfg, engine, executor, store, approver=None, emergency_alert=alert)
    emergency = OrderIntent(
        symbol="BTC/USD", side=Side.SELL, amount=0.4, order_type=OrderType.MARKET,
        price=100.0, is_entry=False,
        metadata={"emergency": True, "vwap": 95.0, "valuation_pct": 5.2, "valuation": "overvalued"},
    )
    res = await pipeline.process(emergency, snapshot())
    assert res.outcome is Outcome.EXECUTED
    assert res.execution.filled
    assert len(alerts) == 1 and "EMERGENCY" in alerts[0]


async def test_process_signals_handles_multiple() -> None:
    pipeline, _ = build(approval_threshold=1000.0)
    results = await pipeline.process_signals(
        [buy_intent(), buy_intent(symbol="DOGE/USD")], snapshot()
    )
    assert [r.outcome for r in results] == [Outcome.EXECUTED, Outcome.REJECTED_BY_RISK]

"""TradingRunner.run_once integration with fakes (no network/telegram)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingbot.app.portfolio import PositionTracker
from tradingbot.app.runner import TradingRunner
from tradingbot.config import Config, Secrets, Settings
from tradingbot.domain import OrderIntent, OrderType, Side
from tradingbot.exchange.adapter import ExchangeAdapter
from tradingbot.execution.broker import PaperBroker
from tradingbot.execution.executor import Executor
from tradingbot.execution.pipeline import Outcome, TradingPipeline, auto_approve
from tradingbot.risk.engine import RiskEngine
from tradingbot.store import SqliteRiskStateStore, TradeLog, make_session_factory
from tradingbot.strategy.base import MarketData, Strategy

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


class BuyOnceStrategy(Strategy):
    name = "buy_once"

    def generate_signals(self, market: MarketData) -> list[OrderIntent]:
        if market.holding:
            return []
        return [OrderIntent(symbol=market.symbol, side=Side.BUY, amount=0.4,
                            order_type=OrderType.LIMIT, price=100.0,
                            take_profit_price=102.0, stop_price=99.0, is_entry=True,
                            metadata={"valuation_pct": -1.0})]


def build(tmp_path, strategy: Strategy):
    cfg = Config(exchange={"symbols_allowlist": ["BTC/USD"], "quote_currency": "USD"},
                 telegram={"approval_threshold_quote": 1000.0})
    settings = Settings(config=cfg, secrets=Secrets(_env_file=None))
    sf = make_session_factory(f"sqlite:///{tmp_path / 'tb.db'}")
    store = SqliteRiskStateStore(sf)
    trade_log = TradeLog(sf)
    engine = RiskEngine(cfg, store)
    executor = Executor(cfg, PaperBroker(cfg.fees))
    pipeline = TradingPipeline(cfg, engine, executor, store, approver=auto_approve)
    portfolio = PositionTracker()
    runner = TradingRunner(
        settings=settings, adapter=ExchangeAdapter(_FakeClient()), pipeline=pipeline,
        strategy=strategy, store=store, trade_log=trade_log, portfolio=portfolio,
    )
    return runner, trade_log, portfolio


class _FakeClient:
    id = "fake"

    async def load_markets(self, reload=False):
        return {}

    async def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None):
        base = 1_700_000_000_000
        return [[base + i * 60_000, 100, 101, 99, 100, 5.0] for i in range(limit or 50)]

    async def fetch_ticker(self, symbol, params=None):
        return {"symbol": symbol, "bid": 99.95, "ask": 100.05, "last": 100.0,
                "baseVolume": 1, "quoteVolume": 1, "timestamp": 0}

    async def close(self):
        pass


async def test_run_once_executes_and_persists(tmp_path) -> None:
    runner, trade_log, portfolio = build(tmp_path, BuyOnceStrategy())
    results = await runner.run_once("BTC/USD", now=NOW)
    assert any(r.outcome is Outcome.EXECUTED for r in results)
    assert portfolio.holding("BTC/USD") is True
    rows = trade_log.all()
    assert len(rows) == 1 and rows[0].side == "buy" and rows[0].is_entry is True
    assert rows[0].valuation_pct == pytest.approx(-1.0)


async def test_run_once_no_double_entry_when_holding(tmp_path) -> None:
    runner, trade_log, portfolio = build(tmp_path, BuyOnceStrategy())
    await runner.run_once("BTC/USD", now=NOW)
    await runner.run_once("BTC/USD", now=NOW)  # already holding -> no new buy
    assert len(trade_log.all()) == 1


async def test_tp_stop_exit_emission(tmp_path) -> None:
    runner, _, portfolio = build(tmp_path, BuyOnceStrategy())
    portfolio.on_fill("BTC/USD", Side.BUY, 100.0, 0.4, NOW, take_profit=101.0, stop=99.0)
    # take-profit: limit exit
    tp = runner._tp_stop_exits("BTC/USD", last=102.0)
    assert len(tp) == 1 and tp[0].side is Side.SELL and tp[0].order_type is OrderType.LIMIT
    # stop: emergency market exit
    stop = runner._tp_stop_exits("BTC/USD", last=98.0)
    assert stop[0].order_type is OrderType.MARKET and stop[0].metadata.get("emergency") is True
    # in-band: nothing
    assert runner._tp_stop_exits("BTC/USD", last=100.0) == []


async def test_weekly_report_reset_and_market_returns(tmp_path) -> None:
    runner, _, _ = build(tmp_path, BuyOnceStrategy())
    runner._week_first_price = {"BTC/USD": 100.0}
    runner._last_price = {"BTC/USD": 110.0}
    assert runner._market_returns()["BTC/USD"] == pytest.approx(10.0)
    from tradingbot.reporting.weekly import WeeklyReporter
    runner.reporter = WeeklyReporter(runner.trade_log)
    text = await runner.emit_weekly_report(now=NOW)
    assert "Weekly performance report" in text
    assert runner._week_first_price == {"BTC/USD": 110.0}  # benchmark window reset

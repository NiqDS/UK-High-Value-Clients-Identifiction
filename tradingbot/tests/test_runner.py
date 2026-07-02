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


class WrongSizeExitStrategy(Strategy):
    """Emits a channel-style exit sized from notional (like Donchian) — the
    runner must resize it to the actually-held units."""

    name = "wrong_size_exit"

    def generate_signals(self, market: MarketData) -> list[OrderIntent]:
        if not market.holding:
            return []
        return [OrderIntent(symbol=market.symbol, side=Side.SELL, amount=999.0,
                            order_type=OrderType.MARKET, price=100.0, is_entry=False,
                            reason="channel exit")]


async def test_exit_resized_to_held_units(tmp_path) -> None:
    runner, trade_log, portfolio = build(tmp_path, WrongSizeExitStrategy())
    portfolio.on_fill("BTC/USD", Side.BUY, 100.0, 0.4, NOW)  # hold 0.4 units
    results = await runner.run_once("BTC/USD", now=NOW)
    executed = [r for r in results if r.outcome is Outcome.EXECUTED]
    assert len(executed) == 1
    assert executed[0].execution.fill.amount == pytest.approx(0.4)  # not 999
    assert portfolio.holding("BTC/USD") is False  # fully closed


async def test_double_exit_deduped_prefers_emergency(tmp_path) -> None:
    runner, _, portfolio = build(tmp_path, WrongSizeExitStrategy())
    # stop above last price so the stop monitor ALSO fires an emergency exit
    portfolio.on_fill("BTC/USD", Side.BUY, 100.0, 0.4, NOW, stop=100.5)
    results = await runner.run_once("BTC/USD", now=NOW)
    executed = [r for r in results if r.outcome is Outcome.EXECUTED]
    assert len(executed) == 1  # ONE exit, not two
    assert executed[0].intent.metadata.get("emergency") is True  # the stop won
    assert executed[0].execution.fill.amount == pytest.approx(0.4)
    assert portfolio.holding("BTC/USD") is False


async def test_exit_with_no_tracked_position_is_dropped(tmp_path) -> None:
    # restart-gap case: strategy believes it should exit but tracker is empty
    runner, trade_log, portfolio = build(tmp_path, WrongSizeExitStrategy())
    portfolio.on_fill("BTC/USD", Side.BUY, 100.0, 0.4, NOW)
    intents = runner._normalize_exits("ETH/USD", [OrderIntent(
        symbol="ETH/USD", side=Side.SELL, amount=1.0, order_type=OrderType.MARKET,
        price=100.0, is_entry=False, reason="x")])
    assert intents == []


def test_drop_forming_candle() -> None:
    from tradingbot.app.runner import drop_forming_candle, timeframe_ms
    from tradingbot.exchange.models import Candle

    day = timeframe_ms("1d")
    now_ms = 1_700_000_000_000
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    closed = [Candle(now_ms - 3 * day, 1, 1, 1, 1, 1), Candle(now_ms - 2 * day, 1, 1, 1, 1, 1)]
    forming = Candle(now_ms - day // 2, 1, 1, 1, 1, 1)  # started 12h ago, still open
    out = drop_forming_candle(closed + [forming], "1d", now)
    assert out == closed
    # a candle that closed exactly at now is kept
    edge = Candle(now_ms - day, 1, 1, 1, 1, 1)
    assert drop_forming_candle(closed + [edge], "1d", now)[-1] is edge


async def test_snapshot_carries_unrealized_pnl(tmp_path) -> None:
    runner, _, portfolio = build(tmp_path, WrongSizeExitStrategy())
    portfolio.on_fill("BTC/USD", Side.BUY, 90.0, 0.4, NOW)
    runner._last_price["BTC/USD"] = 100.0
    assert runner._unrealized_pnl() == pytest.approx((100.0 - 90.0) * 0.4)


async def test_cancel_stale_orders_cancels_bot_owned(tmp_path) -> None:
    runner, _, _ = build(tmp_path, WrongSizeExitStrategy())
    runner.cfg.app.dry_run = False  # live path
    cancelled: list[str] = []

    class _OrdersClient(_FakeClient):
        async def fetch_open_orders(self, symbol=None, params=None):
            return [
                {"id": "1", "clientOrderId": "tb-stale1"},
                {"id": "2", "clientOrderId": "manual-order"},
                {"id": "3", "info": {"clientOrderId": "tb-stale2"}},
            ]

        async def cancel_order(self, id, symbol=None, params=None):
            cancelled.append(id)
            return {}

    runner.adapter = ExchangeAdapter(_OrdersClient())
    remaining = await runner._cancel_stale_orders("BTC/USD")
    assert cancelled == ["1", "3"]     # both bot-owned orders cancelled
    assert remaining == 1              # the manual order remains, and is counted


async def test_cancel_stale_orders_skipped_in_paper(tmp_path) -> None:
    runner, _, _ = build(tmp_path, WrongSizeExitStrategy())  # dry_run defaults True
    assert await runner._cancel_stale_orders("BTC/USD") == 0


async def test_paper_equity_overrides_synthetic_fallback(tmp_path) -> None:
    # no credentials -> paper run sizes off app.paper_equity when set
    runner, _, _ = build(tmp_path, BuyOnceStrategy())
    runner.cfg.app.paper_equity = 500.0
    equity, free = await runner._equity_free()
    assert equity == pytest.approx(500.0) and free == pytest.approx(500.0)


async def test_paper_equity_unset_uses_floor_fallback(tmp_path) -> None:
    runner, _, _ = build(tmp_path, BuyOnceStrategy())
    runner.cfg.app.paper_equity = 0.0  # unset -> floor*3 fallback
    equity, _ = await runner._equity_free()
    assert equity == pytest.approx(runner.cfg.risk.floor_quote * 3)


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

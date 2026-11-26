"""Expected-safe accounting and crash recovery, not defect confirmation."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
import json
import pytest
from tradingbot.store import make_session_factory, TradeLog, SqliteRiskStateStore
from tradingbot.store.journal import Journal
from tradingbot.execution.journal_broker import JournalBroker, PaperVenue
from tradingbot.execution.models import Order, LiquidityRole, ExecStatus
from tradingbot.domain import OrderType, Side
from tradingbot.config import FeeConfig
from tradingbot.reporting.weekly import WeeklyReporter

NOW = datetime(2026, 6, 12, 12, tzinfo=timezone.utc)


@pytest.fixture
def journal(tmp_path):
    path = tmp_path / "account.db"
    make_session_factory(f"sqlite:///{path}")
    return Journal(path, 206, "USD", clock=lambda: NOW)


def order(key="buy", side=Side.BUY, units=1):
    return Order(
        key, "BTC/USD", side, units, OrderType.MARKET, LiquidityRole.TAKER, 100, 100, stop_price=90
    )


def execution(key="f1", units=1, price=100, fee=1, currency="USD"):
    return dict(
        id=key, units=units, price=price, fee=fee, fee_currency=currency, timestamp=NOW.isoformat()
    )


def complete_buy(j):
    j.prepare(order(), 90)
    j.reconcile("buy", "filled", [execution()])


def test_net_profit_includes_both_fees_and_atomic_reporting(journal):
    complete_buy(journal)
    p = journal.snapshot().get("BTC/USD")
    assert p.units == 1 and p.cost_basis == 101 and p.stop == 90
    assert journal.cash() == 105
    journal.prepare(order("sell", Side.SELL), reason="exit")
    journal.reconcile("sell", "filled", [execution("s1", 1, 110, "1.1")])
    assert journal.cash() == Decimal("213.9")
    sf = make_session_factory(f"sqlite:///{journal.path}")
    stats = WeeklyReporter(TradeLog(sf), net_accounting=True).compute(NOW, NOW)
    assert stats.net_pnl == pytest.approx(7.9)
    assert SqliteRiskStateStore(sf).get_daily_counters(NOW).realized_pnl == pytest.approx(7.9)
    assert stats.fees_paid == pytest.approx(2.1)


def test_base_buy_fee_and_partial_sales_keep_full_cost(journal):
    journal.prepare(order(), 90)
    journal.reconcile("buy", "filled", [execution(currency="BTC", fee=".01")])
    assert journal.cash() == 106
    assert journal.snapshot().get("BTC/USD").units == pytest.approx(0.99)
    journal.prepare(order("sell", Side.SELL, 0.99))
    journal.reconcile("sell", "partial", [execution("s1", ".4", 110, ".044")])
    journal.reconcile(
        "sell",
        "filled",
        [execution("s1", ".400", 110, ".044"), execution("s2", ".59", 110, ".0649")],
    )
    assert journal.cash() == Decimal("214.7911")
    assert not journal.snapshot().positions


def test_base_sell_fee_consumes_additional_units(journal):
    complete_buy(journal)
    journal.prepare(order("sell", Side.SELL, ".99"))
    journal.reconcile("sell", "filled", [execution("s1", ".99", 110, ".01", "BTC")])
    assert not journal.snapshot().positions
    assert journal.cash() == Decimal("213.9")


def test_duplicate_and_conflicting_fill_records(journal):
    complete_buy(journal)
    assert (
        journal.reconcile("buy", "filled", [execution(units="1.000", price="100.0", fee="1.0")])
        == []
    )
    assert journal.cash() == 105
    with pytest.raises(ValueError, match="Conflicting"):
        journal.reconcile("buy", "filled", [execution(price=101)])
    assert journal.cash() == 105


def test_failure_rolls_back_cash_positions_trades_and_counters(journal):
    journal.prepare(order(), 90)

    def crash():
        raise OSError("injected commit failure")

    with pytest.raises(OSError):
        journal.reconcile("buy", "filled", [execution()], fault=crash)
    restored = Journal(journal.path, 999, "USD")
    assert restored.cash() == 206 and not restored.snapshot().positions
    sf = make_session_factory(f"sqlite:///{journal.path}")
    assert TradeLog(sf).all() == []
    assert SqliteRiskStateStore(sf).get_daily_counters(NOW).trades == 0
    restored.reconcile("buy", "filled", [execution()])
    assert restored.cash() == 105
    assert len(TradeLog(sf).all()) == 1


def test_closed_zero_acknowledgement_is_not_a_fill(journal):
    journal.prepare(order(), 90)
    with pytest.raises(ValueError, match="execution records"):
        journal.reconcile("buy", "filled", [])
    assert journal.cash() == 206 and not journal.snapshot().positions
    assert journal.order("buy")["state"] == "prepared"


def test_partial_fill_is_not_complete_and_late_fill_applies_once(journal):
    journal.prepare(order(), 90)
    journal.reconcile("buy", "open", [execution("f1", ".4", 100, ".4")])
    assert journal.order("buy")["state"] == "partial"
    journal.reconcile(
        "buy", "filled", [execution("f1", ".4", 100, ".4"), execution("f2", ".6", 100, ".6")]
    )
    assert journal.snapshot().get("BTC/USD").units == 1
    sf = make_session_factory(f"sqlite:///{journal.path}")
    counters = SqliteRiskStateStore(sf).get_daily_counters(NOW)
    assert counters.trades == 1 and counters.traded_notional == 100


@pytest.mark.parametrize(
    "field,value", [("price", "NaN"), ("units", "Infinity"), ("fee", -1), ("units", 0)]
)
def test_invalid_execution_leaves_order_and_cash_unchanged(journal, field, value):
    journal.prepare(order(), 90)
    e = execution()
    e[field] = value
    with pytest.raises(ValueError):
        journal.reconcile("buy", "filled", [e])
    assert journal.cash() == 206 and not journal.snapshot().positions


def test_unconverted_fee_asset_blocks_accounting(journal):
    journal.prepare(order(), 90)
    with pytest.raises(ValueError, match="conversion"):
        journal.reconcile("buy", "filled", [execution(currency="BNB")])
    assert journal.cash() == 206


class LostReplyVenue:
    def __init__(self):
        self.calls = 0
        self.available = False

    async def submit(self, order, record):
        self.calls += 1
        raise TimeoutError("accepted, reply lost")

    async def lookup(self, record):
        if not self.available:
            return None
        return dict(status="filled", exchange_id="ex1", executions=[execution()])


async def test_timeout_after_acceptance_never_resubmits_on_restart(journal):
    venue = LostReplyVenue()
    broker = JournalBroker(journal, venue)
    with pytest.raises(TimeoutError):
        await broker.place_order(order())
    assert journal.order("buy")["state"] == "unknown"
    restarted = JournalBroker(Journal(journal.path, 206, "USD"), venue)
    with pytest.raises(RuntimeError, match="inconclusive"):
        await restarted.place_order(order())
    venue.available = True
    result = await restarted.place_order(order())
    assert result.accounted and len(result.receipts) == 1 and venue.calls == 1
    assert (await restarted.place_order(order())).status == ExecStatus.SKIPPED
    assert journal.cash() == 105


async def test_paper_recovery_after_fill_before_commit(journal):
    fees = FeeConfig(taker_fee_pct=1)
    venue = PaperVenue(fees, "USD")
    broker = JournalBroker(journal, venue)
    original = journal.reconcile

    def broken(*a, **kw):
        raise OSError("disk failed after simulated execution")

    journal.reconcile = broken
    with pytest.raises(OSError):
        await broker.place_order(order())
    journal.reconcile = original
    recovered = JournalBroker(Journal(journal.path, 206, "USD"), PaperVenue(fees, "USD"))
    await recovered.recover_all()
    assert journal.cash() == 105
    assert journal.snapshot().get("BTC/USD").stop == 90
    assert (await recovered.place_order(order())).status == ExecStatus.SKIPPED


def test_unknown_order_blocks_other_entries(journal):
    journal.prepare(order(), 90)
    journal.reconcile("buy", "unknown")
    second = order("second")
    second.symbol = "ETH/USD"
    with pytest.raises(RuntimeError, match="Pending account"):
        journal.prepare(second, 90)


def test_day_mark_survives_restart(journal):
    assert journal.day_change(NOW, 206) == 0
    restored = Journal(journal.path, 1, "USD")
    assert restored.day_change(NOW, 190) == -16


async def test_protection_acknowledgement_requires_exact_coverage(journal):
    from tradingbot.app.protection import ProtectionManager

    complete_buy(journal)
    venue = AsyncMock()
    venue.ensure_stop.return_value = dict(state="open", id="stop1", units=0.5, stop=90)
    with pytest.raises(RuntimeError, match="not confirmed"):
        await ProtectionManager(journal, venue).ensure("BTC/USD")
    venue.ensure_stop.return_value = dict(state="open", id="stop1", units=1, stop=90)
    await ProtectionManager(journal, venue).ensure("BTC/USD")
    with journal.connection() as db:
        assert (
            db.execute("SELECT protection FROM position_journal").fetchone()[0] == "confirmed:stop1"
        )


def test_second_process_lock_is_rejected(tmp_path):
    from tradingbot.app.instance_lock import InstanceLock

    a, b = InstanceLock(tmp_path / "account.lock"), InstanceLock(tmp_path / "account.lock")
    a.acquire()
    try:
        with pytest.raises(RuntimeError, match="Another runner"):
            b.acquire()
    finally:
        a.release()
    b.acquire()
    b.release()


def test_strict_saved_numeric_state_and_write_failure(tmp_path):
    from tradingbot.app.portfolio import JsonPositionStore, PositionTracker
    from pathlib import Path

    p = tmp_path / "state.json"
    for units, price, stop in [(float("inf"), 100, 90), (1, float("nan"), 90), (1, 100, "bad")]:
        p.write_text(
            json.dumps(
                dict(
                    positions=[
                        dict(
                            symbol="BTC/USD",
                            units=units,
                            entry_price=price,
                            stop=stop,
                            entry_ts=NOW.isoformat(),
                        )
                    ]
                )
            )
        )
        with pytest.raises(ValueError):
            JsonPositionStore(p, strict=True).load()
    with patch.object(Path, "replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            JsonPositionStore(p).save(PositionTracker())


async def test_journal_runner_stop_does_not_depend_on_candle_fetch(tmp_path):
    from tests.test_daily_policy import daily_config
    from tradingbot.config import Settings, Secrets
    from tradingbot.__main__ import _build_runner
    from tradingbot.exchange.models import Ticker

    cfg = daily_config()
    cfg.app.paper_equity = 206
    cfg.exchange.name = "bybit"
    cfg.app.db_url = f"sqlite:///{tmp_path / 'daily.db'}"
    runner, _, _ = _build_runner(Settings(config=cfg, secrets=Secrets(_env_file=None)))
    try:
        j = runner.journal
        j.prepare(order(), 90)
        # Use a timestamp within this prepared order's lifetime.
        row = j.order("buy")
        e = execution(fee=".1")
        e["timestamp"] = json.loads(row["payload"])["created"]
        j.reconcile("buy", "filled", [e])
        runner.portfolio = j.snapshot()
        runner.adapter.fetch_ticker = AsyncMock(
            return_value=Ticker("BTC/USD", 79.9, 80, 80, 1, 1, 0)
        )
        runner.adapter.fetch_ohlcv = AsyncMock(side_effect=RuntimeError("candles unavailable"))
        results = await runner.run_once("BTC/USD", NOW)
        assert not j.snapshot().positions
        assert runner.adapter.fetch_ohlcv.await_count == 0
        assert results[0].execution.accounted
    finally:
        await runner.adapter.close()


async def test_full_paper_runner_restarts_without_duplicate_entry(tmp_path):
    from tests.test_daily_policy import daily_config
    from tradingbot.config import Settings, Secrets
    from tradingbot.__main__ import _build_runner

    cfg = daily_config()
    cfg.app.paper_equity = 206
    cfg.exchange.name = "bybit"
    cfg.app.db_url = f"sqlite:///{tmp_path / 'daily.db'}"
    runner, _, _ = _build_runner(Settings(config=cfg, secrets=Secrets(_env_file=None)))
    o = order()
    o.stop_price = 90
    try:
        result = await runner.pipeline.executor.broker.place_order(o)
        assert result.accounted
        cash = runner.journal.cash()
    finally:
        await runner.adapter.close()
    restored, _, _ = _build_runner(Settings(config=cfg, secrets=Secrets(_env_file=None)))
    try:
        second = await restored.pipeline.executor.broker.place_order(o)
        assert second.status == ExecStatus.SKIPPED
        assert restored.journal.cash() == cash
        assert restored.portfolio.get("BTC/USD").units == 1
    finally:
        await restored.adapter.close()


async def test_approval_delay_rechecks_current_price(tmp_path):
    from tests.test_runner import build, BuyOnceStrategy
    from tradingbot.execution.pipeline import Outcome
    from tradingbot.risk.engine import AccountSnapshot
    from tradingbot.exchange.models import Ticker

    runner, _, _ = build(tmp_path, BuyOnceStrategy())
    runner.cfg.fees.allow_taker_fallback = True
    runner.cfg.fees.require_take_profit = False
    runner.cfg.risk.max_notional_per_trade_quote = 30
    runner.cfg.risk.max_notional_per_trade_pct_equity = 100
    o = __import__("tradingbot.domain", fromlist=["OrderIntent"]).OrderIntent(
        "BTC/USD", Side.BUY, 0.27, OrderType.MARKET, 100, stop_price=90
    )
    first = AccountSnapshot(
        equity_quote=3000,
        free_quote=3000,
        ticker=Ticker("BTC/USD", 100, 100, 100, 1, 1, 0),
        now=NOW,
    )
    later = AccountSnapshot(
        equity_quote=3000,
        free_quote=3000,
        ticker=Ticker("BTC/USD", 149.9, 150, 150, 1, 1, 0),
        now=NOW,
    )
    runner.pipeline.snapshot_provider = AsyncMock(return_value=later)
    runner.pipeline.executor.execute = AsyncMock()
    result = await runner.pipeline.process(o, first)
    assert result.outcome == Outcome.REJECTED_BY_RISK
    runner.pipeline.executor.execute.assert_not_awaited()


def test_archive_failure_never_returns_partial_data(monkeypatch):
    import tradingbot.exchange.binance_vision as bv

    monkeypatch.setattr(bv, "_completed_months", lambda months: [(2020, 1), (2020, 2)])

    def broken(*a, **kw):
        raise OSError("missing archive")

    monkeypatch.setattr(bv.urllib.request, "urlopen", broken)
    with pytest.raises(ValueError, match="Archive unavailable"):
        bv.fetch_klines("BTCUSDT", "1d", 2)


@pytest.mark.parametrize("text", ["0,1,2,0,1,1", "0,1,2,1,nan,1", "bad,row"])
def test_malformed_archive_rows_fail(text):
    from tradingbot.exchange.binance_vision import parse_kline_csv

    with pytest.raises(ValueError):
        parse_kline_csv(text)


def test_invalid_balance_is_not_replaced_with_zero():
    from tradingbot.exchange.adapter import _parse_balance

    with pytest.raises(ValueError):
        _parse_balance({"total": {"USD": "not-a-number"}})


def test_process_death_before_commit_leaves_no_partial_accounting(journal):
    import subprocess
    import sys

    journal.prepare(order(), 90)
    payload = json.dumps(execution())
    code = """
import json,sys,os
from tradingbot.store.journal import Journal
j=Journal(sys.argv[1],206,'USD')
j.reconcile('buy','filled',[json.loads(sys.argv[2])],fault=lambda:os._exit(7))
"""
    result = subprocess.run([sys.executable, "-c", code, journal.path, payload])
    assert result.returncode == 7
    assert journal.cash() == 206 and not journal.snapshot().positions
    sf = make_session_factory(f"sqlite:///{journal.path}")
    assert TradeLog(sf).all() == []
    journal.reconcile("buy", "filled", [execution()])
    assert journal.cash() == 105 and len(TradeLog(sf).all()) == 1


async def test_cancel_ack_without_final_executions_does_not_end_recovery(journal):
    class Venue:
        def __init__(self):
            self.stage = 0

        async def submit(self, order, record):
            return dict(status="open", executions=[])

        async def lookup(self, record):
            self.stage += 1
            return dict(
                status="canceled",
                executions=[] if self.stage == 1 else [execution(units=".4", fee=".4")],
                final=self.stage == 3,
            )

    venue = Venue()
    broker = JournalBroker(journal, venue)
    await broker.place_order(order())
    await broker.recover_all()
    assert journal.pending()
    await broker.recover_all()
    assert journal.snapshot().get("BTC/USD").units == 0.4 and journal.pending()
    await broker.recover_all()
    assert journal.order("buy")["state"] == "canceled" and not journal.pending()
    assert journal.cash() == Decimal("165.6")


def test_partial_closed_ack_still_records_known_fills(journal):
    journal.prepare(order(), 90)
    journal.reconcile("buy", "filled", [execution(units=".4", fee=".4")])
    assert journal.order("buy")["state"] == "partial"
    assert journal.snapshot().get("BTC/USD").units == 0.4


def test_market_rounding_cannot_increase_order_and_universe_must_match():
    from tradingbot.exchange.adapter import ExchangeAdapter
    from types import SimpleNamespace

    client = SimpleNamespace(amount_to_precision=lambda symbol, amount: "0.123")
    adapter = ExchangeAdapter(client)
    adapter._markets_loaded = True
    adapter._markets = {
        "BTC/USDC": dict(
            spot=True,
            active=True,
            quote="USDC",
            precision={"amount": 0.001},
            limits={"cost": {"min": 1}, "amount": {"min": 0.001, "max": 10}},
        )
    }
    adapter.validate_universe(["BTC/USDC"], "USDC")
    assert adapter.round_amount("BTC/USDC", 0.1239) == 0.123
    with pytest.raises(ValueError):
        adapter.round_amount("BTC/USDC", 0.12)
    with pytest.raises(ValueError):
        adapter.validate_universe(["BTC/USDT"], "USDT")


def test_eu_profile_is_paper_usdc_and_public_only(tmp_path):
    from pathlib import Path
    import yaml
    from tradingbot.config import Config, Settings, Secrets
    from tradingbot.__main__ import _build_runner

    cfg = Config(
        **yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "config.bybiteu-daily-paper.yaml").read_text()
        )
    )
    assert cfg.exchange.name == "bybiteu" and cfg.exchange.quote_currency == "USDC"
    assert cfg.app.dry_run and all(s.endswith("/USDC") for s in cfg.exchange.symbols_allowlist)
    cfg.app.db_url = f"sqlite:///{tmp_path / 'eu.db'}"
    # No real adapter is needed for wiring this test.
    from tradingbot.exchange.adapter import ExchangeAdapter
    from tests.test_runner import _FakeClient

    with patch(
        "tradingbot.exchange.factory.build_adapter", return_value=ExchangeAdapter(_FakeClient())
    ) as build_adapter:
        runner, _, _ = _build_runner(Settings(config=cfg, secrets=Secrets(_env_file=None)))
        assert build_adapter.call_args.kwargs["public_only"] is True
        assert runner.journal.quote == "USDC"

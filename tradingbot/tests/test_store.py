"""SQLite persistent store: trade log + state (flag, counters) survive restart."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from tradingbot.store import DecisionLog, SqliteRiskStateStore, TradeLog, make_session_factory


def _url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'tb.db'}"


def test_trade_log_records_risk_pct(tmp_path) -> None:
    log = TradeLog(make_session_factory(_url(tmp_path)))
    log.record(ts=_ts(12), symbol="BTC/USD", side="buy", price=100.0, amount=0.4,
               cost_quote=40.0, fee_quote=0.16, role="maker", is_entry=True, risk_pct=1.7)
    assert log.all()[0].risk_pct == 1.7


def test_decision_log_records_every_decision(tmp_path) -> None:
    dl = DecisionLog(make_session_factory(_url(tmp_path)))
    dl.record(ts=_ts(12), symbol="ADA/USDT", side="buy", is_entry=True,
              outcome="rejected_by_risk", gate="per_trade_risk", approved=False,
              notional=23.0, risk_pct=6.2, reason="risk-to-stop > budget")
    dl.record(ts=_ts(13), symbol="ADA/USDT", side="buy", is_entry=True,
              outcome="executed", gate="ok", approved=True, notional=23.0, risk_pct=0.9)
    rows = dl.all()
    assert len(rows) == 2
    assert rows[0].outcome == "rejected_by_risk" and rows[0].risk_pct == 6.2
    assert rows[1].approved is True and rows[1].source == "live"


def test_migration_adds_risk_pct_to_legacy_trades_table(tmp_path) -> None:
    # simulate a pre-existing db whose 'trades' table lacks risk_pct
    url = _url(tmp_path)
    sf = make_session_factory(url)
    with sf() as s:
        s.execute(text("ALTER TABLE trades DROP COLUMN risk_pct"))
        s.commit()
    # reopening must back-fill the column (no crash) and accept a risk_pct write
    log = TradeLog(make_session_factory(url))
    log.record(ts=_ts(12), symbol="BTC/USD", side="buy", price=100.0, amount=0.4,
               cost_quote=40.0, fee_quote=0.16, role="maker", is_entry=True, risk_pct=2.5)
    assert log.all()[0].risk_pct == 2.5


def _ts(d=12, h=12) -> datetime:
    return datetime(2026, 6, d, h, 0, tzinfo=timezone.utc)


def test_trade_log_record_and_query(tmp_path) -> None:
    sf = make_session_factory(_url(tmp_path))
    log = TradeLog(sf)
    log.record(ts=_ts(12), symbol="BTC/USD", side="buy", price=100.0, amount=0.4,
               cost_quote=40.0, fee_quote=0.16, role="maker", is_entry=True)
    log.record(ts=_ts(13), symbol="BTC/USD", side="sell", price=102.0, amount=0.4,
               cost_quote=40.8, fee_quote=0.16, role="maker", is_entry=False, realized_pnl=0.8)
    assert len(log.all()) == 2
    window = log.between(_ts(12, 0), _ts(12, 23))
    assert len(window) == 1 and window[0].side == "buy"


def test_state_flag_persists_across_restart(tmp_path) -> None:
    url = _url(tmp_path)
    s1 = SqliteRiskStateStore(make_session_factory(url))
    assert s1.is_trading_enabled() is True
    s1.set_trading_enabled(False, "floor breach")
    # "restart": brand-new store on the same DB file
    s2 = SqliteRiskStateStore(make_session_factory(url))
    assert s2.is_trading_enabled() is False
    assert s2.disabled_reason() == "floor breach"


def test_daily_counters_accumulate_and_reset(tmp_path) -> None:
    store = SqliteRiskStateStore(make_session_factory(_url(tmp_path)))
    store.record_trade(_ts(12), 25.0)
    store.record_trade(_ts(12), 30.0)
    store.record_realized_pnl(_ts(12), -4.0)
    c = store.get_daily_counters(_ts(12))
    assert c.trades == 2 and c.traded_notional == 55.0 and c.realized_pnl == -4.0
    # next day is a fresh counter
    assert store.get_daily_counters(_ts(13)).trades == 0


def test_counters_persist_across_restart(tmp_path) -> None:
    url = _url(tmp_path)
    SqliteRiskStateStore(make_session_factory(url)).record_trade(_ts(12), 10.0)
    reloaded = SqliteRiskStateStore(make_session_factory(url))
    assert reloaded.get_daily_counters(_ts(12)).trades == 1

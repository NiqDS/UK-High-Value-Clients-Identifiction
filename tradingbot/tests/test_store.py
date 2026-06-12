"""SQLite persistent store: trade log + state (flag, counters) survive restart."""

from __future__ import annotations

from datetime import datetime, timezone

from tradingbot.store import SqliteRiskStateStore, TradeLog, make_session_factory


def _url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'tb.db'}"


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

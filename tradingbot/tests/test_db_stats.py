"""db-stats rendering over trade + decision records."""

from __future__ import annotations

from datetime import datetime, timezone

from tradingbot.analysis.db_stats import render_db_stats, render_multi_db_stats
from tradingbot.store.models import DecisionRecord, TradeRecord

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def _t(symbol, is_entry, pnl=0.0, fee=0.1):
    return TradeRecord(ts=NOW, symbol=symbol, side="buy" if is_entry else "sell",
                       price=100.0, amount=1.0, cost_quote=100.0, fee_quote=fee,
                       role="taker", is_entry=is_entry, realized_pnl=pnl)


def _d(symbol, is_entry, outcome, approved=False, gate="OK"):
    return DecisionRecord(ts=NOW, symbol=symbol, side="buy" if is_entry else "sell",
                          is_entry=is_entry, outcome=outcome, gate=gate, approved=approved)


def test_empty_db_reports_no_data() -> None:
    out = render_db_stats([], [])
    assert "fills: none yet" in out
    assert "decisions logged: 0" in out


def test_multi_db_stats_labels_each_bucket() -> None:
    daily = [_t("BTC/USDT", True), _t("BTC/USDT", False, pnl=3.0)]
    out = render_multi_db_stats(
        [("DAILY (live)", daily, []), ("4h PAPER", [], [])], quote="USDT"
    )
    assert "━━ DAILY (live) ━━" in out
    assert "━━ 4h PAPER ━━" in out
    # each bucket renders its own body; the empty 4h bucket shows the no-data line
    assert "fills: none yet" in out
    assert out.index("DAILY (live)") < out.index("4h PAPER")  # order preserved


def test_win_rate_and_net_of_fees() -> None:
    trades = [
        _t("BTC/USDT", True, fee=0.5),
        _t("BTC/USDT", False, pnl=10.0, fee=0.5),   # win
        _t("ETH/USDT", True, fee=0.5),
        _t("ETH/USDT", False, pnl=-4.0, fee=0.5),   # loss
    ]
    out = render_db_stats(trades, [], quote="USDT")
    assert "win rate: 50% (1/2)" in out
    assert "realized PnL: +6.00 USDT" in out       # 10 - 4
    assert "fees paid:    2.00 USDT" in out         # 4 x 0.5
    assert "net of fees:  +4.00 USDT" in out        # 6 - 2


def test_per_coin_table_and_gate_breakdown() -> None:
    trades = [_t("BTC/USDT", True), _t("BTC/USDT", False, pnl=5.0)]
    decisions = [
        _d("BTC/USDT", True, "EXECUTED", approved=True),
        _d("ETH/USDT", True, "REJECTED", approved=False, gate="MAX_NOTIONAL"),
        _d("ETH/USDT", True, "REJECTED", approved=False, gate="MAX_NOTIONAL"),
    ]
    out = render_db_stats(trades, decisions, quote="USDT")
    assert "BTC/USDT" in out
    assert "MAX_NOTIONAL=2" in out                  # gate breakdown counts blocks
    assert "by outcome:" in out
    assert "entry decisions: 3" in out
    assert "approved: 1" in out

"""Risk-band analysis: pair entry risk% with exit P&L, bucket by band."""

from __future__ import annotations

from dataclasses import dataclass

from tradingbot.analysis.risk_bands import analyze_risk_bands, render_risk_report


@dataclass
class Rec:
    symbol: str
    is_entry: bool
    risk_pct: float | None = None
    realized_pnl: float = 0.0


def test_pairs_entries_to_exits_and_buckets_by_band() -> None:
    recs = [
        # low-risk winner
        Rec("BTC", True, risk_pct=0.5), Rec("BTC", False, realized_pnl=+10.0),
        # high-risk loser
        Rec("ETH", True, risk_pct=4.0), Rec("ETH", False, realized_pnl=-8.0),
        # another high-risk loser
        Rec("ETH", True, risk_pct=5.0), Rec("ETH", False, realized_pnl=-3.0),
        # med-risk winner
        Rec("ADA", True, risk_pct=2.0), Rec("ADA", False, realized_pnl=+4.0),
    ]
    bands = {b.label: b for b in analyze_risk_bands(recs)}
    assert bands["low"].trades == 1 and bands["low"].net_pnl == 10.0 and bands["low"].win_rate == 100.0
    assert bands["med"].trades == 1 and bands["med"].net_pnl == 4.0
    assert bands["high"].trades == 2 and bands["high"].net_pnl == -11.0 and bands["high"].win_rate == 0.0


def test_fifo_pairing_per_symbol() -> None:
    # two overlapping BTC entries closed by two exits -> FIFO order
    recs = [
        Rec("BTC", True, risk_pct=0.5),
        Rec("BTC", True, risk_pct=6.0),
        Rec("BTC", False, realized_pnl=+5.0),   # closes the 0.5% entry
        Rec("BTC", False, realized_pnl=-9.0),   # closes the 6.0% entry
    ]
    bands = {b.label: b for b in analyze_risk_bands(recs)}
    assert bands["low"].net_pnl == 5.0    # 0.5% entry, closed first (FIFO)
    assert bands["high"].net_pnl == -9.0  # 6.0% entry -> 'high' band (3-8)


def test_band_boundaries() -> None:
    # 3.0 is the low edge of 'high' (inclusive), 1.0 low edge of 'med'
    recs = [
        Rec("A", True, risk_pct=1.0), Rec("A", False, realized_pnl=1.0),   # med
        Rec("B", True, risk_pct=3.0), Rec("B", False, realized_pnl=1.0),   # high
        Rec("C", True, risk_pct=8.0), Rec("C", False, realized_pnl=1.0),   # xhigh
    ]
    bands = {b.label: b for b in analyze_risk_bands(recs)}
    assert bands["med"].trades == 1
    assert bands["high"].trades == 1
    assert bands["xhigh"].trades == 1


def test_entries_without_risk_or_unpaired_are_ignored() -> None:
    recs = [
        Rec("BTC", True, risk_pct=None), Rec("BTC", False, realized_pnl=+5.0),  # no risk -> skip
        Rec("ETH", True, risk_pct=2.0),                                          # still open
        Rec("XRP", False, realized_pnl=-1.0),                                    # exit w/o entry
    ]
    total = sum(b.trades for b in analyze_risk_bands(recs))
    assert total == 0


def test_render_names_worst_band() -> None:
    recs = [
        Rec("BTC", True, risk_pct=0.5), Rec("BTC", False, realized_pnl=+10.0),
        Rec("ETH", True, risk_pct=5.0), Rec("ETH", False, realized_pnl=-20.0),
    ]
    report = render_risk_report(analyze_risk_bands(recs), quote="USDT")
    assert "Risk-band analysis" in report
    assert "Worst band: **high**" in report
    assert "Best band: **low**" in report


def test_render_handles_empty_history() -> None:
    report = render_risk_report(analyze_risk_bands([]))
    assert "No paired entry/exit trades yet" in report

"""Weekly learning loop: tolerant log ingestion + evidence-driven assessment."""

from __future__ import annotations

import json

from tradingbot.learning.loop import assess, paired_samples_from_db, render_learning_report
from tradingbot.learning.samples import (
    TradeSample, append_own_loss, parse_text, scan_folder,
)


# --- tolerant parsing ------------------------------------------------------
def test_parse_jsonl_with_aliases() -> None:
    text = "\n".join([
        json.dumps({"pair": "BTC/USDT", "direction": "buy", "profit": -3.5, "risk": 4.2}),
        json.dumps({"ticker": "ETH/USDT", "net_pnl": 5.0, "risk_pct": 0.8}),
    ])
    rows = parse_text(text, "ext.jsonl")
    assert len(rows) == 2
    assert rows[0].symbol == "BTC/USDT" and rows[0].pnl == -3.5 and rows[0].risk_pct == 4.2
    assert rows[1].symbol == "ETH/USDT" and rows[1].pnl == 5.0


def test_parse_csv_with_dollar_and_percent() -> None:
    text = "symbol,pnl,risk%\nDOGE/USDT,$-2.00,6%\nADA/USDT,$1.50,1.0%\n"
    rows = parse_text(text, "ext.csv")
    assert rows[0].symbol == "DOGE/USDT" and rows[0].pnl == -2.0 and rows[0].risk_pct == 6.0
    assert rows[1].pnl == 1.5


def test_parse_json_array_and_trades_key() -> None:
    arr = parse_text(json.dumps([{"symbol": "X", "pnl": 1.0}]), "a.json")
    wrapped = parse_text(json.dumps({"trades": [{"symbol": "Y", "pnl": -1.0}]}), "b.json")
    assert arr[0].symbol == "X" and wrapped[0].symbol == "Y"


def test_source_field_overrides_filename() -> None:
    rows = parse_text(json.dumps({"symbol": "X", "pnl": 1.0, "bot": "competitorBot"}), "file.json")
    assert rows[0].source == "competitorBot"
    rows2 = parse_text(json.dumps({"symbol": "X", "pnl": 1.0}), "file.json")
    assert rows2[0].source == "file.json"  # falls back to filename


def test_garbage_rows_skipped_not_fatal() -> None:
    assert parse_text("not json, not csv header\n@@@@", "junk.txt") == []
    assert parse_text("", "empty.csv") == []


# --- folder scan + manifest ------------------------------------------------
def test_scan_only_new_files(tmp_path) -> None:
    (tmp_path / "a.jsonl").write_text(json.dumps({"symbol": "A", "pnl": -1.0}))
    s1, p1 = scan_folder(tmp_path, only_new=True)
    assert p1 == ["a.jsonl"] and len(s1) == 1
    # second scan: nothing new
    s2, p2 = scan_folder(tmp_path, only_new=True)
    assert p2 == [] and s2 == []
    # a new drop is picked up
    (tmp_path / "b.csv").write_text("symbol,pnl\nB,2.0\n")
    _, p3 = scan_folder(tmp_path, only_new=True)
    assert p3 == ["b.csv"]
    # --all re-reads everything
    s4, p4 = scan_folder(tmp_path, only_new=False)
    assert set(p4) == {"a.jsonl", "b.csv"} and len(s4) == 2


def test_append_own_loss_roundtrips(tmp_path) -> None:
    append_own_loss(tmp_path, symbol="BTC/USDT", side="sell", entry_price=100.0,
                    exit_price=90.0, pnl=-2.3, risk_pct=1.1, reason="channel exit",
                    ts="2026-06-15T00:00:00+00:00", bucket="2026W24")
    f = tmp_path / "own_losses_2026W24.jsonl"
    assert f.exists()
    rows = parse_text(f.read_text(), f.name)
    assert rows[0].source == "live" and rows[0].pnl == -2.3 and rows[0].is_loss


# --- assessment ------------------------------------------------------------
class _R:
    def __init__(self, symbol, is_entry, risk_pct=None, realized_pnl=0.0, price=100.0,
                 reason="", ts="2026-06-15"):
        self.symbol, self.is_entry, self.risk_pct = symbol, is_entry, risk_pct
        self.realized_pnl, self.price, self.reason, self.ts = realized_pnl, price, reason, ts


def test_paired_samples_from_db() -> None:
    recs = [_R("BTC", True, risk_pct=0.5), _R("BTC", False, realized_pnl=5.0),
            _R("ETH", True, risk_pct=4.0), _R("ETH", False, realized_pnl=-8.0)]
    samples = paired_samples_from_db(recs)
    assert len(samples) == 2
    assert samples[0].risk_pct == 0.5 and samples[0].pnl == 5.0 and samples[0].source == "live"


def test_assess_flags_losing_high_risk_band() -> None:
    own = [TradeSample("live", symbol="X", pnl=p, risk_pct=4.5) for p in (-2, -3, -1, -4, -2)]
    r = assess(own, [], min_trades=5)
    text = render_learning_report(r)
    assert "Risk band **high**" in text
    assert "TRIMMING position size" in text


def test_assess_external_outperformance_flagged() -> None:
    own = [TradeSample("live", symbol="X", pnl=-1.0, risk_pct=2.0) for _ in range(5)]
    ext = [TradeSample("rivalBot", symbol="X", pnl=+2.0, risk_pct=2.0) for _ in range(5)]
    r = assess(own, ext, min_trades=5)
    text = render_learning_report(r)
    assert "rivalBot" in text and "outperformed ours" in text


def test_assess_quiet_when_insufficient_data() -> None:
    r = assess([TradeSample("live", symbol="X", pnl=-1.0, risk_pct=2.0)], [], min_trades=5)
    text = render_learning_report(r)
    assert "keep accumulating" in text or "nothing to change" in text

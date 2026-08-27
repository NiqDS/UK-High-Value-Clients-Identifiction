"""At-a-glance DB stats for the paper/live trade + decision logs.

Pure functions over the record lists so they're trivially testable — the CLI
just loads the logs and hands them in. Answers the questions you actually ask
while a run accumulates data:
  * how much has piled up (fills, decisions),
  * are entries getting through or being gated (and by WHICH gate),
  * per-coin fills / realized PnL / win rate.

Note on PnL: TradeRecord.realized_pnl is the position's realized PnL booked on
the EXIT fill (gross of that exit's fee); fees are summed separately so the
"net" line is honest. This is a monitoring view, not the audited weekly report.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..store.models import DecisionRecord, TradeRecord


def _fmt_pct(n: int, d: int) -> str:
    return f"{(n / d * 100.0):.0f}%" if d else "-"


def render_multi_db_stats(
    sections: list[tuple[str, list[TradeRecord], list[DecisionRecord]]],
    quote: str = "USDT",
) -> str:
    """Render several labelled buckets into one /report body.

    ``sections`` is a list of ``(label, trades, decisions)``. Each is rendered
    with :func:`render_db_stats` under a clear ``━━ LABEL ━━`` header so the live
    daily bucket and the 4h paper bucket read as distinct blocks in one message.
    """
    blocks: list[str] = []
    for label, trades, decisions in sections:
        body = render_db_stats(trades, decisions, quote)
        blocks.append(f"━━ {label} ━━\n{body}")
    return "\n\n".join(blocks)


def render_db_stats(
    trades: list[TradeRecord], decisions: list[DecisionRecord], quote: str = "USDT",
) -> str:
    lines: list[str] = ["# DB stats"]

    # ---- fills -------------------------------------------------------------
    entries = [t for t in trades if t.is_entry]
    exits = [t for t in trades if not t.is_entry]
    fees = sum(t.fee_quote for t in trades)
    realized = sum(t.realized_pnl for t in exits)
    wins = sum(1 for t in exits if t.realized_pnl > 0)
    if not trades:
        lines.append("fills: none yet — no entries have filled.")
    else:
        span = f"{trades[0].ts:%Y-%m-%d %H:%M} → {trades[-1].ts:%Y-%m-%d %H:%M} UTC"
        lines += [
            f"fills:        {len(trades)}  ({len(entries)} entries, {len(exits)} exits)",
            f"window:       {span}",
            f"closed exits: {len(exits)}  |  win rate: {_fmt_pct(wins, len(exits))} "
            f"({wins}/{len(exits)})",
            f"realized PnL: {realized:+.2f} {quote} (gross of exit fee)",
            f"fees paid:    {fees:.2f} {quote}",
            f"net of fees:  {realized - fees:+.2f} {quote}",
        ]

    # ---- per-coin ----------------------------------------------------------
    if trades:
        per: dict[str, dict[str, float]] = defaultdict(
            lambda: {"entries": 0, "exits": 0, "pnl": 0.0, "wins": 0}
        )
        for t in trades:
            row = per[t.symbol]
            if t.is_entry:
                row["entries"] += 1
            else:
                row["exits"] += 1
                row["pnl"] += t.realized_pnl
                if t.realized_pnl > 0:
                    row["wins"] += 1
        lines += ["", "coin       | entries | exits | win% | realized PnL"]
        for sym in sorted(per):
            r = per[sym]
            ex = int(r["exits"])
            lines.append(f"{sym:10s} | {int(r['entries']):7d} | {ex:5d} | "
                         f"{_fmt_pct(int(r['wins']), ex):>4s} | {r['pnl']:+.2f} {quote}")

    # ---- decisions ---------------------------------------------------------
    lines += ["", f"decisions logged: {len(decisions)}"]
    if decisions:
        by_outcome = Counter(d.outcome for d in decisions)
        lines.append("by outcome:  " + ", ".join(
            f"{k}={v}" for k, v in by_outcome.most_common()))
        # which gates blocked entries (only non-empty gates on un-approved entries)
        blocked = Counter(
            d.gate for d in decisions
            if d.is_entry and not d.approved and d.gate and d.gate != "OK")
        if blocked:
            lines.append("entry blocks: " + ", ".join(
                f"{k}={v}" for k, v in blocked.most_common()))
        entries_seen = sum(1 for d in decisions if d.is_entry)
        approved = sum(1 for d in decisions if d.is_entry and d.approved)
        lines.append(f"entry decisions: {entries_seen}  |  approved: {approved} "
                     f"({_fmt_pct(approved, entries_seen)})")

    return "\n".join(lines)

"""Risk-band analysis: does the algo lose more on higher-risk entries?

Pairs each executed ENTRY (which carries ``risk_pct`` = % of equity risked to the
stop) with the exit that closed it (FIFO, one position per symbol) and attributes
the exit's realized P&L back to the entry's risk band. The output tells you which
risk levels actually lose money on real trades — the input for risk-based sizing
(size up the bands that win, size down the bands that bleed).

Pure over a list of trade records so it is unit-testable without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BandStats:
    label: str
    lo: float
    hi: float
    trades: int = 0
    wins: int = 0
    net_pnl: float = 0.0
    risk_pcts: list[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades * 100.0 if self.trades else 0.0

    @property
    def avg_pnl(self) -> float:
        return self.net_pnl / self.trades if self.trades else 0.0

    @property
    def avg_risk(self) -> float:
        return sum(self.risk_pcts) / len(self.risk_pcts) if self.risk_pcts else 0.0


# default risk bands (% of equity risked to the stop)
DEFAULT_BANDS: list[tuple[str, float, float]] = [
    ("low",  0.0, 1.0),
    ("med",  1.0, 3.0),
    ("high", 3.0, 8.0),
    ("xhigh", 8.0, float("inf")),
]


def _band_for(risk_pct: float, bands: list[BandStats]) -> BandStats | None:
    for b in bands:
        if b.lo <= risk_pct < b.hi:
            return b
    return None


def analyze_risk_bands(records, bands=DEFAULT_BANDS) -> list[BandStats]:
    """``records`` = trade rows (any object with .symbol, .is_entry, .risk_pct,
    .realized_pnl, ordered by time). Returns per-band stats.

    Pairing: for each symbol, FIFO-match entries to the exits that follow them;
    the exit's realized_pnl is attributed to the paired entry's risk band. Unpaired
    entries (still open) and exits without a matching entry are ignored."""
    out = [BandStats(lbl, lo, hi) for lbl, lo, hi in bands]
    open_entries: dict[str, list] = {}
    for r in records:
        if r.is_entry:
            if r.risk_pct is not None:
                open_entries.setdefault(r.symbol, []).append(r)
        else:  # exit — pair with the oldest open entry for this symbol
            queue = open_entries.get(r.symbol)
            if not queue:
                continue
            entry = queue.pop(0)
            band = _band_for(entry.risk_pct, out)
            if band is None:
                continue
            band.trades += 1
            band.wins += 1 if (r.realized_pnl or 0.0) > 0 else 0
            band.net_pnl += r.realized_pnl or 0.0
            band.risk_pcts.append(entry.risk_pct)
    return out


def render_risk_report(bands: list[BandStats], quote: str = "USDT") -> str:
    lines = [
        "# Risk-band analysis (paired entry risk% -> exit P&L)",
        "",
        "band  | risk% range | avg risk% | trades | win% | net P&L | avg P&L",
    ]
    active = [b for b in bands if b.trades]
    for b in bands:
        hi = "inf" if b.hi == float("inf") else f"{b.hi:.0f}"
        lines.append(
            f"{b.label:5s} | {b.lo:4.1f}-{hi:<4} | {b.avg_risk:8.2f} | {b.trades:6d} | "
            f"{b.win_rate:4.0f} | {b.net_pnl:+8.2f} | {b.avg_pnl:+7.3f}")
    lines += ["", "### Read"]
    if not active:
        lines.append("- No paired entry/exit trades yet — accumulate live history first.")
        return "\n".join(lines)
    worst = min(active, key=lambda b: b.avg_pnl)
    best = max(active, key=lambda b: b.avg_pnl)
    lines.append(f"- Worst band: **{worst.label}** ({worst.lo:.1f}-"
                 f"{'inf' if worst.hi == float('inf') else f'{worst.hi:.0f}'}% risk) — "
                 f"avg {worst.avg_pnl:+.3f} {quote}/trade, {worst.win_rate:.0f}% win.")
    lines.append(f"- Best band: **{best.label}** — avg {best.avg_pnl:+.3f} {quote}/trade, "
                 f"{best.win_rate:.0f}% win.")
    lines.append("- Use this to size: lean into bands that win, trim size on bands that "
                 "bleed. Needs enough trades per band to trust (few => noise).")
    return "\n".join(lines)

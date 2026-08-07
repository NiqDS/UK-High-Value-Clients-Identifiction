"""The weekly learning loop: assess own + external trades, propose adjustments.

Honest by construction. With only a few daily trades a week, black-box RL would
fit noise (the same overfitting trap that ruled out the SMA/momentum gates), so
this does structured, evidence-weighted assessment instead:

  1. pool the window's own trades (DB) with samples from the bad-trades folder
     (own losing trades + uploaded external-bot logs);
  2. attribute P&L by risk band, by symbol, and by exit reason;
  3. where a bucket has ENOUGH trades to trust and loses money, emit a CANDIDATE
     adjustment (trim size in that risk band, review that coin, ...) tagged with
     the evidence and a sample-size confidence note.

Candidates are advisory — printed / delivered to Telegram, never auto-applied.
The config change they imply must be backtested first, like everything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis.risk_bands import BandStats, analyze_risk_bands, render_risk_report
from .samples import TradeSample


@dataclass
class _Bucket:
    key: str
    trades: int = 0
    wins: int = 0
    net: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades * 100.0 if self.trades else 0.0

    @property
    def avg(self) -> float:
        return self.net / self.trades if self.trades else 0.0


@dataclass
class LearningReport:
    n_own: int
    n_external: int
    bands: list[BandStats]
    by_symbol: list[_Bucket]
    by_reason: list[_Bucket]
    by_source: list[_Bucket]
    candidates: list[str]
    min_trades: int
    quote: str = "USDT"


def _bucketize(pairs: list[tuple[str, float]]) -> list[_Bucket]:
    """pairs = (key, pnl). Returns per-key buckets sorted worst-avg first."""
    by: dict[str, _Bucket] = {}
    for key, pnl in pairs:
        b = by.setdefault(key or "?", _Bucket(key or "?"))
        b.trades += 1
        b.wins += 1 if pnl > 0 else 0
        b.net += pnl
    return sorted(by.values(), key=lambda b: b.avg)


def _paired_trade_records(records) -> list:
    """Adapter: risk_bands.analyze_risk_bands wants objects with .symbol/.is_entry/
    .risk_pct/.realized_pnl. External samples are already 'closed trades', so wrap
    each as a synthetic entry+exit pair carrying the risk% and pnl."""
    class _R:
        def __init__(self, symbol, is_entry, risk_pct, realized_pnl):
            self.symbol = symbol
            self.is_entry = is_entry
            self.risk_pct = risk_pct
            self.realized_pnl = realized_pnl
    out = []
    for s in records:
        if s.pnl is None:
            continue
        out.append(_R(s.symbol, True, s.risk_pct, 0.0))
        out.append(_R(s.symbol, False, None, s.pnl))
    return out


def paired_samples_from_db(records) -> list[TradeSample]:
    """Pair DB trade rows (entries with risk_pct, exits with realized_pnl) into
    closed-trade samples tagged source='live'. FIFO per symbol, matching the
    risk-band pairing. This is the authoritative own-trade feed (all wins AND
    losses), unlike the folder's own_losses record which holds only losers."""
    open_entries: dict[str, list] = {}
    out: list[TradeSample] = []
    for r in records:
        if r.is_entry:
            open_entries.setdefault(r.symbol, []).append(r)
        else:
            queue = open_entries.get(r.symbol)
            entry = queue.pop(0) if queue else None
            out.append(TradeSample(
                source="live", symbol=r.symbol, side="buy",
                entry_price=(entry.price if entry else None), exit_price=r.price,
                pnl=r.realized_pnl, risk_pct=(entry.risk_pct if entry else None),
                reason=r.reason, ts=r.ts.isoformat() if hasattr(r.ts, "isoformat") else str(r.ts),
            ))
    return out


def assess(
    own: list[TradeSample], external: list[TradeSample], min_trades: int = 5,
    quote: str = "USDT",
) -> LearningReport:
    combined = own + external
    closed = [s for s in combined if s.pnl is not None]

    bands = analyze_risk_bands(_paired_trade_records(combined))
    by_symbol = _bucketize([(s.symbol, s.pnl) for s in closed])
    by_reason = _bucketize([(s.reason or "unspecified", s.pnl) for s in closed])
    by_source = _bucketize([(s.source, s.pnl) for s in closed])

    candidates: list[str] = []

    # 1. risk bands that lose with enough trades to trust
    for b in bands:
        if b.trades >= min_trades and b.avg_pnl < 0:
            candidates.append(
                f"Risk band **{b.label}** ({b.lo:.1f}-"
                f"{'inf' if b.hi == float('inf') else f'{b.hi:.0f}'}% risk) loses "
                f"{b.avg_pnl:+.3f} {quote}/trade over {b.trades} trades ({b.win_rate:.0f}% win) "
                f"-> consider TRIMMING position size in this band (backtest first)."
            )
    # 2. coins that repeatedly lose
    for b in by_symbol:
        if b.trades >= min_trades and b.avg < 0:
            candidates.append(
                f"Symbol **{b.key}** loses {b.avg:+.3f} {quote}/trade over {b.trades} trades "
                f"({b.win_rate:.0f}% win) -> review whether it is trending; consider excluding."
            )
    # 3. exit reasons dominating losses
    for b in by_reason:
        if b.trades >= min_trades and b.avg < 0 and b.key not in ("", "unspecified"):
            candidates.append(
                f"Exit reason **{b.key}** is loss-heavy ({b.avg:+.3f} {quote}/trade, "
                f"{b.trades} trades) -> inspect that exit path."
            )
    # 4. our vs external comparison (only if both present with enough data)
    srcs = {b.key: b for b in by_source}
    ext = [b for k, b in srcs.items() if k != "live" and b.trades >= min_trades]
    ours = srcs.get("live")
    if ours and ours.trades >= min_trades and ext:
        best_ext = max(ext, key=lambda b: b.avg)
        if best_ext.avg > ours.avg:
            candidates.append(
                f"External source **{best_ext.key}** outperformed ours "
                f"({best_ext.avg:+.3f} vs {ours.avg:+.3f} {quote}/trade) -> study its "
                f"entries/exits in the overlapping conditions."
            )
    if not candidates:
        n = len(closed)
        candidates.append(
            f"No bucket reached {min_trades} trades with a negative edge "
            f"({n} closed trade(s) assessed) -> keep accumulating; nothing to change yet."
        )

    return LearningReport(
        n_own=len([s for s in own if s.pnl is not None]),
        n_external=len([s for s in external if s.pnl is not None]),
        bands=bands, by_symbol=by_symbol, by_reason=by_reason, by_source=by_source,
        candidates=candidates, min_trades=min_trades, quote=quote,
    )


def render_learning_report(r: LearningReport) -> str:
    lines = [
        "# Weekly learning assessment",
        f"own closed trades: {r.n_own} | external: {r.n_external} | "
        f"min trades/bucket to trust: {r.min_trades}",
        "",
        render_risk_report(r.bands, quote=r.quote),
        "",
        "## Loss attribution by symbol (worst first)",
        "symbol | trades | win% | net | avg",
    ]
    for b in r.by_symbol[:10]:
        lines.append(f"{b.key:8s} | {b.trades:6d} | {b.win_rate:4.0f} | {b.net:+8.2f} | {b.avg:+7.3f}")
    lines += ["", "## Candidate adjustments (advisory — backtest before applying)"]
    for i, c in enumerate(r.candidates, 1):
        lines.append(f"{i}. {c}")
    lines += ["", "_These are hypotheses from limited live data, not auto-applied changes. "
              "Validate any parameter change in the backtest harness before adopting._"]
    return "\n".join(lines)

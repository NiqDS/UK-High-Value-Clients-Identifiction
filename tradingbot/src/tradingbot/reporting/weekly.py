"""Weekly trades-log / performance report.

Summarises the week's executed trades from the persistent trade log and
benchmarks realised performance against the market (buy-and-hold % move of each
traded symbol over the same window), so the algorithm can be assessed and
tuned. Generated on a weekly cadence by the runner (and on demand via the
``weekly-report`` CLI command).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..store.trade_log import TradeLog


@dataclass
class WeeklyStats:
    start: datetime
    end: datetime
    num_fills: int
    num_exits: int
    realized_pnl: float
    fees_paid: float
    net_pnl: float
    win_rate_pct: float
    per_symbol_net: dict[str, float]
    market_returns_pct: dict[str, float]


class WeeklyReporter:
    def __init__(self, trade_log: TradeLog) -> None:
        self.trade_log = trade_log

    def compute(
        self, start: datetime, end: datetime, market_returns_pct: dict[str, float] | None = None
    ) -> WeeklyStats:
        trades = self.trade_log.between(start, end)
        realized = sum(t.realized_pnl for t in trades)
        fees = sum(t.fee_quote for t in trades)
        exits = [t for t in trades if not t.is_entry]
        wins = sum(1 for t in exits if (t.realized_pnl - t.fee_quote) > 0)
        per_symbol: dict[str, float] = {}
        for t in trades:
            per_symbol[t.symbol] = per_symbol.get(t.symbol, 0.0) + t.realized_pnl - t.fee_quote
        return WeeklyStats(
            start=start, end=end, num_fills=len(trades), num_exits=len(exits),
            realized_pnl=realized, fees_paid=fees, net_pnl=realized - fees,
            win_rate_pct=(wins / len(exits) * 100.0) if exits else 0.0,
            per_symbol_net=per_symbol,
            market_returns_pct=market_returns_pct or {},
        )

    def render(self, stats: WeeklyStats, quote: str = "USD") -> str:
        lines = [
            "🗓 *Weekly performance report*",
            f"window: {stats.start.date()} → {stats.end.date()}",
            f"fills: {stats.num_fills} ({stats.num_exits} exits)",
            f"realized P&L: {stats.realized_pnl:+.2f} {quote}",
            f"fees paid: {stats.fees_paid:.2f} {quote}",
            f"NET P&L (after fees): {stats.net_pnl:+.2f} {quote}",
            f"win rate (closed): {stats.win_rate_pct:.1f}%",
        ]
        if stats.per_symbol_net:
            lines.append("per-symbol net:")
            for sym, net in sorted(stats.per_symbol_net.items()):
                mkt = stats.market_returns_pct.get(sym)
                bench = f"  vs market {mkt:+.2f}%" if mkt is not None else ""
                lines.append(f"  {sym}: {net:+.2f} {quote}{bench}")
        lines.append("")
        lines.append("> Assess vs the market benchmark; if the strategy trails "
                     "buy-and-hold net of fees, tune the algorithm.")
        return "\n".join(lines)

    def generate(
        self, now: datetime | None = None, days: int = 7,
        market_returns_pct: dict[str, float] | None = None, quote: str = "USD",
    ) -> str:
        now = now or datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        return self.render(self.compute(start, now, market_returns_pct), quote)

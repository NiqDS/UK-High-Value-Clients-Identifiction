"""Does trading NEAR a scheduled macro event (FOMC etc.) hurt the strategy?

Evidence before intuition. We tag each DAILY backtest trade by how close its
ENTRY fell to the nearest event, split the trades into NEAR (within ±window
days) and AWAY, and compare the two with the same expectancy lens used
everywhere else. Then we run the counterfactual — "skip every near-event
entry" — and show what it would do to the overall edge.

The point is to settle whether a "pause around events" rule is worth adding, or
whether it's another intuitive-but-harmful filter like the 200-day SMA gate.
A rule is only justified if near-event trades are MATERIALLY worse AND there are
enough of them to trust — otherwise leave the strategy alone.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..config import StrategyConfig
from ..exchange.models import Candle
from ..backtest.engine import BacktestConfig, Trade
from ..events.calendar import ScheduledEvent
from .backtest_learn import _ret_pct, _stats, run_backtest_trades


def _entry_date(t: Trade):
    return datetime.fromtimestamp(t.entry_ts / 1000, tz=timezone.utc).date()


def _min_days_to_event(d, event_dates: list) -> int:
    return min((abs((d - ed).days) for ed in event_dates), default=10**9)


def render_event_proximity_report(
    tagged: list[tuple[str, Trade]], events: list[ScheduledEvent], window_days: int,
    base: StrategyConfig, bt: BacktestConfig, label: str = "", min_trades: int = 15,
) -> str:
    event_dates = sorted({e.timestamp.date() for e in events})
    near: list[float] = []
    away: list[float] = []
    for _sym, t in tagged:
        r = _ret_pct(t)
        if _min_days_to_event(_entry_date(t), event_dates) <= window_days:
            near.append(r)
        else:
            away.append(r)

    all_stats = _stats(near + away)
    near_s = _stats(near)
    away_s = _stats(away)

    def _payoff(p: float) -> str:
        return "inf" if p == float("inf") else f"{p:.2f}"

    lines = [
        f"# Event-proximity backtest — {label}".rstrip(),
        f"entry {base.donchian_entry_period} / exit {base.donchian_exit_period} | "
        f"{len(event_dates)} events | NEAR = entry within ±{window_days} days of an event",
        "",
        "bucket           | trades | win% | payoff | expectancy% | sum%",
        f"NEAR event       | {near_s.n:6d} | {near_s.win_rate:4.0f} | {_payoff(near_s.payoff):>6s} | "
        f"{near_s.expectancy:+11.3f} | {near_s.total:+.1f}",
        f"AWAY from events | {away_s.n:6d} | {away_s.win_rate:4.0f} | {_payoff(away_s.payoff):>6s} | "
        f"{away_s.expectancy:+11.3f} | {away_s.total:+.1f}",
        f"ALL              | {all_stats.n:6d} | {all_stats.win_rate:4.0f} | {_payoff(all_stats.payoff):>6s} | "
        f"{all_stats.expectancy:+11.3f} | {all_stats.total:+.1f}",
    ]

    # counterfactual: skip the near-event entries -> you're left with AWAY only
    lines += ["", "## Counterfactual — skip every near-event entry",
              f"- removes {near_s.n} of {all_stats.n} trades "
              f"({(near_s.n / all_stats.n * 100 if all_stats.n else 0):.0f}%).",
              f"- per-trade expectancy: {all_stats.expectancy:+.3f}% -> {away_s.expectancy:+.3f}%.",
              f"- near-event trades collectively { 'LOST' if near_s.total < 0 else 'MADE' } "
              f"{near_s.total:+.1f}% (sum of their returns)."]

    # verdict
    lines += ["", "## Verdict"]
    if near_s.n < min_trades:
        lines.append(f"- **Not enough near-event trades** ({near_s.n} < {min_trades}) to conclude. "
                     f"No evidence for an event rule; leave the strategy as-is.")
    elif near_s.total < 0 and near_s.expectancy < away_s.expectancy - 2.0:
        lines.append(f"- **Near-event entries are materially worse** (expectancy {near_s.expectancy:+.2f}% "
                     f"vs {away_s.expectancy:+.2f}% away) AND collectively lose. An entry pause around "
                     f"events is WORTH TESTING further (walk-forward / out-of-sample before adopting).")
    elif near_s.expectancy < away_s.expectancy - 2.0:
        lines.append(f"- Near-event entries are somewhat weaker (expectancy {near_s.expectancy:+.2f}% vs "
                     f"{away_s.expectancy:+.2f}%) but still profitable — skipping them raises the average "
                     f"while losing real winners. Marginal; the built-in protections likely suffice.")
    else:
        lines.append(f"- **No event effect.** Near-event trades ({near_s.expectancy:+.2f}%/trade) are not "
                     f"worse than away ({away_s.expectancy:+.2f}%/trade). An event-pause rule would only "
                     f"drop good trades — like the 200-SMA filter, it's not worth adding. The closed-bar "
                     f"entry, channel exit, and kill switch already handle event shocks.")
    lines += ["", "_Historical association only. This measures ENTRY proximity to events; it is not a "
              "forecast, and any rule must clear out-of-sample before adoption._"]
    return "\n".join(lines)

"""Bridge: analyse the DAILY strategy's own BACKTEST trades with the same lens
we use on live trades — but on hundreds of round-trips across real regimes
(bull, bear, chop) instead of a noisy handful of live 15m trades.

Why a dedicated report rather than reusing the live `learn` render:
  * the live risk-band lens is degenerate for fixed sleeves (every trade lands
    in one band), and
  * the number that actually judges a breakout strategy is the PAYOFF SKEW
    (avg win vs avg loss) and EXPECTANCY, which a low win rate hides. A 15%
    win rate is HEALTHY if winners are ~6x losers; it's fatal if they aren't.

Everything is in per-trade RETURN % (net P&L / entry cost), so coins of wildly
different price scales are directly comparable. Trades can also be exported as
JSONL for the existing learn loop to ingest (source='backtest').
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..config import StrategyConfig
from ..exchange.models import Candle
from ..backtest.engine import Backtester, BacktestConfig, Trade
from ..strategy.trend import DonchianBreakoutStrategy


def run_backtest_trades(
    assets: list[tuple[str, list[Candle]]], base: StrategyConfig, bt: BacktestConfig,
) -> list[tuple[str, Trade]]:
    """Run the daily Donchian per coin and return every closed trade tagged with
    its symbol (the per-coin standalone run — same initial equity each — so
    return%s are comparable across coins)."""
    tagged: list[tuple[str, Trade]] = []
    for label, candles in assets:
        res = Backtester(bt).run(candles, DonchianBreakoutStrategy(base), symbol=label)
        for t in res.trades:
            tagged.append((label, t))
    return tagged


def _ret_pct(t: Trade) -> float:
    cost = t.units * t.entry_price
    return (t.net_pnl / cost * 100.0) if cost else 0.0


@dataclass
class _Stats:
    n: int
    win_rate: float
    avg_win: float
    avg_loss: float
    payoff: float
    expectancy: float
    total: float


def _stats(rets: list[float]) -> _Stats:
    n = len(rets)
    if n == 0:
        return _Stats(0, 0, 0, 0, 0, 0, 0)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    wr = len(wins) / n
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(losses) / len(losses) if losses else 0.0   # negative (or 0)
    payoff = aw / abs(al) if al < 0 else float("inf") if aw > 0 else 0.0
    expectancy = wr * aw + (1 - wr) * al
    return _Stats(n, wr * 100.0, aw, al, payoff, expectancy, sum(rets))


def render_backtest_learn_report(
    tagged: list[tuple[str, Trade]], base: StrategyConfig, bt: BacktestConfig,
    label: str = "", min_trades: int = 20,
) -> str:
    all_rets = [_ret_pct(t) for _, t in tagged]
    overall = _stats(all_rets)

    per_sym: dict[str, list[float]] = {}
    per_reason: dict[str, list[float]] = {}
    for sym, t in tagged:
        per_sym.setdefault(sym, []).append(_ret_pct(t))
        per_reason.setdefault(t.reason or "unspecified", []).append(_ret_pct(t))

    def _payoff(p: float) -> str:
        return "inf" if p == float("inf") else f"{p:.2f}"

    lines = [
        f"# Backtest learning — DAILY strategy — {label}".rstrip(),
        f"entry {base.donchian_entry_period} / exit {base.donchian_exit_period} | "
        f"fees {bt.fee_pct}%/side, slippage {bt.slippage_pct}%/fill | per-trade return%",
        "",
        "## Expectancy (the real lens for a breakout strategy)",
        f"trades:        {overall.n}",
        f"win rate:      {overall.win_rate:.1f}%",
        f"avg win:       {overall.avg_win:+.2f}%",
        f"avg loss:      {overall.avg_loss:+.2f}%",
        f"payoff ratio:  {_payoff(overall.payoff)}  (avg win / |avg loss|)",
        f"expectancy:    {overall.expectancy:+.3f}% per trade",
        f"sum of returns:{overall.total:+.1f}%",
        "",
        "## Per-symbol (worst expectancy first — over the FULL history)",
        "symbol     | trades | win% | payoff | expectancy% | sum%",
    ]
    rows = sorted(per_sym.items(), key=lambda kv: _stats(kv[1]).expectancy)
    for sym, rets in rows:
        st = _stats(rets)
        lines.append(f"{sym:10s} | {st.n:6d} | {st.win_rate:4.0f} | {_payoff(st.payoff):>6s} | "
                     f"{st.expectancy:+11.3f} | {st.total:+.1f}")

    lines += ["", "## By exit reason", "reason              | trades | win% | expectancy%"]
    for reason, rets in sorted(per_reason.items(), key=lambda kv: -len(kv[1])):
        st = _stats(rets)
        lines.append(f"{reason:19s} | {st.n:6d} | {st.win_rate:4.0f} | {st.expectancy:+.3f}")

    # ---- candidate adjustments (advisory) ---------------------------------
    lines += ["", "## Candidate adjustments (advisory — backtest before applying)"]
    cands: list[str] = []
    for sym, rets in rows:
        st = _stats(rets)
        if st.n >= min_trades and st.expectancy < 0:
            cands.append(f"- **{sym}** has NEGATIVE expectancy ({st.expectancy:+.3f}%/trade over "
                         f"{st.n} trades) across the full history -> review for exclusion from the "
                         f"basket (confirm it's not one bad regime dragging a long-run winner).")
    # skew health commentary
    if overall.n >= min_trades:
        if overall.expectancy > 0 and overall.payoff >= 2.0:
            cands.append(f"- HEALTHY trend profile: low win rate ({overall.win_rate:.0f}%) but "
                         f"payoff {_payoff(overall.payoff)} gives POSITIVE expectancy "
                         f"({overall.expectancy:+.3f}%/trade) — the edge is a few big winners, as "
                         f"intended. Don't 'fix' the win rate.")
        elif overall.expectancy <= 0:
            cands.append(f"- NEGATIVE expectancy overall ({overall.expectancy:+.3f}%/trade): the "
                         f"winners aren't large enough to cover the losers at payoff "
                         f"{_payoff(overall.payoff)}. Widening the exit channel (bigger winners) or "
                         f"a trend filter (fewer chop losers) are the levers to test.")
    if not cands:
        cands.append("- nothing stands out at this sample size; every symbol carries positive "
                     "expectancy over the full history.")
    lines += cands
    lines += ["", "_Hypotheses from the backtest, not auto-applied. Validate any change in the "
              "backtest harness (and against out-of-sample) before adopting._"]
    return "\n".join(lines)


def export_trades_jsonl(tagged: list[tuple[str, Trade]], path: str) -> int:
    """Dump the backtest trades as JSONL the learn loop can ingest (source=
    'backtest'), so daily backtest trades can flow into the learning folder
    alongside live ones. Returns the number of rows written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for sym, t in tagged:
            f.write(json.dumps({
                "source": "backtest", "symbol": sym, "side": "buy",
                "entry_price": t.entry_price, "exit_price": t.exit_price,
                "pnl": t.net_pnl, "reason": t.reason, "ts": str(t.exit_ts),
            }) + "\n")
    return len(tagged)

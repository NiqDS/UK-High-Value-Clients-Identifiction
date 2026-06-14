"""Portfolio (basket / "index") backtest for the trend-following thesis.

The single-coin robustness runs showed the Donchian breakout edge generalises
across the trending majors (BTC, ETH, BNB, ADA, AVAX, DOGE, TRX), but each coin
trades only a handful of times — too thin to trust alone. This bundles them into
one **equal-weight (or inverse-volatility) index**: a shared starting capital is
split into per-coin *sleeves*, each sleeve runs the same Donchian strategy and is
deployed fully when its coin is in an uptrend (long) and held as cash otherwise.

Pooling N coins on the same edge does two honest things:
  1. it gives the basket the statistical mass each coin lacks individually, and
  2. it diversifies the outlier dependence — when one coin's big trend trade
     misses, another's can land — which is visible as a LOWER portfolio drawdown
     than the average single-coin drawdown.

We report each coin's **weight** three ways:
  - allocation weight  — the input capital share (equal, or 1/vol normalised);
  - P&L-contribution weight — each coin's share of the basket's net profit
    (who actually drove the index; can be negative for a losing coin);
  - final-value weight — each sleeve's share of the ending bundle.

Sizing note: to behave like an index, each sleeve deploys its *full* allocation
on a long (target notional = sleeve size), unlike the tiny fixed notional used in
the single-coin `compare`/`robustness` runs. Returns are simple (non-compounded)
on each sleeve's fixed allocation — the same convention the rest of the harness
uses — so per-coin weights stay stable and comparable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..config import StrategyConfig
from ..exchange.models import Candle
from ..strategy.trend import DonchianBreakoutStrategy
from .engine import Backtester, BacktestConfig, _max_drawdown_pct


@dataclass
class SleeveResult:
    label: str
    alloc_weight: float      # input allocation fraction of total capital
    initial: float
    final: float
    net_pct: float           # sleeve return on its own allocation
    net_pnl: float
    trades: int
    maxdd_pct: float
    contrib_weight: float    # share of the basket's TOTAL net P&L
    final_weight: float      # share of the ending bundle value


@dataclass
class PortfolioResult:
    sleeves: list[SleeveResult]
    initial: float
    final: float
    net_pct: float
    maxdd_pct: float
    trades: int
    bars: int
    start_ts: int
    end_ts: int
    weight_mode: str
    fee_pct: float           # cost model the run used (echoed so a report is self-documenting)
    slippage_pct: float
    avg_sleeve_maxdd: float  # mean per-coin drawdown — the diversification benchmark
    equity_curve: list[float] = field(default_factory=list)


def align_on_common_timestamps(
    assets: list[tuple[str, list[Candle]]]
) -> tuple[list[int], dict[str, list[Candle]]]:
    """Restrict every asset to the timestamps present in ALL of them, so the
    per-coin equity curves are index-aligned and equal length (an honest index
    only spans the window where every constituent exists)."""
    if not assets:
        return [], {}
    common: set[int] | None = None
    for _, candles in assets:
        ts = {c.timestamp for c in candles}
        common = ts if common is None else (common & ts)
    common = common or set()
    ordered = sorted(common)
    keep = set(ordered)
    aligned = {
        label: [c for c in candles if c.timestamp in keep]
        for label, candles in assets
    }
    # each list is already chronological if the input was; ensure it
    for label in aligned:
        aligned[label].sort(key=lambda c: c.timestamp)
    return ordered, aligned


def _annualised_vol(candles: list[Candle]) -> float:
    """Stdev of daily log returns over the window (0 if degenerate)."""
    rets: list[float] = []
    for a, b in zip(candles, candles[1:]):
        if a.close > 0 and b.close > 0:
            rets.append(math.log(b.close / a.close))
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def compute_weights(
    aligned: dict[str, list[Candle]], mode: str = "equal"
) -> dict[str, float]:
    """Allocation weights summing to 1. ``equal`` = 1/N; ``invvol`` = inverse
    realised volatility (a simple risk-parity-style index, so a wild coin doesn't
    dominate the basket's risk)."""
    labels = list(aligned)
    n = len(labels)
    if n == 0:
        return {}
    if mode == "invvol":
        inv = {}
        for label in labels:
            vol = _annualised_vol(aligned[label])
            inv[label] = (1.0 / vol) if vol > 0 else 0.0
        total = sum(inv.values())
        if total > 0:
            return {label: inv[label] / total for label in labels}
        # fall through to equal if all vols were degenerate
    return {label: 1.0 / n for label in labels}


def portfolio_backtest(
    assets: list[tuple[str, list[Candle]]],
    base: StrategyConfig,
    bt: BacktestConfig,
    weight_mode: str = "equal",
    weights: dict[str, float] | None = None,
) -> PortfolioResult:
    """Run the Donchian trend index over the common window of ``assets``."""
    timestamps, aligned = align_on_common_timestamps(assets)
    if not timestamps:
        raise ValueError("assets share no common timestamps — cannot form an index")

    w = weights or compute_weights(aligned, weight_mode)
    total_initial = bt.initial_equity

    sleeve_curves: list[list[float]] = []
    raw: list[tuple[str, float, "object"]] = []  # (label, alloc, result)
    for label, candles in aligned.items():
        alloc = total_initial * w.get(label, 0.0)
        # full-deployment sizing: a long uses the whole sleeve
        scfg = base.model_copy(update={"target_notional_quote": alloc})
        sbt = BacktestConfig(
            initial_equity=alloc, fee_pct=bt.fee_pct, slippage_pct=bt.slippage_pct,
        )
        res = Backtester(sbt).run(candles, DonchianBreakoutStrategy(scfg), symbol=label)
        sleeve_curves.append(res.equity_curve)
        raw.append((label, alloc, res))

    # combine aligned per-bar equity into one portfolio curve
    n_bars = min(len(c) for c in sleeve_curves)
    portfolio_curve = [
        sum(curve[i] for curve in sleeve_curves) for i in range(n_bars)
    ]
    final = portfolio_curve[-1] if portfolio_curve else total_initial
    total_net_pnl = final - total_initial

    sleeves: list[SleeveResult] = []
    total_trades = 0
    maxdds: list[float] = []
    for label, alloc, res in raw:
        net_pnl = res.final_equity - alloc
        total_trades += res.num_trades
        maxdds.append(res.max_drawdown_pct)
        sleeves.append(SleeveResult(
            label=label, alloc_weight=alloc / total_initial if total_initial else 0.0,
            initial=alloc, final=res.final_equity,
            net_pct=res.net_return_pct, net_pnl=net_pnl, trades=res.num_trades,
            maxdd_pct=res.max_drawdown_pct,
            contrib_weight=(net_pnl / total_net_pnl) if abs(total_net_pnl) > 1e-9 else 0.0,
            final_weight=(res.final_equity / final) if final else 0.0,
        ))

    sleeves.sort(key=lambda s: s.net_pnl, reverse=True)
    return PortfolioResult(
        sleeves=sleeves, initial=total_initial, final=final,
        net_pct=total_net_pnl / total_initial * 100.0 if total_initial else 0.0,
        maxdd_pct=_max_drawdown_pct(portfolio_curve), trades=total_trades,
        bars=n_bars, start_ts=timestamps[0], end_ts=timestamps[-1],
        weight_mode=weight_mode, fee_pct=bt.fee_pct, slippage_pct=bt.slippage_pct,
        avg_sleeve_maxdd=sum(maxdds) / len(maxdds) if maxdds else 0.0,
        equity_curve=portfolio_curve,
    )


def _fmt_ts(ms: int) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(ms / 1000, tz=_dt.timezone.utc).strftime("%Y-%m-%d")


def portfolio_report(result: PortfolioResult, label: str = "") -> str:
    r = result
    dd_delta = r.avg_sleeve_maxdd - r.maxdd_pct
    lines = [
        f"# Trend index backtest — {label}".rstrip(),
        f"{len(r.sleeves)} coins, {r.weight_mode}-weight | "
        f"{r.bars} common daily bars ({_fmt_ts(r.start_ts)} → {_fmt_ts(r.end_ts)})",
        f"fees {r.fee_pct}%/side, slippage {r.slippage_pct}%/fill | "
        "each sleeve runs Donchian breakout, fully deployed when long, cash otherwise",
        "",
        "coin   | alloc% | net%   | P&L     | contrib% | final% | maxdd% | trades",
    ]
    for s in r.sleeves:
        lines.append(
            f"{s.label:6s} | {s.alloc_weight * 100:5.1f}  | {s.net_pct:+6.2f} | "
            f"{s.net_pnl:+8.0f} | {s.contrib_weight * 100:+7.1f}  | "
            f"{s.final_weight * 100:5.1f}  | {s.maxdd_pct:5.1f}  | {s.trades:4d}"
        )
    lines += [
        "",
        f"INDEX  | 100.0  | {r.net_pct:+6.2f} | {r.final - r.initial:+8.0f} | "
        f"  100.0  | 100.0  | {r.maxdd_pct:5.1f}  | {r.trades:4d}",
        "",
        "### Read",
        f"- Index net return: **{r.net_pct:+.2f}%** "
        f"({r.initial:.0f} → {r.final:.0f}), max drawdown {r.maxdd_pct:.1f}%.",
        f"- Diversification: index drawdown {r.maxdd_pct:.1f}% vs average single-coin "
        f"{r.avg_sleeve_maxdd:.1f}% "
        f"(**{dd_delta:+.1f} pts** {'lower — basket smooths the ride' if dd_delta > 0 else 'higher'}).",
        "- `alloc%` = capital weight in; `contrib%` = share of the basket's net P&L "
        "(who drove it); `final%` = share of the ending bundle.",
        "- Trend P&L is convex and thin per coin — the index pools those few fat "
        "winners across coins so the aggregate is steadier than any one sleeve.",
    ]
    return "\n".join(lines)

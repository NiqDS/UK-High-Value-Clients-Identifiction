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
    exposure_pct: float      # % of bars the sleeve was actually long (vs in cash)
    buyhold_pct: float       # passive buy-and-hold return over the same window


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
    regime_filter: bool      # whether the 200d-SMA regime gate was applied
    regime_period: int
    avg_sleeve_maxdd: float  # mean per-coin drawdown — the diversification benchmark
    exposure_pct: float      # capital-weighted time-in-market across the basket
    buyhold_pct: float       # weighted buy-and-hold return (the "just hold" benchmark)
    buyhold_maxdd_pct: float # drawdown of holding the basket passively (what trend dodged)
    momentum_lookback: int = 0   # cross-sectional momentum gate (0 = off)
    momentum_top_k: int = 0
    equity_curve: list[float] = field(default_factory=list)


def slice_by_date(candles: list[Candle], start_ms: int | None, end_ms: int | None) -> list[Candle]:
    """Keep candles with start_ms <= ts <= end_ms (either bound optional)."""
    return [
        c for c in candles
        if (start_ms is None or c.timestamp >= start_ms)
        and (end_ms is None or c.timestamp <= end_ms)
    ]


def _exposure_pct(trades, timestamps: list[int]) -> float:
    """Fraction of bars (%) spent holding a position — derived from each trade's
    [entry_ts, exit_ts] span. Low exposure in a bear = correctly stood aside."""
    if not timestamps:
        return 0.0
    in_market = 0
    for ts in timestamps:
        if any(t.entry_ts <= ts <= t.exit_ts for t in trades):
            in_market += 1
    return in_market / len(timestamps) * 100.0


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
    momentum_lookback: int = 0,
    momentum_top_k: int = 0,
    risk_sizing_pct: float = 0.0,
) -> PortfolioResult:
    """Run the Donchian trend index over the common window of ``assets``.

    With ``momentum_lookback``/``momentum_top_k`` set, each sleeve is wrapped in
    the cross-sectional momentum gate: new entries only when the coin ranks in
    the basket's top-K by trailing lookback-bar return (exits never gated)."""
    timestamps, aligned = align_on_common_timestamps(assets)
    if not timestamps:
        raise ValueError("assets share no common timestamps — cannot form an index")

    w = weights or compute_weights(aligned, weight_mode)
    total_initial = bt.initial_equity

    rank = None
    if momentum_lookback > 0 and momentum_top_k > 0:
        from ..strategy.momentum import MomentumRank

        rank = MomentumRank.build(aligned, momentum_lookback, momentum_top_k)

    sleeve_curves: list[list[float]] = []
    buyhold_curves: list[list[float]] = []
    raw: list[tuple[str, float, "object", float, float]] = []  # (label, alloc, res, exp, bh)
    for label, candles in aligned.items():
        alloc = total_initial * w.get(label, 0.0)
        # full-deployment sizing: a long uses the whole sleeve
        scfg = base.model_copy(update={"target_notional_quote": alloc})
        sbt = BacktestConfig(
            initial_equity=alloc, fee_pct=bt.fee_pct, slippage_pct=bt.slippage_pct,
        )
        strategy: object = DonchianBreakoutStrategy(scfg)
        if rank is not None:
            from ..strategy.momentum import MomentumGatedStrategy

            strategy = MomentumGatedStrategy(strategy, label, rank)
        if risk_sizing_pct > 0:
            # size each entry to risk `risk_sizing_pct`% of the sleeve to its stop
            # (instead of full deployment); the engine still caps at cash
            from ..strategy.risk_sizing import RiskSizedStrategy

            strategy = RiskSizedStrategy(strategy, equity=alloc, risk_pct=risk_sizing_pct)
        res = Backtester(sbt).run(candles, strategy, symbol=label)
        sleeve_curves.append(res.equity_curve)
        exp = _exposure_pct(res.trades, [c.timestamp for c in candles])
        # passive buy-and-hold of the same allocation over the same window
        first, last = candles[0].close, candles[-1].close
        bh_pct = (last / first - 1.0) * 100.0 if first > 0 else 0.0
        buyhold_curves.append([alloc * (c.close / first) if first > 0 else alloc for c in candles])
        raw.append((label, alloc, res, exp, bh_pct))

    # combine aligned per-bar equity into one portfolio curve
    n_bars = min(len(c) for c in sleeve_curves)
    portfolio_curve = [
        sum(curve[i] for curve in sleeve_curves) for i in range(n_bars)
    ]
    bh_curve = [sum(curve[i] for curve in buyhold_curves) for i in range(n_bars)]
    final = portfolio_curve[-1] if portfolio_curve else total_initial
    total_net_pnl = final - total_initial
    bh_final = bh_curve[-1] if bh_curve else total_initial

    sleeves: list[SleeveResult] = []
    total_trades = 0
    maxdds: list[float] = []
    exp_weighted = 0.0
    for label, alloc, res, exp, bh_pct in raw:
        net_pnl = res.final_equity - alloc
        total_trades += res.num_trades
        maxdds.append(res.max_drawdown_pct)
        exp_weighted += exp * (alloc / total_initial if total_initial else 0.0)
        sleeves.append(SleeveResult(
            label=label, alloc_weight=alloc / total_initial if total_initial else 0.0,
            initial=alloc, final=res.final_equity,
            net_pct=res.net_return_pct, net_pnl=net_pnl, trades=res.num_trades,
            maxdd_pct=res.max_drawdown_pct,
            contrib_weight=(net_pnl / total_net_pnl) if abs(total_net_pnl) > 1e-9 else 0.0,
            final_weight=(res.final_equity / final) if final else 0.0,
            exposure_pct=exp, buyhold_pct=bh_pct,
        ))

    sleeves.sort(key=lambda s: s.net_pnl, reverse=True)
    return PortfolioResult(
        sleeves=sleeves, initial=total_initial, final=final,
        net_pct=total_net_pnl / total_initial * 100.0 if total_initial else 0.0,
        maxdd_pct=_max_drawdown_pct(portfolio_curve), trades=total_trades,
        bars=n_bars, start_ts=timestamps[0], end_ts=timestamps[-1],
        weight_mode=weight_mode, fee_pct=bt.fee_pct, slippage_pct=bt.slippage_pct,
        regime_filter=base.trend_filter_enabled, regime_period=base.trend_period,
        momentum_lookback=momentum_lookback if rank is not None else 0,
        momentum_top_k=momentum_top_k if rank is not None else 0,
        avg_sleeve_maxdd=sum(maxdds) / len(maxdds) if maxdds else 0.0,
        exposure_pct=exp_weighted,
        buyhold_pct=(bh_final / total_initial - 1.0) * 100.0 if total_initial else 0.0,
        buyhold_maxdd_pct=_max_drawdown_pct(bh_curve),
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
        + (f"regime gate: long only above the {r.regime_period}d SMA"
           if r.regime_filter else "no regime gate (raw breakout)")
        + (f" | momentum gate: entries only in the top {r.momentum_top_k} "
           f"of {len(r.sleeves)} by {r.momentum_lookback}-bar return"
           if r.momentum_lookback else ""),
        "each sleeve runs Donchian breakout, fully deployed when long, cash otherwise",
        "",
        "coin   | alloc% | net%   | buy&hold% | expo% | maxdd% | trades",
    ]
    for s in r.sleeves:
        lines.append(
            f"{s.label:6s} | {s.alloc_weight * 100:5.1f}  | {s.net_pct:+6.2f} | "
            f"{s.buyhold_pct:+8.1f}  | {s.exposure_pct:4.0f}  | "
            f"{s.maxdd_pct:5.1f}  | {s.trades:4d}"
        )
    # capital-preservation framing: trend vs just holding the basket
    dodged = r.buyhold_maxdd_pct - r.maxdd_pct
    beat_bh = r.net_pct - r.buyhold_pct
    lines += [
        "",
        f"INDEX  | 100.0  | {r.net_pct:+6.2f} | {r.buyhold_pct:+8.1f}  | "
        f"{r.exposure_pct:4.0f}  | {r.maxdd_pct:5.1f}  | {r.trades:4d}",
        "",
        "### Read",
        f"- Trend index: **{r.net_pct:+.2f}%** ({r.initial:.0f} → {r.final:.0f}), "
        f"max drawdown {r.maxdd_pct:.1f}%, time-in-market **{r.exposure_pct:.0f}%**.",
        f"- Buy-and-hold the same basket: **{r.buyhold_pct:+.2f}%**, "
        f"max drawdown {r.buyhold_maxdd_pct:.1f}%.",
        f"- Capital preservation: trend {'BEAT' if beat_bh > 0 else 'TRAILED'} buy-and-hold by "
        f"**{beat_bh:+.1f} pts** of return while {'cutting' if dodged > 0 else 'adding'} "
        f"**{abs(dodged):.1f} pts** of drawdown "
        f"(held only {r.exposure_pct:.0f}% of the time — the rest in cash).",
        f"- Diversification: index drawdown {r.maxdd_pct:.1f}% vs average single-coin "
        f"{r.avg_sleeve_maxdd:.1f}% "
        f"(**{dd_delta:+.1f} pts** {'lower' if dd_delta > 0 else 'higher'}).",
        "- `expo%` = % of bars actually long (low in a bear = it sold and sat in cash). "
        "Donchian is long-only: it can't short the downtrend, so 'spotting the bear' "
        "shows up as an early channel exit + low exposure, NOT a short profit.",
    ]
    return "\n".join(lines)


def risk_sweep_report(
    assets: list[tuple[str, list[Candle]]], base: StrategyConfig, bt: BacktestConfig,
    risk_levels: list[float], weight_mode: str = "equal", label: str = "",
) -> str:
    """Sweep the per-trade RISK LIMIT (% of sleeve risked to the stop) over the
    common window and report each level's return / max-drawdown, so the optimal
    risk-adjusted level is visible. Each level re-runs the basket with risk-based
    sizing; return/maxdd (higher = better) is the selection metric."""
    rows: list[tuple[float, PortfolioResult]] = []
    for r in sorted(risk_levels):
        res = portfolio_backtest(assets, base, bt, weight_mode=weight_mode, risk_sizing_pct=r)
        rows.append((r, res))
    lines = [
        f"# Risk-limit sweep — {label}".rstrip(),
        f"{len(assets)} coins, {weight_mode}-weight | fees {bt.fee_pct}%/side, "
        f"slippage {bt.slippage_pct}%/fill | each entry risks R% of its sleeve to the stop",
        "",
        "risk% | net%    | maxdd% | return/dd | exposure% | trades",
    ]
    best = None
    for r, res in rows:
        rr = res.net_pct / res.maxdd_pct if res.maxdd_pct > 0 else 0.0
        if best is None or rr > best[1]:
            best = (r, rr, res)
        lines.append(f"{r:5.1f} | {res.net_pct:+7.2f} | {res.maxdd_pct:5.1f}  | "
                     f"{rr:8.2f}  | {res.exposure_pct:8.0f}  | {res.trades:5d}")
    lines += ["", "### Read"]
    if best:
        r, rr, res = best
        lines.append(f"- Best risk-adjusted limit: **{r:.1f}% per trade** "
                     f"(return/maxdd {rr:.2f}: {res.net_pct:+.1f}% over {res.maxdd_pct:.1f}% dd).")
        lines.append("- Higher risk% = bigger positions = more return AND more drawdown; the "
                     "peak return/maxdd is the sweet spot. Set risk-based sizing there.")
    lines.append("- Bull-regime + survivor-biased history (see RESEARCH_REPORT) — treat the "
                 "level as a starting point, not a guarantee; re-check on new data.")
    return "\n".join(lines)

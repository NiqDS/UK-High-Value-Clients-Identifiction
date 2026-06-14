"""Controlled comparison of strategy hypotheses + mechanic attribution.

Runs a set of experiments on the SAME out-of-sample data and ranks them, then
isolates each *mechanic* (trend filter, VWAP valuation gate, fee-drag controls,
VWAP sizing, mean-reversion thesis) by its delta vs a fixed baseline — so the
ideas that actually help can be retrofitted onto any strategy.

The baseline (H1) is the raw SMA crossover (no VWAP filter); each single-mechanic
experiment adds exactly one thing over it. ``vwap_reversion`` (H2) is the
separate volatility-thesis strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..config import StrategyConfig
from ..strategy.base import Strategy
from ..strategy.sma import SmaCrossoverStrategy
from ..strategy.trend import DonchianBreakoutStrategy
from ..strategy.vwap_reversion import VwapReversionStrategy
from .engine import Backtester, BacktestConfig
from .metrics import Metric

BASELINE = "H1_sma_baseline"


@dataclass
class Experiment:
    name: str
    factory: Callable[[], Strategy]
    mechanic: str       # the single mechanic this adds over the baseline
    single: bool = True  # True if it isolates exactly one mechanic over baseline


@dataclass
class ExpScore:
    name: str
    mechanic: str
    trades: int
    net_pct: float
    score: float
    win_pct: float
    maxdd: float
    single: bool


def default_experiments(base: StrategyConfig) -> list[Experiment]:
    def c(**kw) -> StrategyConfig:
        return base.model_copy(update=kw)

    return [
        Experiment(BASELINE,
                   lambda: SmaCrossoverStrategy(c(vwap_filter_enabled=False)),
                   "none (baseline)", single=False),
        Experiment("add_vwap_gate",
                   lambda: SmaCrossoverStrategy(c(vwap_filter_enabled=True)),
                   "vwap_valuation_gate"),
        Experiment("add_trend_filter",
                   lambda: SmaCrossoverStrategy(c(vwap_filter_enabled=False, trend_filter_enabled=True)),
                   "trend_filter"),
        Experiment("add_fee_drag",
                   lambda: SmaCrossoverStrategy(
                       c(vwap_filter_enabled=False, trade_cooldown_bars=20, min_crossover_strength_pct=0.2)),
                   "fee_drag_controls"),
        Experiment("add_vwap_sizing",
                   lambda: SmaCrossoverStrategy(c(vwap_filter_enabled=True, sizing_mode="vwap_scaled")),
                   "vwap_scaled_sizing"),
        Experiment("sma_all_mechanics",
                   lambda: SmaCrossoverStrategy(
                       c(vwap_filter_enabled=True, trend_filter_enabled=True,
                         trade_cooldown_bars=20, min_crossover_strength_pct=0.2)),
                   "combined_sma", single=False),
        Experiment("H2_vwap_reversion",
                   lambda: VwapReversionStrategy(c()),
                   "mean_reversion (thesis)", single=False),
        Experiment("H3_donchian_trend",
                   lambda: DonchianBreakoutStrategy(c()),
                   "trend_breakout (thesis)", single=False),
    ]


def funding_experiments(base: StrategyConfig, series, overlay, gate: bool) -> list[Experiment]:
    """Wrap the baseline + reversion strategies with the funding overlay so its
    out-of-sample contribution is measured the same way."""
    from ..strategy.funding import FundingFilteredStrategy

    def c(**kw) -> StrategyConfig:
        return base.model_copy(update=kw)

    return [
        Experiment(
            "baseline+funding",
            lambda: FundingFilteredStrategy(
                SmaCrossoverStrategy(c(vwap_filter_enabled=False)), series, overlay, gate),
            "funding_overlay", single=False),
        Experiment(
            "reversion+funding",
            lambda: FundingFilteredStrategy(VwapReversionStrategy(c()), series, overlay, gate),
            "funding+reversion", single=False),
        Experiment(
            "trend+funding",
            lambda: FundingFilteredStrategy(DonchianBreakoutStrategy(c()), series, overlay, gate),
            "funding+trend", single=False),
    ]


def mvrv_experiments(base: StrategyConfig, series, overlay, gate: bool) -> list[Experiment]:
    """Wrap baseline + reversion with the MVRV Z-score valuation overlay."""
    from ..strategy.onchain import MvrvFilteredStrategy

    def c(**kw) -> StrategyConfig:
        return base.model_copy(update=kw)

    return [
        Experiment(
            "baseline+mvrv",
            lambda: MvrvFilteredStrategy(
                SmaCrossoverStrategy(c(vwap_filter_enabled=False)), series, overlay, gate),
            "mvrv_overlay", single=False),
        Experiment(
            "reversion+mvrv",
            lambda: MvrvFilteredStrategy(VwapReversionStrategy(c()), series, overlay, gate),
            "mvrv+reversion", single=False),
        Experiment(
            "trend+mvrv",
            lambda: MvrvFilteredStrategy(DonchianBreakoutStrategy(c()), series, overlay, gate),
            "mvrv+trend", single=False),
    ]


def trend_sweep_report(
    candles, base: StrategyConfig, entry_periods: list[int], exit_periods: list[int],
    fee_pct: float, slippage_pct: float, oos_ratio: float, metric: Metric, label: str = "",
) -> str:
    """Run the Donchian trend strategy across a grid of (entry, exit) periods — is
    the edge robust across parameters, or a lucky single setting? (exit < entry only.)"""
    bt = BacktestConfig(fee_pct=fee_pct, slippage_pct=slippage_pct, oos_ratio=oos_ratio)
    results = []
    for ep in entry_periods:
        for xp in exit_periods:
            if xp >= ep:
                continue
            cfg = base.model_copy(update={"donchian_entry_period": ep, "donchian_exit_period": xp})
            exp = Experiment("t", lambda c=cfg: DonchianBreakoutStrategy(c), "trend", single=False)
            s = score_experiment(candles, exp, bt, metric)
            results.append((ep, xp, s))
    results.sort(key=lambda r: r[2].score, reverse=True)
    pos = sum(1 for _, _, s in results if s.net_pct > 0)
    lines = [
        f"# Donchian param sweep — {label}",
        f"fee {fee_pct}%/side, slippage {slippage_pct}%/fill | OOS {oos_ratio:.0%}",
        "",
        "entry | exit | net%    | score   | win% | trades",
    ]
    for ep, xp, s in results:
        lines.append(f"{ep:5d} | {xp:4d} | {s.net_pct:+6.2f} | {s.score:+6.3f} | "
                     f"{s.win_pct:4.0f} | {s.trades:4d}")
    lines += ["", "### Read",
              f"- net-positive in **{pos}/{len(results)}** parameter settings.",
              "- A robust edge is positive across MOST of the grid, not at one lucky setting."]
    return "\n".join(lines)


def segment_report(
    candles, base: StrategyConfig, n_segments: int, fee_pct: float,
    slippage_pct: float, metric: Metric, label: str = "",
) -> str:
    """Run the FIXED baseline + mean-reversion strategies on each of N sequential,
    non-overlapping time segments — does the edge hold over time, or was it one
    lucky period? (Each segment is scored standalone; no parameters are learned.)"""
    bt = BacktestConfig(fee_pct=fee_pct, slippage_pct=slippage_pct)
    seg = max(1, len(candles) // n_segments)
    rev_f = lambda: VwapReversionStrategy(base)
    trend_f = lambda: DonchianBreakoutStrategy(base)
    lines = [
        f"# Robustness across {n_segments} sequential segments — {label}",
        f"fee {fee_pct}%/side, slippage {slippage_pct}%/fill | each segment scored standalone",
        "",
        "seg |  bars | reversion net% / score (trades) | trend net% / score (trades)",
    ]
    rev_pos = trend_pos = 0
    for i in range(n_segments):
        s = candles[i * seg:] if i == n_segments - 1 else candles[i * seg:(i + 1) * seg]
        rr = Backtester(bt).run(s, rev_f())
        rt = Backtester(bt).run(s, trend_f())
        rs, ts = metric(rr), metric(rt)
        rev_pos += rr.net_return_pct > 0
        trend_pos += rt.net_return_pct > 0
        lines.append(
            f"{i:2d}  | {len(s):5d} | {rr.net_return_pct:+6.2f} / {rs:+6.3f} ({rr.num_trades:3d}) "
            f"      | {rt.net_return_pct:+6.2f} / {ts:+6.3f} ({rt.num_trades:3d})")
    lines += ["", "### Read",
              f"- mean-reversion net-positive in **{rev_pos}/{n_segments}** segments; "
              f"trend-breakout net-positive in **{trend_pos}/{n_segments}**.",
              "- A real edge is positive in MOST segments. Concentrated in one => fragile/lucky."]
    return "\n".join(lines)


def score_experiment(candles, exp: Experiment, bt_config: BacktestConfig, metric: Metric) -> ExpScore:
    oos = Backtester(bt_config).run_oos(candles, exp.factory)[1]
    return ExpScore(
        name=exp.name, mechanic=exp.mechanic, trades=oos.num_trades,
        net_pct=oos.net_return_pct, score=metric(oos),
        win_pct=oos.win_rate_pct, maxdd=oos.max_drawdown_pct, single=exp.single,
    )


def cross_asset_report(
    assets: list[tuple[str, list, float]],  # (label, candles, fee_pct)
    base: StrategyConfig, oos_ratio: float, slippage_pct: float, metric: Metric,
) -> str:
    """Run the SAME baseline + mean-reversion strategies on each asset and tabulate
    how each behaves — the cross-asset view (does the thesis fit equities vs crypto?)."""
    lines = [
        "# Cross-asset comparison (out-of-sample)",
        f"metric: net-of-fees return / max drawdown | OOS fraction: {oos_ratio:.0%}",
        "",
        "asset      | bars  | fee%  | reversion net%/score(t) | trend net%/score(t)     | best thesis",
    ]
    for label, candles, fee in assets:
        bt = BacktestConfig(fee_pct=fee, slippage_pct=slippage_pct, oos_ratio=oos_ratio)
        by = {s.name: s for s in (score_experiment(candles, e, bt, metric)
                                  for e in default_experiments(base))}
        r, t = by.get("H2_vwap_reversion"), by.get("H3_donchian_trend")
        # best of the two theses that is also net-positive
        best = "neither"
        cands = [(n, s) for n, s in (("reversion", r), ("trend", t)) if s and s.net_pct > 0]
        if cands:
            best = max(cands, key=lambda x: x[1].score)[0]
        lines.append(
            f"{label:10s} | {len(candles):5d} | {fee:4.2f}  | "
            f"{(r.net_pct if r else 0):+6.2f} / {(r.score if r else 0):+6.3f} ({r.trades if r else 0:3d}) | "
            f"{(t.net_pct if t else 0):+6.2f} / {(t.score if t else 0):+6.3f} ({t.trades if t else 0:3d}) | {best}"
        )
    lines += ["", "### Read",
              "- 'best thesis' = whichever of mean-reversion / trend-breakout is net-positive OOS "
              "with the higher risk-adjusted score (or 'neither').",
              "- Expectation: reversion fits mean-reverting low-vol assets (equity indices); "
              "trend fits trending high-vol assets (crypto)."]
    return "\n".join(lines)


def run_comparison(
    candles, experiments: list[Experiment], bt_config: BacktestConfig, metric: Metric,
) -> tuple[list[ExpScore], str]:
    scores = [score_experiment(candles, e, bt_config, metric) for e in experiments]
    by_name = {s.name: s for s in scores}
    baseline = by_name.get(BASELINE)
    base_score = baseline.score if baseline else 0.0

    ranked = sorted(scores, key=lambda s: s.score, reverse=True)
    lines = [
        "## Strategy comparison (out-of-sample)",
        f"metric: net-of-fees return / max drawdown | OOS fraction: {bt_config.oos_ratio:.0%}",
        "",
        "rank | experiment            | mechanic                | trades | net%    | score   | win% | maxdd%",
    ]
    for i, s in enumerate(ranked, 1):
        lines.append(
            f" {i:2d}  | {s.name:21s} | {s.mechanic:23s} | {s.trades:5d}  | "
            f"{s.net_pct:+6.2f} | {s.score:+6.3f} | {s.win_pct:4.0f} | {s.maxdd:5.2f}"
        )

    # mechanic attribution: single-mechanic experiments vs the baseline
    lines += ["", "### Mechanic attribution (delta vs H1 baseline)"]
    singles = sorted((s for s in scores if s.single), key=lambda s: s.score - base_score, reverse=True)
    if baseline:
        lines.append(f"baseline ({BASELINE}) score = {base_score:+.3f}  "
                     f"(net {baseline.net_pct:+.2f}%, {baseline.trades} trades)")
    for s in singles:
        delta = s.score - base_score
        verdict = "HELPS" if delta > 1e-9 else ("hurts" if delta < -1e-9 else "neutral")
        lines.append(f"  {s.mechanic:23s} : {delta:+.3f}  ({verdict})")

    # verdicts
    lines += ["", "### Read"]
    best = ranked[0]
    lines.append(f"- Best OOS: **{best.name}** ({best.mechanic}), score {best.score:+.3f}, "
                 f"net {best.net_pct:+.2f}% over {best.trades} trades.")
    rev = by_name.get("H2_vwap_reversion")
    if rev and baseline:
        cmp = "beats" if rev.score > base_score else "does NOT beat"
        lines.append(f"- Volatility thesis (mean-reversion) {cmp} the directional baseline "
                     f"({rev.score:+.3f} vs {base_score:+.3f}).")
    lines.append("- A mechanic only counts if it HELPS out-of-sample AND trades enough to trust "
                 "(few trades => the score is noise, not signal).")
    return scores, "\n".join(lines)

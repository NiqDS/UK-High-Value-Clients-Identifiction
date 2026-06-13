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
    ]


def score_experiment(candles, exp: Experiment, bt_config: BacktestConfig, metric: Metric) -> ExpScore:
    oos = Backtester(bt_config).run_oos(candles, exp.factory)[1]
    return ExpScore(
        name=exp.name, mechanic=exp.mechanic, trades=oos.num_trades,
        net_pct=oos.net_return_pct, score=metric(oos),
        win_pct=oos.win_rate_pct, maxdd=oos.max_drawdown_pct, single=exp.single,
    )


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

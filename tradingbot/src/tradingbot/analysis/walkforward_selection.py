"""Walk-forward coin selection — is picking coins real, or curve-fitting?

Split history into TRAIN (early) and TEST (late). Rank coins by expectancy on
TRAIN only, select the winners, then trade that FIXED set on TEST — data the
selection never saw. Then ask two questions:

  1. Did the TRAIN ranking predict the TEST ranking? (rank stability)
  2. Did the selected subset beat the FULL basket out-of-sample? (return/dd)

If both hold, coin selection has genuine value. If not, "dropping the bad coins"
was fitting noise, and you should trade the whole basket. This is the only
honest way to answer "should we trim the basket?" — full-sample selection can
only ever curve-fit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import StrategyConfig
from ..exchange.models import Candle
from ..backtest.engine import Backtester, BacktestConfig
from ..backtest.portfolio import PortfolioResult, portfolio_backtest, slice_by_date
from ..strategy.trend import DonchianBreakoutStrategy
from .backtest_learn import _ret_pct, _stats


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _coin_exp(candles: list[Candle], cfg: StrategyConfig, bt: BacktestConfig):
    if len(candles) < 60:
        return None
    res = Backtester(bt).run(candles, DonchianBreakoutStrategy(cfg))
    return _stats([_ret_pct(t) for t in res.trades])


def _ranks(vals: list[float]) -> list[float]:
    """Fractional ranks with ties averaged (proper Spearman handling), so tied
    values don't manufacture a spurious ordering."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    r = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def _spearman(a: list[float], b: list[float]) -> float:
    """Rank correlation (Pearson on ranks). +1 = ranking perfectly preserved."""
    n = len(a)
    if n < 3:
        return 0.0
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return cov / (va * vb) if va > 0 and vb > 0 else 0.0


def _rr(res: PortfolioResult) -> float:
    return res.net_pct / res.maxdd_pct if res.maxdd_pct > 0 else 0.0


@dataclass
class WFResult:
    split_ms: int
    train: dict            # coin -> _Stats (train expectancy)
    test: dict             # coin -> _Stats (test expectancy)
    selected: list[str]
    full_test: PortfolioResult
    sel_test: PortfolioResult
    stability: float


def walkforward_select(
    assets: list[tuple[str, list[Candle]]], base: StrategyConfig, bt: BacktestConfig,
    split_ms: int, top_k: int | None = None,
) -> WFResult:
    by_label = dict(assets)
    train, test = {}, {}
    for label, candles in assets:
        train[label] = _coin_exp([c for c in candles if c.timestamp < split_ms], base, bt)
        test[label] = _coin_exp([c for c in candles if c.timestamp >= split_ms], base, bt)

    ranked = sorted((l for l in train if train[l]), key=lambda l: -train[l].expectancy)
    if top_k:
        selected = ranked[:top_k]
    else:  # all coins with positive TRAIN expectancy
        selected = [l for l in ranked if train[l].expectancy > 0]
    if not selected:                      # degenerate: nothing positive on train
        selected = ranked[:1]

    test_all = [(l, slice_by_date(by_label[l], split_ms, None)) for l in by_label]
    test_sel = [(l, c) for l, c in test_all if l in selected]
    full_test = portfolio_backtest(test_all, base, bt)
    sel_test = portfolio_backtest(test_sel, base, bt)

    common = [l for l in by_label if train[l] and test[l]]
    stability = _spearman([train[l].expectancy for l in common],
                          [test[l].expectancy for l in common])
    return WFResult(split_ms, train, test, selected, full_test, sel_test, stability)


def render_walkforward_report(wf: WFResult, base: StrategyConfig, label: str = "") -> str:
    lines = [
        f"# Walk-forward coin selection — {label}".rstrip(),
        f"entry {base.donchian_entry_period} / exit {base.donchian_exit_period} | "
        f"split at {_day(wf.split_ms)}  (TRAIN before, TEST after)",
        "",
        "## Per-coin expectancy: does the TRAIN ranking survive into TEST?",
        "coin       | train exp% | test exp% | picked on train?",
    ]
    ranked = sorted((l for l in wf.train if wf.train[l]),
                    key=lambda l: -wf.train[l].expectancy)
    for l in ranked:
        te = wf.test[l]
        te_s = f"{te.expectancy:+.3f}" if te else "  n/a"
        pick = "✔ selected" if l in wf.selected else ""
        lines.append(f"{l:10s} | {wf.train[l].expectancy:+10.3f} | {te_s:>9s} | {pick}")
    lines += ["",
              f"rank stability (Spearman train→test): {wf.stability:+.2f}  "
              f"(+1 = order preserved, ~0 = scrambled, <0 = inverted)"]

    lines += ["", "## Out-of-sample TEST portfolio — selected subset vs the full basket",
              "basket            | coins | net%    | maxdd% | return/dd",
              f"full (all)        | {len(wf.full_test.sleeves):5d} | {wf.full_test.net_pct:+7.1f} | "
              f"{wf.full_test.maxdd_pct:5.1f}  | {_rr(wf.full_test):8.2f}",
              f"selected on train | {len(wf.sel_test.sleeves):5d} | {wf.sel_test.net_pct:+7.1f} | "
              f"{wf.sel_test.maxdd_pct:5.1f}  | {_rr(wf.sel_test):8.2f}",
              f"selected: {', '.join(wf.selected)}"]

    # verdict
    full_rr, sel_rr = _rr(wf.full_test), _rr(wf.sel_test)
    lines += ["", "## Verdict"]
    helped = sel_rr > full_rr * 1.05
    stable = wf.stability >= 0.3
    if helped and stable:
        lines.append(f"- Coin selection HELPED out-of-sample (return/dd {full_rr:.2f} -> {sel_rr:.2f}) "
                     f"AND the ranking was stable ({wf.stability:+.2f}). Real, not curve-fit — "
                     "trimming the basket has genuine value.")
    elif helped and not stable:
        lines.append(f"- Selected beat the full basket on TEST (return/dd {full_rr:.2f} -> {sel_rr:.2f}), "
                     f"BUT the ranking was UNSTABLE ({wf.stability:+.2f}) — the train 'winners' didn't "
                     "stay winners, so this is likely luck, not skill. Treat with suspicion.")
    else:
        lines.append(f"- Coin selection did NOT help out-of-sample "
                     f"(full return/dd {full_rr:.2f} vs selected {sel_rr:.2f}"
                     f"{', ranking unstable ' + format(wf.stability, '+.2f') if not stable else ''}). "
                     "Dropping the in-sample losers was CURVE-FITTING — trade the full basket.")
    lines += ["", "_TEST is data the selection never saw. This is the honest test of 'trim the "
              "basket'; a full-sample pick can only ever flatter the past._"]
    return "\n".join(lines)

"""Screen a CANDIDATE coin for admission to the validated basket.

The bar for adding a pair is the same one the current basket passed — NOT "it's
a new listing and looks exciting". A candidate earns its place only if:

  1. it has ENOUGH history to judge (a 50-day Donchian with an OOS split needs
     hundreds of daily bars; a 3-week-old listing simply cannot be assessed);
  2. its trend edge is ROBUST — net-positive across most time segments and over
     the full out-of-sample, not one lucky window;
  3. it is ADDITIVE — the basket WITH it has >= the risk-adjusted return
     (net%/maxdd%) of the basket WITHOUT it, measured over the SAME window;
  4. it is not REDUNDANT — a pair ~perfectly correlated with the existing coins
     adds fees and turnover without diversifying.

Fair-comparison note: ``portfolio_backtest`` aligns on the assets' COMMON
window, so a short candidate would silently shrink it. We therefore slice the
incumbents to the candidate's window and run BOTH baskets over that identical
span — otherwise "with vs without" compares different periods.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import StrategyConfig
from ..exchange.models import Candle
from ..backtest.engine import Backtester, BacktestConfig
from ..backtest.portfolio import PortfolioResult, portfolio_backtest, slice_by_date
from ..strategy.trend import DonchianBreakoutStrategy

# A 50-day Donchian with a 30% OOS tail needs a few hundred bars to mean
# anything. Below this the honest answer is "cannot assess yet".
MIN_BARS = 400


@dataclass
class CandidateVerdict:
    label: str
    bars: int
    enough_history: bool
    robust_segments: int
    n_segments: int
    full_net_pct: float
    base_net_pct: float
    base_rr: float
    with_net_pct: float
    with_rr: float
    correlation: float
    promote: bool
    reasons: list[str]


def _rr(res: PortfolioResult) -> float:
    return res.net_pct / res.maxdd_pct if res.maxdd_pct > 0 else 0.0


def _returns_from_series(values: list[float]) -> list[float]:
    out = []
    for i in range(1, len(values)):
        p0 = values[i - 1]
        if p0:
            out.append((values[i] - p0) / p0)
    return out


def _daily_returns(candles: list[Candle]) -> list[float]:
    return _returns_from_series([c.close for c in candles])


def _pearson(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = math.sqrt(sum((x - ma) ** 2 for x in a))
    vb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return cov / (va * vb) if va > 0 and vb > 0 else 0.0


def screen_candidate(
    baseline: list[tuple[str, list[Candle]]],
    candidate_label: str,
    candidate: list[Candle],
    base_cfg: StrategyConfig,
    bt: BacktestConfig,
    n_segments: int = 5,
    weight_mode: str = "equal",
) -> CandidateVerdict:
    bars = len(candidate)
    enough = bars >= MIN_BARS
    reasons: list[str] = []

    # 1) candidate's OWN trend robustness across N sequential segments + full OOS
    seg = max(1, bars // n_segments)
    robust_segments = 0
    for i in range(n_segments):
        s = candidate[i * seg:] if i == n_segments - 1 else candidate[i * seg:(i + 1) * seg]
        if len(s) < 30:
            continue
        r = Backtester(bt).run(s, DonchianBreakoutStrategy(base_cfg))
        robust_segments += r.net_return_pct > 0
    full = Backtester(bt).run(candidate, DonchianBreakoutStrategy(base_cfg))
    full_net = full.net_return_pct

    # 2) FAIR portfolio comparison over the candidate's window (slice incumbents
    #    to the same span so with-vs-without is like-for-like)
    c_start, c_end = candidate[0].timestamp, candidate[-1].timestamp
    seven = [(lbl, slice_by_date(c, c_start, c_end)) for lbl, c in baseline]
    seven = [(lbl, c) for lbl, c in seven if len(c) >= 30]
    base_res = portfolio_backtest(seven, base_cfg, bt, weight_mode=weight_mode)
    with_res = portfolio_backtest(
        [*seven, (candidate_label, candidate)], base_cfg, bt, weight_mode=weight_mode)

    # 3) correlation vs the equal-weight basket's daily returns (redundancy check)
    basket_rets = _returns_from_series(base_res.equity_curve) if base_res.equity_curve else []
    cand_win = slice_by_date(candidate, base_res.start_ts, base_res.end_ts)
    corr = _pearson(_daily_returns(cand_win), basket_rets)

    # ---- verdict ----------------------------------------------------------
    robust = robust_segments >= math.ceil(0.6 * n_segments) and full_net > 0
    additive = _rr(with_res) >= _rr(base_res) * 0.98
    if not enough:
        reasons.append(f"insufficient history: {bars} bars < {MIN_BARS} needed to validate "
                       f"(new listings can't be assessed until they age)")
    if not robust:
        reasons.append(f"weak/fragile edge: net-positive in only {robust_segments}/{n_segments} "
                       f"segments, full net {full_net:+.1f}%")
    if not additive:
        reasons.append(f"not additive: basket return/dd falls {_rr(base_res):.2f} -> "
                       f"{_rr(with_res):.2f} when added")
    if corr >= 0.90:
        reasons.append(f"highly correlated with the basket ({corr:.2f}) — diversifies little")

    promote = enough and robust and additive
    return CandidateVerdict(
        label=candidate_label, bars=bars, enough_history=enough,
        robust_segments=robust_segments, n_segments=n_segments, full_net_pct=full_net,
        base_net_pct=base_res.net_pct, base_rr=_rr(base_res),
        with_net_pct=with_res.net_pct, with_rr=_rr(with_res),
        correlation=corr, promote=promote, reasons=reasons,
    )


def render_candidate_report(v: CandidateVerdict) -> str:
    verdict = "PROMOTE ✅" if v.promote else "REJECT ❌"
    lines = [
        f"# Candidate screen — {v.label}",
        f"verdict: {verdict}",
        "",
        f"history:        {v.bars} daily bars  ({'ok' if v.enough_history else 'TOO SHORT'}; "
        f"need >= {MIN_BARS})",
        f"trend edge:     net-positive in {v.robust_segments}/{v.n_segments} segments, "
        f"full-history net {v.full_net_pct:+.1f}%",
        f"basket return/dd (same window):  without {v.base_rr:.2f}  ->  with {v.with_rr:.2f}",
        f"basket net% (same window):       without {v.base_net_pct:+.1f}%  ->  with {v.with_net_pct:+.1f}%",
        f"correlation to basket:           {v.correlation:.2f}"
        + ("  (redundant)" if v.correlation >= 0.90 else ""),
    ]
    if v.reasons:
        lines += ["", "### Why", *[f"- {r}" for r in v.reasons]]
    else:
        lines += ["", "### Why",
                  "- enough history, robust trend edge across segments, and it holds or "
                  "improves the basket's risk-adjusted return without being redundant."]
    lines += ["", "### Read",
              "- PROMOTE means it clears the SAME bar the current basket did — not that it's "
              "guaranteed to help live. Add it to the PAPER allowlist first and collect mock "
              "trades before it ever touches the live config.",
              "- REJECT on 'insufficient history' is the norm for fresh listings — re-screen "
              "once the coin has aged past the bar."]
    return "\n".join(lines)

"""Map the price action of the basket over a window: monthly moves, and the
sequence of corrections (down-legs) and rallies (up-legs).

This is DESCRIPTIVE history, not a forecast. It shows the SHAPE of a regime —
how drops and bounces alternate, how deep, how long — which is useful for
knowing what the bot will actually sit through. It says nothing about what the
market will do next; a bear that fell in month 3 last time implies nothing
about timing this time.

Basket = equal-weight index of the constituents, each normalised to 100 at the
window start and averaged each day. Swings are detected with a threshold
zig-zag: a leg is confirmed only once price reverses >= `swing_pct` from its
extreme, so noise below that threshold is ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..exchange.models import Candle
from ..backtest.portfolio import align_on_common_timestamps


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _ym(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def basket_index(assets: list[tuple[str, list[Candle]]]) -> tuple[list[int], list[float], dict[str, list[float]]]:
    """Equal-weight index (base 100) over the common window, plus each coin's
    normalised series. Returns (timestamps, basket_levels, {coin: levels})."""
    ts, aligned = align_on_common_timestamps(assets)
    if not ts:
        return [], [], {}
    norm: dict[str, list[float]] = {}
    for label, candles in aligned.items():
        base = candles[0].close or 1.0
        norm[label] = [(c.close / base) * 100.0 for c in candles]
    basket = [sum(norm[l][i] for l in norm) / len(norm) for i in range(len(ts))]
    return ts, basket, norm


@dataclass
class Leg:
    kind: str          # "correction" | "rally"
    start_ts: int
    end_ts: int
    pct: float
    days: int


def swings(ts: list[int], levels: list[float], swing_pct: float) -> list[Leg]:
    """Threshold zig-zag: alternating rallies/corrections, each confirmed only
    after a reversal of >= swing_pct from the running extreme."""
    n = len(levels)
    if n < 2:
        return []
    thr = swing_pct / 100.0
    legs: list[Leg] = []
    start_i = hi_i = lo_i = 0
    trend = 0  # +1 up, -1 down, 0 undetermined

    def _leg(kind: str, a: int, b: int) -> None:
        if b > a and levels[a] > 0:
            pct = (levels[b] - levels[a]) / levels[a] * 100.0
            days = int((ts[b] - ts[a]) / 86_400_000)
            legs.append(Leg(kind, ts[a], ts[b], pct, days))

    for i in range(1, n):
        if levels[i] > levels[hi_i]:
            hi_i = i
        if levels[i] < levels[lo_i]:
            lo_i = i
        if trend >= 0 and levels[hi_i] > 0 and (levels[hi_i] - levels[i]) / levels[hi_i] >= thr:
            _leg("rally", start_i, hi_i)
            trend, start_i, lo_i, hi_i = -1, hi_i, i, i
        elif trend <= 0 and levels[lo_i] > 0 and (levels[i] - levels[lo_i]) / levels[lo_i] >= thr:
            _leg("correction", start_i, lo_i)
            trend, start_i, hi_i, lo_i = 1, lo_i, i, i
    # close the final open leg to its extreme
    if trend == 1:
        _leg("rally", start_i, hi_i)
    elif trend == -1:
        _leg("correction", start_i, lo_i)
    return legs


def _monthly(ts: list[int], levels: list[float]) -> list[tuple[str, float]]:
    """Month-end index levels -> month-over-month % returns."""
    month_end: dict[str, tuple[int, float]] = {}
    for t, v in zip(ts, levels):
        month_end[_ym(t)] = (t, v)  # last write per month = month-end
    months = sorted(month_end)
    out: list[tuple[str, float]] = []
    prev = levels[0]
    for m in months:
        lvl = month_end[m][1]
        out.append((m, (lvl - prev) / prev * 100.0 if prev else 0.0))
        prev = lvl
    return out


def render_timeline_report(
    assets: list[tuple[str, list[Candle]]], swing_pct: float = 15.0, label: str = "",
) -> str:
    ts, basket, norm = basket_index(assets)
    if not ts:
        return "no common window across the assets — check the CSVs / date filter."

    lines = [
        f"# Price timeline — {len(assets)} coins {label}".rstrip(),
        f"equal-weight basket index (base 100 at {_day(ts[0])}) | swing threshold {swing_pct:.0f}%",
        f"window: {_day(ts[0])} -> {_day(ts[-1])} ({len(ts)} days)",
        "",
        "## Month-by-month (basket %, coins up, best / worst)",
        "month   | basket% | up/total | best        | worst",
    ]
    # per-coin monthly returns for best/worst + up-count
    coin_monthly = {c: dict(_monthly(ts, s)) for c, s in norm.items()}
    for m, r in _monthly(ts, basket):
        month_moves = [(c, coin_monthly[c].get(m, 0.0)) for c in norm]
        up = sum(1 for _, x in month_moves if x > 0)
        best = max(month_moves, key=lambda kv: kv[1])
        worst = min(month_moves, key=lambda kv: kv[1])
        lines.append(f"{m} | {r:+7.1f} | {up:2d}/{len(norm):<5d} | "
                     f"{best[0]:<4s} {best[1]:+5.0f} | {worst[0]:<4s} {worst[1]:+5.0f}")

    legs = swings(ts, basket, swing_pct)
    corrections = [l for l in legs if l.kind == "correction"]
    rallies = [l for l in legs if l.kind == "rally"]

    lines += ["", f"## Corrections (basket drops >= {swing_pct:.0f}%, deepest first)"]
    if corrections:
        for l in sorted(corrections, key=lambda x: x.pct)[:10]:
            lines.append(f"- {_day(l.start_ts)} -> {_day(l.end_ts)} : {l.pct:+.1f}% over {l.days}d")
    else:
        lines.append(f"- none exceeded {swing_pct:.0f}% in this window")

    lines += ["", f"## Rallies (basket gains >= {swing_pct:.0f}%, biggest first)"]
    if rallies:
        for l in sorted(rallies, key=lambda x: -x.pct)[:10]:
            lines.append(f"- {_day(l.start_ts)} -> {_day(l.end_ts)} : {l.pct:+.1f}% over {l.days}d")
    else:
        lines.append(f"- none exceeded {swing_pct:.0f}% in this window")

    net = (basket[-1] - basket[0]) / basket[0] * 100.0
    neg_months = sum(1 for _, r in _monthly(ts, basket) if r < 0)
    total_months = len(_monthly(ts, basket))
    lines += ["", "## Summary",
              f"- basket net over window: {net:+.1f}%  ({neg_months}/{total_months} months negative)",
              f"- {len(corrections)} correction(s) and {len(rallies)} rally(s) above the "
              f"{swing_pct:.0f}% threshold — this is the alternating chop the bot sits through.",
              "- DESCRIPTIVE only: past timing does not predict future timing. Markets are never "
              "'due' for a move; use this to know the SHAPE of a regime, not to call the next one."]
    return "\n".join(lines)

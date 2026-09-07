"""Cross-sectional momentum gate — deploy sleeves only into the strongest coins.

The single-coin research showed the Donchian edge needs an asset that is
actually trending (XRP/LINK, which ranged, failed 1/5 segments). This overlay
attacks that failure mode directly: at each decision bar, rank the basket by
trailing N-bar return and allow NEW entries only in the top-K coins. Classic
cross-sectional momentum — the best-documented return premium in crypto.

Design rules (same as the funding/MVRV governors):
  - entries only: a coin dropping out of the top-K never force-exits an open
    position — the Donchian channel remains the exit authority (an exit signal
    always passes through untouched);
  - strictly causal: the rank at bar N uses closes up to and including bar N —
    the same information set the wrapped strategy decides on (the backtest
    engine fills at bar N+1's open);
  - coins without enough history to compute the trailing return are simply not
    rankable (never in the top-K) until `lookback` bars exist.

Validated through the portfolio backtest BEFORE any live wiring.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from ..domain import OrderIntent
from ..exchange.models import Candle
from .base import MarketData, Strategy


@dataclass
class MomentumRank:
    """Precomputed per-timestamp top-K membership over an ALIGNED candle set
    (all labels sharing the same timestamp axis — the portfolio backtest's
    ``align_on_common_timestamps`` output)."""

    timestamps: list[int]
    allowed_sets: list[frozenset[str]]
    lookback: int
    top_k: int

    @classmethod
    def build(
        cls, aligned: dict[str, list[Candle]], lookback: int, top_k: int
    ) -> "MomentumRank":
        labels = sorted(aligned)
        if not labels or lookback <= 0 or top_k <= 0:
            return cls([], [], lookback, top_k)
        ts_list = [c.timestamp for c in aligned[labels[0]]]
        closes = {lab: [c.close for c in cs] for lab, cs in aligned.items()}
        allowed: list[frozenset[str]] = []
        for j in range(len(ts_list)):
            if j < lookback:
                allowed.append(frozenset())  # nobody rankable yet
                continue
            rets: list[tuple[float, str]] = []
            for lab in labels:
                prev = closes[lab][j - lookback]
                if prev > 0:
                    rets.append((closes[lab][j] / prev - 1.0, lab))
            rets.sort(key=lambda r: (-r[0], r[1]))  # best first; label-stable ties
            allowed.append(frozenset(lab for _, lab in rets[:top_k]))
        return cls(ts_list, allowed, lookback, top_k)

    def allowed(self, label: str, ts: int) -> bool:
        """Is ``label`` in the top-K at the latest rank AT or BEFORE ``ts``?"""
        i = bisect.bisect_right(self.timestamps, ts) - 1
        if i < 0:
            return False
        return label in self.allowed_sets[i]


class MomentumGatedStrategy(Strategy):
    """Wrap any base strategy: suppress NEW entries when this sleeve's coin is
    not in the basket's top-K by trailing return. Exits always pass."""

    name = "momentum_gated"

    def __init__(self, base: Strategy, label: str, rank: MomentumRank) -> None:
        self.base = base
        self.label = label
        self.rank = rank

    def generate_signals(self, market: MarketData) -> list[OrderIntent]:
        signals = self.base.generate_signals(market)
        if not signals or not market.candles:
            return signals
        ts = market.candles[-1].timestamp
        out: list[OrderIntent] = []
        for s in signals:
            if s.is_entry and not self.rank.allowed(self.label, ts):
                continue  # not among the strongest coins — stand aside
            out.append(s)
        return out

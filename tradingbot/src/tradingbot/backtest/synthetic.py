"""Deterministic synthetic OHLCV generator.

For demos and reproducible reports ONLY — it is a seeded random walk, not market
data, and says nothing about real performance. Use real exchange OHLCV for any
decision-relevant backtest (see the README backtest workflow).
"""

from __future__ import annotations

import random

from ..exchange.models import Candle


def synthetic_candles(
    n: int = 500, seed: int = 42, start: float = 100.0, vol: float = 0.012, drift: float = 0.0002
) -> list[Candle]:
    rng = random.Random(seed)
    candles: list[Candle] = []
    close = start
    ts = 1_700_000_000_000
    for _ in range(n):
        open_ = close
        ret = drift + rng.gauss(0.0, vol)
        close = max(0.01, open_ * (1 + ret))
        high = max(open_, close) * (1 + abs(rng.gauss(0.0, vol / 2)))
        low = min(open_, close) * (1 - abs(rng.gauss(0.0, vol / 2)))
        volume = abs(rng.gauss(100.0, 20.0))
        candles.append(Candle(timestamp=ts, open=open_, high=high, low=low, close=close, volume=volume))
        ts += 60_000
    return candles

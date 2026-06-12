"""OHLCV CSV load/save for offline backtesting and walk-forward.

CSV columns: timestamp,open,high,low,close,volume  (timestamp in ms).
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..exchange.models import Candle

_HEADER = ["timestamp", "open", "high", "low", "close", "volume"]


def save_csv(path: str | Path, candles: list[Candle]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_HEADER)
        for c in candles:
            w.writerow([c.timestamp, c.open, c.high, c.low, c.close, c.volume])


def load_csv(path: str | Path) -> list[Candle]:
    out: list[Candle] = []
    with Path(path).open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out.append(Candle(
                timestamp=int(float(row["timestamp"])),
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                volume=float(row.get("volume") or 0.0),
            ))
    return out

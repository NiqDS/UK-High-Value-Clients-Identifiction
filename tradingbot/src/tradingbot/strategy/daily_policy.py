"""Daily signal checks. Prices used for a signal do not set the order budget."""

from __future__ import annotations

import hashlib
import math
import sqlite3
from pathlib import Path

DAY_MS = 86_400_000


def finite(value: float, *, positive: bool = False) -> float:
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError("Invalid account or market number")
    return value


def validate_daily_window(candles, now_ms: int, required: int) -> None:
    if len(candles) < required:
        raise ValueError("Insufficient completed daily bars")
    bars = candles[-required:]
    expected_last = (now_ms // DAY_MS - 1) * DAY_MS
    if bars[-1].timestamp != expected_last:
        raise ValueError("Daily data is stale or has an invalid final timestamp")
    for i, bar in enumerate(bars):
        if bar.timestamp % DAY_MS or (i and bar.timestamp - bars[i - 1].timestamp != DAY_MS):
            raise ValueError("Daily data has a missing, duplicate, or unordered bar")
        for value in [bar.open, bar.high, bar.low, bar.close]:
            finite(value, positive=True)
        finite(bar.volume)
        if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise ValueError("Invalid daily OHLC range")


def signal_identity(symbol: str, bar_open: int, entry: int, exit: int) -> str:
    text = f"daily-v1|{symbol}|1d|{entry}|{exit}|{bar_open}|buy"
    return "tb-" + hashlib.sha256(text.encode()).hexdigest()[:24]


class EntryClaims:
    """Claim an accepted entry before submission. An uncertain claim stays used.

    This is a signal guard. Full order/fill recovery remains a separate control.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS entry_claims "
                "(id TEXT PRIMARY KEY, symbol TEXT NOT NULL)"
            )

    def claim(self, intent) -> bool:
        if not intent.metadata.get("daily_policy"):
            return True
        if not intent.client_order_id:
            raise ValueError("Daily entry lacks a durable identity")
        with sqlite3.connect(self.path) as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO entry_claims VALUES (?, ?)",
                (intent.client_order_id, intent.symbol),
            )
            return cursor.rowcount == 1

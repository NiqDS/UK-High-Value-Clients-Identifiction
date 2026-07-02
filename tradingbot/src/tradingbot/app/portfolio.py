"""Position tracking for the live loop.

Long-only, one position per symbol (matching the reference strategy): a BUY
opens, a SELL closes and realises P&L. Drives the strategy's ``holding`` flag
(so the VWAP force-exit can fire live) and the take-profit / stop monitor.

State is persisted through :class:`JsonPositionStore` — a restart that forgot
its open positions would leave real coins with no stop and no exit, and re-buy
on the next signal (doubling exposure), so the tracker must survive the process.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..domain import Side

logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    units: float
    entry_price: float
    entry_ts: datetime
    take_profit: float | None = None
    stop: float | None = None
    trail_distance: float | None = None


@dataclass
class PositionTracker:
    positions: dict[str, Position] = field(default_factory=dict)

    def holding(self, symbol: str) -> bool:
        return symbol in self.positions

    def get(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)

    def on_fill(
        self, symbol: str, side: Side, price: float, amount: float, ts: datetime,
        take_profit: float | None = None, stop: float | None = None,
        trail_distance: float | None = None,
    ) -> float:
        """Apply a fill. Returns realised gross P&L (0 for an opening buy)."""
        pos = self.positions.get(symbol)
        if side == Side.BUY:
            if pos is None:
                self.positions[symbol] = Position(
                    symbol=symbol, units=amount, entry_price=price, entry_ts=ts,
                    take_profit=take_profit, stop=stop, trail_distance=trail_distance,
                )
            else:  # average up (rare for the reference strategy)
                total = pos.units + amount
                pos.entry_price = (pos.entry_price * pos.units + price * amount) / total
                pos.units = total
            return 0.0
        # SELL — close (fully or partially)
        if pos is None:
            return 0.0
        closed = min(amount, pos.units)
        realized = (price - pos.entry_price) * closed
        pos.units -= closed
        if pos.units <= 1e-12:
            del self.positions[symbol]
        return realized


class JsonPositionStore:
    """Persist the tracker to a JSON file (atomic replace) so open positions
    survive restarts. Load errors degrade to an empty tracker with a LOUD
    warning — better to know than to silently trade against phantom state."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> PositionTracker:
        tracker = PositionTracker()
        if not self._path.exists():
            return tracker
        try:
            data = json.loads(self._path.read_text())
            for row in data.get("positions", []):
                pos = Position(
                    symbol=row["symbol"], units=float(row["units"]),
                    entry_price=float(row["entry_price"]),
                    entry_ts=datetime.fromisoformat(row["entry_ts"]),
                    take_profit=row.get("take_profit"), stop=row.get("stop"),
                    trail_distance=row.get("trail_distance"),
                )
                if pos.units > 0:
                    tracker.positions[pos.symbol] = pos
            if tracker.positions:
                logger.warning(
                    "Rehydrated %d open position(s) from %s: %s",
                    len(tracker.positions), self._path,
                    ", ".join(f"{p.symbol}={p.units:.8f}@{p.entry_price:.4f}"
                              for p in tracker.positions.values()),
                )
        except Exception:
            logger.exception(
                "CORRUPT position store %s — starting with an EMPTY tracker. If the "
                "bot holds real positions, STOP and reconcile manually before trading.",
                self._path,
            )
        return tracker

    def save(self, tracker: PositionTracker) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "positions": [
                    {
                        "symbol": p.symbol, "units": p.units,
                        "entry_price": p.entry_price,
                        "entry_ts": p.entry_ts.isoformat(),
                        "take_profit": p.take_profit, "stop": p.stop,
                        "trail_distance": p.trail_distance,
                    }
                    for p in tracker.positions.values()
                ],
            }
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2))
            tmp.replace(self._path)  # atomic on POSIX
        except Exception:  # persistence must never kill the trading loop
            logger.exception("could not save position state to %s", self._path)

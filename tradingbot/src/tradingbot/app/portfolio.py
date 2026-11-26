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
    cost_basis: float | None = None


@dataclass
class PositionTracker:
    positions: dict[str, Position] = field(default_factory=dict)
    cash_quote: float | None = None

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
    survive restarts. Strict mode refuses corrupt live state. The default
    retains the permissive loader for paper tools and backwards compatibility."""

    def __init__(self, path: str | Path, *, strict: bool = False) -> None:
        self._path = Path(path)
        self._strict = strict

    def load(self) -> PositionTracker:
        tracker = PositionTracker()
        if not self._path.exists():
            return tracker
        try:
            data = json.loads(self._path.read_text())
            if not isinstance(data,dict) or not isinstance(data.get('positions',[]),list):
                raise ValueError('Invalid position document')
            tracker.cash_quote = data.get('cash_quote')
            if tracker.cash_quote is not None:
                import math
                if not math.isfinite(tracker.cash_quote) or tracker.cash_quote < 0:
                    raise ValueError('Invalid saved cash')
            for row in data.get("positions", []):
                pos = Position(
                    symbol=row["symbol"], units=float(row["units"]),
                    entry_price=float(row["entry_price"]),
                    entry_ts=datetime.fromisoformat(row["entry_ts"]),
                    take_profit=row.get("take_profit"), stop=row.get("stop"),
                    trail_distance=row.get("trail_distance"), cost_basis=row.get("cost_basis"),
                )
                from ..strategy.daily_policy import finite
                finite(pos.units, positive=True)
                finite(pos.entry_price, positive=True)
                if pos.entry_ts.tzinfo is None or pos.symbol in tracker.positions:
                    raise ValueError('Invalid position time or duplicate symbol')
                if not isinstance(pos.symbol,str) or '/' not in pos.symbol:
                    raise ValueError('Invalid position symbol')
                for value in [pos.stop,pos.take_profit,pos.trail_distance,pos.cost_basis]:
                    if value is not None:
                        finite(value,positive=True)
                tracker.positions[pos.symbol] = pos
            if tracker.positions:
                logger.warning(
                    "Rehydrated %d open position(s) from %s: %s",
                    len(tracker.positions), self._path,
                    ", ".join(f"{p.symbol}={p.units:.8f}@{p.entry_price:.4f}"
                              for p in tracker.positions.values()),
                )
        except Exception as exc:
            if self._strict:
                raise ValueError(f'Invalid live position store {self._path}; recovery required') from exc
            logger.exception(
                "CORRUPT position store %s — starting with an EMPTY tracker. If the "
                "bot holds real positions, STOP and reconcile manually before trading.",
                self._path,
            )
        return tracker

    def save(self, tracker: PositionTracker) -> None:
        try:
            from ..strategy.daily_policy import finite
            if tracker.cash_quote is not None:
                finite(tracker.cash_quote)
            for pos in tracker.positions.values():
                finite(pos.units,positive=True)
                finite(pos.entry_price,positive=True)
                if pos.entry_ts.tzinfo is None:
                    raise ValueError('Position timestamp is not timezone-aware')
                for value in [pos.stop,pos.take_profit,pos.trail_distance,pos.cost_basis]:
                    if value is not None:
                        finite(value,positive=True)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "cash_quote": tracker.cash_quote,
                "positions": [
                    {
                        "symbol": p.symbol, "units": p.units,
                        "entry_price": p.entry_price,
                        "entry_ts": p.entry_ts.isoformat(),
                        "take_profit": p.take_profit, "stop": p.stop,
                        "trail_distance": p.trail_distance, "cost_basis": p.cost_basis,
                    }
                    for p in tracker.positions.values()
                ],
            }
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2))
            tmp.replace(self._path)  # atomic on POSIX
        except Exception:
            logger.exception("could not save position state to %s", self._path)
            raise

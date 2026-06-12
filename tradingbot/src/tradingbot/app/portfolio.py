"""In-memory position tracking for the live loop.

Long-only, one position per symbol (matching the reference strategy): a BUY
opens, a SELL closes and realises P&L. Drives the strategy's ``holding`` flag
(so the VWAP force-exit can fire live) and the take-profit / stop monitor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..domain import Side


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

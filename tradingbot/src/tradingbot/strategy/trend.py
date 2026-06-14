"""Donchian-channel breakout — the trend-following thesis.

Research on this platform showed mean-reversion is positive on mean-reverting
equity indices (SPY/QQQ) but loses badly on BTC, because **BTC trends**. This
strategy is the trend-following counterpart: it goes long when price breaks out
above the prior N-bar high (momentum/continuation) and exits when price breaks
below the prior M-bar low (the channel stop). Long-only, no fixed take-profit —
it rides the trend and lets the lower channel end it. Classic Turtle logic.

Plugs into the same backtest / compare / walkforward / robustness harness.
"""

from __future__ import annotations

from ..config import StrategyConfig
from ..domain import OrderIntent, OrderType, Side
from .base import MarketData, Strategy


class DonchianBreakoutStrategy(Strategy):
    name = "donchian_breakout"

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def generate_signals(self, market: MarketData) -> list[OrderIntent]:
        cfg = self.config
        candles = market.candles
        n_entry, n_exit = cfg.donchian_entry_period, cfg.donchian_exit_period
        if len(candles) < max(n_entry, n_exit) + 1:
            return []
        price = market.last_price
        if not price or price <= 0:
            return []

        # Channels from the PRIOR bars only (exclude the current bar -> no look-ahead).
        prior_high = max(c.high for c in candles[-n_entry - 1:-1])
        prior_low = min(c.low for c in candles[-n_exit - 1:-1])
        meta = {"donchian_high": prior_high, "donchian_low": prior_low}

        if market.holding:
            # Exit when price breaks below the lower channel (trend over).
            if price < prior_low:
                return [OrderIntent(
                    symbol=market.symbol, side=Side.SELL,
                    amount=cfg.target_notional_quote / price, order_type=OrderType.MARKET,
                    price=price, is_entry=False, reason="donchian channel exit", metadata=meta)]
            return []

        # Entry: breakout above the upper channel. Protective stop at the lower
        # channel (the natural trend-following stop); no take-profit — ride it.
        if price > prior_high:
            return [OrderIntent(
                symbol=market.symbol, side=Side.BUY,
                amount=cfg.target_notional_quote / price, order_type=OrderType.LIMIT,
                price=price, stop_price=min(prior_low, price * (1 - cfg.stop_loss_pct / 100.0)),
                is_entry=True, reason="donchian breakout buy", metadata=meta)]
        return []

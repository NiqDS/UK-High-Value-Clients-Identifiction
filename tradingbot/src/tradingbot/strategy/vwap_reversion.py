"""VWAP mean-reversion strategy — the 'volatility thesis' hypothesis.

Instead of predicting direction (what a moving-average crossover does), this
fades *deviations from fair value*: it buys when price has dropped a configurable
amount below its volume-weighted average cost (VWAP) and exits when price reverts
back to VWAP. The bet is on mean reversion of the deviation, not on trend — which
aligns with the brief's principle that volatility is more predictable than
direction. It reuses the same VWAP and force-exit machinery as the SMA strategy.
"""

from __future__ import annotations

from ..config import StrategyConfig
from ..domain import OrderIntent, OrderType, Side
from .base import MarketData, Strategy
from .sma import _valuation_label, vwap


class VwapReversionStrategy(Strategy):
    name = "vwap_reversion"

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def generate_signals(self, market: MarketData) -> list[OrderIntent]:
        cfg = self.config
        closes = market.closes
        if len(closes) < max(cfg.vwap_window, 2):
            return []
        price = market.last_price
        if not price or price <= 0:
            return []
        wac = vwap(market.candles, cfg.vwap_window)
        if not wac:
            return []
        valuation_pct = (price - wac) / wac * 100.0
        meta = {"vwap": wac, "valuation_pct": valuation_pct,
                "valuation": _valuation_label(valuation_pct)}

        # Manage an open position: exit on reversion to fair value, or force-exit
        # if it runs far above VWAP.
        if market.holding:
            if valuation_pct >= cfg.force_exit_overvaluation_pct:
                return [OrderIntent(
                    symbol=market.symbol, side=Side.SELL,
                    amount=cfg.target_notional_quote / price, order_type=OrderType.MARKET,
                    price=price, is_entry=False,
                    reason=f"force exit: overvalued {valuation_pct:+.2f}%",
                    metadata={**meta, "emergency": True})]
            if price >= wac:  # reverted to/above the weighted-average cost
                return [OrderIntent(
                    symbol=market.symbol, side=Side.SELL,
                    amount=cfg.target_notional_quote / price, order_type=OrderType.LIMIT,
                    price=price, is_entry=False, reason="reverted to VWAP", metadata=meta)]
            return []

        # Entry: price is sufficiently below the weighted-average cost — fade it.
        if valuation_pct <= -cfg.reversion_entry_pct:
            return [OrderIntent(
                symbol=market.symbol, side=Side.BUY,
                amount=cfg.target_notional_quote / price, order_type=OrderType.LIMIT,
                price=price, take_profit_price=wac,  # target = reversion to fair value
                stop_price=price * (1 - cfg.stop_loss_pct / 100.0),
                is_entry=True, reason="vwap reversion buy", metadata=meta)]
        return []

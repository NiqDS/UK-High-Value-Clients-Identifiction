"""Reference strategy: simple moving-average (SMA) crossover.

Deliberately simple — its purpose is to exercise the full pipeline
(strategy → risk → fee gate → execution), not to be a profitable edge.
Validate any real strategy out-of-sample and net of fees (see README) before
trusting it.

Signals:
  - bullish cross (fast crosses above slow) → BUY entry, with take-profit and
    stop derived from config. The take-profit must clear round-trip fees or the
    risk engine's fee gate will (correctly) reject it.
  - bearish cross (fast crosses below slow) → SELL exit (is_entry=False).
"""

from __future__ import annotations

from ..config import StrategyConfig
from ..domain import OrderIntent, OrderType, Side
from .base import MarketData, Strategy


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


class SmaCrossoverStrategy(Strategy):
    name = "sma_crossover"

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def generate_signals(self, market: MarketData) -> list[OrderIntent]:
        cfg = self.config
        closes = market.closes
        # Need one extra bar to compare "previous" vs "now" SMAs.
        if len(closes) < cfg.slow_period + 1:
            return []

        fast_now = sma(closes, cfg.fast_period)
        slow_now = sma(closes, cfg.slow_period)
        fast_prev = sma(closes[:-1], cfg.fast_period)
        slow_prev = sma(closes[:-1], cfg.slow_period)
        if None in (fast_now, slow_now, fast_prev, slow_prev):
            return []

        price = market.last_price
        if not price or price <= 0:
            return []

        bullish = fast_prev <= slow_prev and fast_now > slow_now
        bearish = fast_prev >= slow_prev and fast_now < slow_now

        if bullish:
            amount = cfg.target_notional_quote / price
            return [
                OrderIntent(
                    symbol=market.symbol,
                    side=Side.BUY,
                    amount=amount,
                    order_type=OrderType.LIMIT,
                    price=price,
                    take_profit_price=price * (1 + cfg.take_profit_pct / 100.0),
                    stop_price=price * (1 - cfg.stop_loss_pct / 100.0),
                    is_entry=True,
                    reason="sma bullish crossover",
                )
            ]
        if bearish:
            amount = cfg.target_notional_quote / price
            return [
                OrderIntent(
                    symbol=market.symbol,
                    side=Side.SELL,
                    amount=amount,
                    order_type=OrderType.LIMIT,
                    price=price,
                    is_entry=False,
                    reason="sma bearish crossover",
                )
            ]
        return []

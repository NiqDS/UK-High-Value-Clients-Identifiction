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

Valuation criterion (weighted average cost):
  Every signal also assesses over/under-valuation against the VWAP — the
  volume-weighted average price, i.e. the weighted average cost the market has
  paid over the window. Price below VWAP = undervalued; above = overvalued. A
  bullish entry that is richly overvalued (more than ``max_overvaluation_pct``
  above VWAP) is skipped — don't buy what the market is already paying up for.
  The assessment rides along on the intent (for the Telegram message); the
  intermediate calculation is deliberately not logged.
"""

from __future__ import annotations

from ..config import StrategyConfig
from ..domain import OrderIntent, OrderType, Side
from .base import MarketData, Strategy
from ..exchange.models import Candle


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def vwap(candles: list[Candle], window: int) -> float | None:
    """Volume-weighted average price (weighted average cost) over the last
    ``window`` bars, using each bar's typical price (H+L+C)/3. Falls back to an
    unweighted mean if volume is unavailable."""
    sub = candles[-window:] if window > 0 else candles
    if not sub:
        return None
    typical = [(c.high + c.low + c.close) / 3 for c in sub]
    total_vol = sum(c.volume for c in sub)
    if total_vol <= 0:
        return sum(typical) / len(typical)
    return sum(t * c.volume for t, c in zip(typical, sub)) / total_vol


def _valuation_label(pct: float) -> str:
    if pct <= -0.1:
        return "undervalued"
    if pct >= 0.1:
        return "overvalued"
    return "fair"


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

        # Weighted-average-cost valuation (VWAP). Computed quietly — not logged.
        wac = vwap(market.candles, cfg.vwap_window)
        valuation_pct = (price - wac) / wac * 100.0 if wac else 0.0
        valuation_meta = {
            "vwap": wac,
            "valuation_pct": valuation_pct,
            "valuation": _valuation_label(valuation_pct),
        }

        if bullish:
            # Don't buy what's already richly overvalued vs its weighted avg cost.
            if cfg.vwap_filter_enabled and valuation_pct > cfg.max_overvaluation_pct:
                return []
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
                    metadata=valuation_meta,
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
                    metadata=valuation_meta,
                )
            ]
        return []

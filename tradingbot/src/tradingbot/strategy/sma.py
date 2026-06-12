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
  paid over the window. Price below VWAP = undervalued; above = overvalued.
  BUY only at/below the undervaluation floor (``buy_valuation_floor_pct`` vs
  VWAP); FORCE-EXIT a held position (emergency market order) once it runs above
  the overvaluation ceiling (``force_exit_overvaluation_pct`` above VWAP).
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


def atr(candles: list[Candle], period: int) -> float | None:
    """Average True Range over the last ``period`` bars (volatility measure)."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, low, prev_close = candles[i].high, candles[i].low, candles[i - 1].close
        trs.append(max(h - low, abs(h - prev_close), abs(low - prev_close)))
    return sum(trs[-period:]) / period


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
        self._last_entry_len: int | None = None  # bar count at last entry (cooldown)

    # -- sizing / exit helpers ---------------------------------------------
    def _size_amount(self, price: float, valuation_pct: float) -> float:
        cfg = self.config
        base = cfg.target_notional_quote / price
        if cfg.sizing_mode != "vwap_scaled":
            return base
        # the deeper below VWAP (more undervalued), the larger the size
        discount = max(0.0, -valuation_pct)
        frac = min(1.0, discount / cfg.vwap_full_size_discount_pct)
        mult = cfg.vwap_size_min_mult + (cfg.vwap_size_max_mult - cfg.vwap_size_min_mult) * frac
        return base * mult

    def _exits(self, price: float, candles: list[Candle]) -> tuple[float, float, float | None]:
        cfg = self.config
        if cfg.exit_mode == "atr":
            a = atr(candles, cfg.atr_period)
            if a:
                tp = price + cfg.atr_tp_mult * a
                sl = price - cfg.atr_sl_mult * a
                trail = cfg.trailing_atr_mult * a if cfg.trailing_enabled else None
                return tp, sl, trail
        # fixed-percent fallback
        return (price * (1 + cfg.take_profit_pct / 100.0),
                price * (1 - cfg.stop_loss_pct / 100.0), None)

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

        # FORCE EXIT: a held position that has run too far above its weighted
        # average cost is exited immediately (emergency), regardless of crossover.
        if (
            cfg.vwap_filter_enabled
            and market.holding
            and valuation_pct >= cfg.force_exit_overvaluation_pct
        ):
            return [
                OrderIntent(
                    symbol=market.symbol,
                    side=Side.SELL,
                    amount=cfg.target_notional_quote / price,
                    order_type=OrderType.MARKET,  # cross the spread to exit now
                    price=price,
                    is_entry=False,
                    reason=f"force exit: overvalued {valuation_pct:+.2f}% >= "
                           f"{cfg.force_exit_overvaluation_pct}% ceiling",
                    metadata={**valuation_meta, "emergency": True},
                )
            ]

        if bullish:
            # BUY only when sufficiently undervalued: price at/below the floor vs VWAP.
            if cfg.vwap_filter_enabled and valuation_pct > cfg.buy_valuation_floor_pct:
                return []
            # fee-drag: require a strong-enough crossover and respect a cooldown
            strength = (fast_now - slow_now) / slow_now * 100.0 if slow_now else 0.0
            if strength < cfg.min_crossover_strength_pct:
                return []
            n = len(closes)
            if (
                self._last_entry_len is not None
                and n - self._last_entry_len < cfg.trade_cooldown_bars
            ):
                return []

            amount = self._size_amount(price, valuation_pct)
            tp, sl, trail = self._exits(price, market.candles)
            self._last_entry_len = n
            meta = dict(valuation_meta)
            if trail is not None:
                meta["trail_distance"] = trail
            return [
                OrderIntent(
                    symbol=market.symbol,
                    side=Side.BUY,
                    amount=amount,
                    order_type=OrderType.LIMIT,
                    price=price,
                    take_profit_price=tp,
                    stop_price=sl,
                    is_entry=True,
                    reason="sma bullish crossover",
                    metadata=meta,
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

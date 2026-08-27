"""SMA crossover reference-strategy tests."""

from __future__ import annotations

import pytest

from tradingbot.config import StrategyConfig
from tradingbot.domain import Side
from tradingbot.exchange.models import Candle, Ticker
from tradingbot.strategy.base import MarketData
from tradingbot.strategy.sma import SmaCrossoverStrategy, sma, vwap


def candles(closes: list[float], volumes: list[float] | None = None) -> list[Candle]:
    vols = volumes or [1.0] * len(closes)
    return [Candle(timestamp=i, open=c, high=c, low=c, close=c, volume=v)
            for i, (c, v) in enumerate(zip(closes, vols))]


def market(closes: list[float], volumes: list[float] | None = None,
           holding: bool = False) -> MarketData:
    last = closes[-1]
    return MarketData(
        symbol="BTC/USD",
        candles=candles(closes, volumes),
        ticker=Ticker("BTC/USD", bid=last, ask=last, last=last,
                      base_volume=1, quote_volume=1, timestamp=0),
        holding=holding,
    )


def strat(vwap_filter: bool = False, buy_floor: float = 1.0,
          force_exit: float = 3.0) -> SmaCrossoverStrategy:
    # crossover tests disable the VWAP filter to isolate crossover logic
    return SmaCrossoverStrategy(StrategyConfig(
        fast_period=2, slow_period=3, vwap_filter_enabled=vwap_filter,
        buy_valuation_floor_pct=buy_floor, force_exit_overvaluation_pct=force_exit,
    ))


def test_sma_helper() -> None:
    assert sma([1, 2, 3, 4], 2) == 3.5  # mean of last 2
    assert sma([1], 2) is None


def test_insufficient_history_no_signal() -> None:
    assert strat().generate_signals(market([10, 9, 8])) == []  # < slow+1


def test_bullish_crossover_emits_buy_entry() -> None:
    signals = strat().generate_signals(market([10, 9, 8, 7, 12]))
    assert len(signals) == 1
    s = signals[0]
    assert s.side is Side.BUY and s.is_entry is True
    assert s.take_profit_price == pytest.approx(12 * 1.015)
    assert s.stop_price == pytest.approx(12 * 0.99)
    assert s.amount == pytest.approx(40.0 / 12)  # target_notional / price
    # valuation metadata always attached for the Telegram message
    assert "vwap" in s.metadata and "valuation" in s.metadata


# --- VWAP weighted-average-cost valuation ----------------------------------
def test_vwap_weights_by_volume() -> None:
    # two bars: price 100 (vol 1) and 200 (vol 9) -> VWAP pulled toward 200
    cs = candles([100, 200], volumes=[1, 9])
    assert vwap(cs, window=2) == pytest.approx((100 * 1 + 200 * 9) / 10)


def test_vwap_falls_back_to_mean_without_volume() -> None:
    cs = candles([100, 110, 120], volumes=[0, 0, 0])
    assert vwap(cs, window=3) == pytest.approx((100 + 110 + 120) / 3)


def test_overvalued_bullish_entry_is_skipped() -> None:
    # bullish cross but last price (12) sits ~30% above VWAP (~9.2) -> above floor
    s = strat(vwap_filter=True, buy_floor=1.0)
    assert s.generate_signals(market([10, 9, 8, 7, 12])) == []


def test_undervalued_bullish_entry_passes_floor() -> None:
    # bullish cross where the entry price is at/below VWAP (undervalued)
    s = strat(vwap_filter=True, buy_floor=1.0)
    sigs = s.generate_signals(market([120, 118, 90, 88, 100]))
    assert len(sigs) == 1
    assert sigs[0].side is Side.BUY
    assert sigs[0].metadata["valuation"] in ("undervalued", "fair")


def test_sell_exit_carries_valuation_but_is_not_filtered() -> None:
    s = strat(vwap_filter=True)
    sigs = s.generate_signals(market([7, 8, 9, 10, 5]))
    assert len(sigs) == 1
    assert sigs[0].side is Side.SELL
    assert "valuation" in sigs[0].metadata


# --- force exit on the overvaluation ceiling -------------------------------
def test_force_exit_when_holding_and_overvalued() -> None:
    # holding a position; price (110) is ~7.8% above VWAP (~102), ceiling 3%
    s = strat(vwap_filter=True, force_exit=3.0)
    sigs = s.generate_signals(market([100, 100, 100, 100, 110], holding=True))
    assert len(sigs) == 1
    e = sigs[0]
    assert e.side is Side.SELL and e.is_entry is False
    assert e.metadata.get("emergency") is True
    assert e.order_type.value == "market"  # crosses the spread to exit now


def test_no_force_exit_when_flat() -> None:
    # same overvalued bar but no open position -> no emergency exit
    s = strat(vwap_filter=True, force_exit=3.0, buy_floor=1.0)
    sigs = s.generate_signals(market([100, 100, 100, 100, 110], holding=False))
    assert all(not sig.metadata.get("emergency") for sig in sigs)


def test_force_exit_disabled_when_filter_off() -> None:
    s = strat(vwap_filter=False, force_exit=3.0)
    sigs = s.generate_signals(market([100, 100, 100, 100, 110], holding=True))
    assert all(not sig.metadata.get("emergency") for sig in sigs)


def test_bearish_crossover_emits_sell_exit() -> None:
    signals = strat().generate_signals(market([7, 8, 9, 10, 5]))
    assert len(signals) == 1
    s = signals[0]
    assert s.side is Side.SELL and s.is_entry is False
    assert s.take_profit_price is None  # exits don't carry a profit target


def test_no_crossover_no_signal() -> None:
    assert strat().generate_signals(market([1, 2, 3, 4, 5])) == []

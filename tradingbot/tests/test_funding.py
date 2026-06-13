"""Funding-rate parsing, series lookup, overlay response, and strategy wrapper."""

from __future__ import annotations

import pytest

from tradingbot.config import FundingConfig
from tradingbot.domain import OrderIntent, OrderType, Side
from tradingbot.exchange.funding import FundingRate, parse_vision_funding
from tradingbot.exchange.models import Candle, Ticker
from tradingbot.strategy.base import MarketData, Strategy
from tradingbot.strategy.funding import FundingFilteredStrategy, FundingOverlay, FundingSeries

_8H = 8 * 3600 * 1000


# --- parsing ---------------------------------------------------------------
def test_parse_vision_funding_with_header() -> None:
    text = ("calc_time,funding_interval_hours,last_funding_rate\n"
            "1704067200000,8,0.0001\n"
            "1704096000000,8,-0.0002\n")
    rates = parse_vision_funding(text)
    assert len(rates) == 2
    assert rates[0].timestamp == 1704067200000 and rates[0].rate == 0.0001
    assert rates[1].rate == -0.0002


def test_parse_vision_funding_microseconds() -> None:
    text = "1704067200000000,8,0.0001\n"  # microsecond timestamp, no header
    assert parse_vision_funding(text)[0].timestamp == 1704067200000


# --- series no-look-ahead --------------------------------------------------
def test_series_up_to_is_causal() -> None:
    rates = [FundingRate(i * _8H, 0.0001 * i) for i in range(10)]
    s = FundingSeries(rates)
    got = s.up_to(3 * _8H)
    assert len(got) == 4  # indices 0..3 (<= ts)
    assert got[-1].timestamp == 3 * _8H


# --- overlay ---------------------------------------------------------------
def cfg(**kw) -> FundingConfig:
    base = dict(enabled=True, z_window=5, extreme_z=1.5, min_mult=0.25, max_mult=1.0)
    base.update(kw)
    return FundingConfig(**base)


def _rates(values: list[float]) -> list[FundingRate]:
    return [FundingRate(i * _8H, v) for i, v in enumerate(values)]


def test_overlay_neutral_with_insufficient_history() -> None:
    st = FundingOverlay(cfg()).assess(_rates([0.0001, 0.0001]))
    assert st.multiplier == 1.0 and st.z == 0.0


def test_overlay_crowded_longs_scale_down() -> None:
    # flat funding then a big positive spike -> high z -> low multiplier, crowded
    st = FundingOverlay(cfg()).assess(_rates([0.0001] * 6 + [0.01]))
    assert st.z > 1.5
    assert st.crowded is True
    assert st.multiplier < 0.5


def test_overlay_crowded_shorts_scale_up() -> None:
    # flat then a big negative spike -> negative z -> high multiplier, favorable
    st = FundingOverlay(cfg()).assess(_rates([0.0001] * 6 + [-0.01]))
    assert st.z < -1.5
    assert st.crowded is False
    assert st.multiplier > 0.9


def test_overlay_multiplier_respects_bounds() -> None:
    o = FundingOverlay(cfg(min_mult=0.4, max_mult=0.8))
    for series in (_rates([0.0001] * 6 + [0.02]), _rates([0.0001] * 6 + [-0.02])):
        assert 0.4 <= o.assess(series).multiplier <= 0.8


# --- wrapper strategy ------------------------------------------------------
class AlwaysBuy(Strategy):
    name = "always_buy"

    def generate_signals(self, market: MarketData):
        return [OrderIntent(symbol=market.symbol, side=Side.BUY, amount=1.0,
                            order_type=OrderType.LIMIT, price=100.0,
                            take_profit_price=102.0, is_entry=True)]


def _market(now_ts: int) -> MarketData:
    return MarketData("BTC/USD", [Candle(now_ts, 100, 100, 100, 100, 1.0)],
                      Ticker("BTC/USD", 100, 100, 100, 1, 1, 0))


def test_wrapper_scales_buy_size_by_funding() -> None:
    series = FundingSeries(_rates([0.0001] * 6 + [0.01]))  # crowded longs at last ts
    overlay = FundingOverlay(cfg())
    wrapped = FundingFilteredStrategy(AlwaysBuy(), series, overlay)
    sig = wrapped.generate_signals(_market(6 * _8H))[0]
    assert sig.amount < 1.0  # scaled down
    assert "funding_z" in sig.metadata


def test_wrapper_gates_longs_when_crowded() -> None:
    series = FundingSeries(_rates([0.0001] * 6 + [0.01]))
    wrapped = FundingFilteredStrategy(AlwaysBuy(), series, FundingOverlay(cfg()),
                                      gate_when_crowded=True)
    assert wrapped.generate_signals(_market(6 * _8H)) == []  # skipped

"""MVRV Z-score (causal) + overlay/wrapper, and the Stooq equities/bond parser."""

from __future__ import annotations

import pytest

from tradingbot.config import MvrvConfig
from tradingbot.domain import OrderIntent, OrderType, Side
from tradingbot.exchange.onchain import MvrvPoint, mvrv_z_from_caps, parse_bgeometrics
from tradingbot.exchange.models import Candle, Ticker
from tradingbot.exchange.stooq import parse_stooq_csv
from tradingbot.exchange.yahoo import parse_yahoo_chart
from tradingbot.strategy.base import MarketData, Strategy
from tradingbot.strategy.onchain import MvrvFilteredStrategy, MvrvOverlay, MvrvSeries

_DAY = 86_400_000


# --- MVRV Z-score is causal (expanding std, no look-ahead) ------------------
def test_mvrv_z_uses_expanding_std() -> None:
    # market cap rising, realized cap below it -> positive, growing-then-settling Z
    rows = [(i * _DAY, 100.0 + 10 * i, 90.0 + 5 * i) for i in range(10)]
    pts = mvrv_z_from_caps(rows)
    assert len(pts) == 10
    assert pts[0].z == 0.0  # n=1, std=0 -> 0
    assert all(p.z >= 0 for p in pts)
    # recomputing with one fewer trailing point doesn't change earlier values
    assert mvrv_z_from_caps(rows[:-1])[5].z == pts[5].z  # causal


# --- BGeometrics MVRV-Z API parsing ----------------------------------------
def test_parse_bgeometrics_unix_seconds() -> None:
    data = [{"d": "2024-01-01", "unixTs": "1704067200", "mvrvZscore": "2.34"},
            {"d": "2024-01-02", "unixTs": "1704153600", "mvrvZscore": "2.40"}]
    pts = parse_bgeometrics(data)
    assert len(pts) == 2
    assert pts[0].timestamp == 1704067200000  # seconds -> ms
    assert pts[0].z == 2.34


def test_parse_bgeometrics_date_fallback_and_bad_rows() -> None:
    data = [{"d": "2024-01-03", "mvrvZscore": "1.10"},  # no unixTs -> parse date
            {"d": "2024-01-04", "mvrvZscore": None}]     # bad -> skipped
    pts = parse_bgeometrics(data)
    assert len(pts) == 1 and pts[0].z == 1.10


# --- overlay valuation mapping ---------------------------------------------
def cfg(**kw) -> MvrvConfig:
    base = dict(enabled=True, low_z=0.0, high_z=7.0, min_mult=0.25, max_mult=1.0)
    base.update(kw)
    return MvrvConfig(**base)


def test_overlay_cheap_is_full_size() -> None:
    st = MvrvOverlay(cfg()).assess(MvrvPoint(0, -0.5))  # below low_z
    assert st.multiplier == 1.0 and st.rich is False


def test_overlay_rich_is_min_size_and_flagged() -> None:
    st = MvrvOverlay(cfg()).assess(MvrvPoint(0, 8.0))  # above high_z
    assert st.multiplier == 0.25 and st.rich is True


def test_overlay_midband_interpolates() -> None:
    st = MvrvOverlay(cfg()).assess(MvrvPoint(0, 3.5))  # halfway
    assert 0.5 < st.multiplier < 0.7
    assert st.rich is False


def test_overlay_none_is_neutral() -> None:
    assert MvrvOverlay(cfg()).assess(None).multiplier == 1.0


# --- series lookup ----------------------------------------------------------
def test_series_latest_before_is_causal() -> None:
    s = MvrvSeries([MvrvPoint(i * _DAY, float(i)) for i in range(5)])
    assert s.latest_before(2 * _DAY + 1).z == 2.0
    assert s.latest_before(-1) is None


# --- wrapper ----------------------------------------------------------------
class AlwaysBuy(Strategy):
    name = "always_buy"

    def generate_signals(self, market: MarketData):
        return [OrderIntent(symbol=market.symbol, side=Side.BUY, amount=1.0,
                            order_type=OrderType.LIMIT, price=100.0,
                            take_profit_price=102.0, is_entry=True)]


def _market(ts: int) -> MarketData:
    return MarketData("BTC/USD", [Candle(ts, 100, 100, 100, 100, 1.0)],
                      Ticker("BTC/USD", 100, 100, 100, 1, 1, 0))


def test_wrapper_scales_and_gates() -> None:
    series = MvrvSeries([MvrvPoint(0, 8.0)])  # rich
    scaled = MvrvFilteredStrategy(AlwaysBuy(), series, MvrvOverlay(cfg()))
    assert scaled.generate_signals(_market(_DAY))[0].amount == 0.25
    gated = MvrvFilteredStrategy(AlwaysBuy(), series, MvrvOverlay(cfg()), gate_when_rich=True)
    assert gated.generate_signals(_market(_DAY)) == []


# --- Stooq equities/bond CSV ------------------------------------------------
def test_parse_stooq_csv() -> None:
    text = ("Date,Open,High,Low,Close,Volume\n"
            "2024-01-02,470.0,472.5,469.0,471.2,1000000\n"
            "2024-01-03,471.0,471.0,466.0,467.3,1200000\n")
    candles = parse_stooq_csv(text)
    assert len(candles) == 2
    assert candles[0].close == 471.2
    assert candles[1].low == 466.0
    assert candles[0].timestamp < candles[1].timestamp


def test_parse_stooq_skips_bad_rows() -> None:
    text = "Date,Open,High,Low,Close,Volume\n2024-01-02,470,472,469,N/A,0\n"
    assert parse_stooq_csv(text) == []


# --- Yahoo chart API --------------------------------------------------------
def test_parse_yahoo_chart() -> None:
    payload = {"chart": {"result": [{
        "timestamp": [1704153600, 1704240000, 1704326400],
        "indicators": {"quote": [{
            "open": [470.0, 471.0, None],   # third bar has a null -> skipped
            "high": [472.0, 471.5, None],
            "low": [469.0, 466.0, None],
            "close": [471.2, 467.3, None],
            "volume": [1_000_000, 1_200_000, None],
        }]},
    }]}}
    candles = parse_yahoo_chart(payload)
    assert len(candles) == 2
    assert candles[0].timestamp == 1704153600000
    assert candles[1].close == 467.3


def test_parse_yahoo_chart_empty() -> None:
    assert parse_yahoo_chart({"chart": {"result": None}}) == []

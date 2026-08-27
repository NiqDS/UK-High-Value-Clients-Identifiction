"""Binance-vision kline parsing + event-correspondence analysis (no network)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingbot.analysis.event_study import (
    BUILTIN_EVENTS, KnownEvent, study_events, swing_points,
)
from tradingbot.exchange.binance_vision import normalize_symbol, parse_kline_csv
from tradingbot.exchange.models import Candle

_DAY = 86_400_000


# --- binance-vision parsing ------------------------------------------------
def test_normalize_symbol() -> None:
    assert normalize_symbol("BTC/USD") == "BTCUSDT"
    assert normalize_symbol("BTC/USDT") == "BTCUSDT"
    assert normalize_symbol("ETHUSDT") == "ETHUSDT"


def test_parse_kline_csv_ms_and_header() -> None:
    text = (
        "open_time,open,high,low,close,volume\n"  # header (skipped)
        "1704067200000,42000,42500,41800,42300,120.5,extra,cols,ok\n"
        "1704070800000,42300,42400,42100,42150,80.0\n"
    )
    candles = parse_kline_csv(text)
    assert len(candles) == 2
    assert candles[0].timestamp == 1704067200000
    assert candles[0].high == 42500 and candles[0].low == 41800
    assert candles[0].volume == 120.5


def test_parse_kline_csv_microseconds_normalised() -> None:
    # Binance switched some recent files to microsecond timestamps
    text = "1704067200000000,42000,42500,41800,42300,120.5\n"
    candles = parse_kline_csv(text)
    assert candles[0].timestamp == 1704067200000  # /1000 back to ms


# --- swing points ----------------------------------------------------------
def _c(ts_days, o, h, l, c):
    return Candle(ts_days * _DAY, o, h, l, c, 1.0)


def test_swing_points_finds_local_extrema() -> None:
    # a clear peak at index 2 and a trough at index 5
    candles = [
        _c(0, 10, 11, 9, 10), _c(1, 11, 13, 10, 12), _c(2, 12, 20, 11, 18),  # peak
        _c(3, 18, 19, 14, 15), _c(4, 15, 16, 8, 10), _c(5, 10, 11, 3, 4),    # trough
        _c(6, 4, 7, 3.5, 6),
    ]
    highs, lows = swing_points(candles, window=2)
    assert 2 in highs       # the 20-high is a swing high
    assert 5 in lows        # the 3-low is a swing low


# --- event study -----------------------------------------------------------
def test_event_within_window_matches_extreme() -> None:
    # V-shaped series with a single clear trough at day 10
    candles = []
    for d in range(21):
        base = 10.0 + abs(d - 10) * 0.5  # minimum at day 10
        candles.append(_c(d, base, base, base, base))
    ev_ts_day = 11  # 1 day after the low
    ev = KnownEvent(
        date=datetime.fromtimestamp(ev_ts_day * _DAY / 1000, tz=timezone.utc).date().isoformat(),
        label="test", kind="macro")
    study = study_events(candles, [ev], swing_window=3, match_window_days=5)
    hit = study.hits[0]
    assert hit.nearest_kind == "low"
    assert hit.distance_days <= 5


def test_event_outside_data_range_is_none() -> None:
    candles = [_c(d, 10, 11, 9, 10) for d in range(10)]
    ev = KnownEvent("1999-01-01", "ancient", "macro")
    study = study_events(candles, [ev], swing_window=2, match_window_days=7)
    assert study.hits[0].nearest_kind == "none"


def test_builtin_events_have_valid_dates() -> None:
    for e in BUILTIN_EVENTS:
        assert e.ts > 0  # parses to a timestamp
    assert any(e.kind == "halving" for e in BUILTIN_EVENTS)
    assert any(e.kind == "geopolitical" for e in BUILTIN_EVENTS)


def test_render_runs() -> None:
    candles = [_c(d, 10, 11, 9, 10) for d in range(30)]
    study = study_events(candles, BUILTIN_EVENTS, swing_window=3, match_window_days=7)
    assert "Event ↔ chart-extreme correspondence" in study.render()

"""Price-timeline: basket index, monthly moves, correction/rally detection."""

from __future__ import annotations

from tradingbot.analysis.price_timeline import (
    basket_index,
    render_timeline_report,
    swings,
)
from tradingbot.exchange.models import Candle

DAY = 86_400_000
START = 1_640_995_200_000  # 2022-01-01 UTC


def _candles(closes: list[float]) -> list[Candle]:
    out = []
    for i, c in enumerate(closes):
        out.append(Candle(timestamp=START + i * DAY, open=c, high=c, low=c, close=c, volume=1.0))
    return out


def _ramp(a: float, b: float, n: int) -> list[float]:
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def test_swings_detect_rally_then_correction_then_rally() -> None:
    # 100 -> 130 (rally +30%), -> 96 (correction ~-26%), -> 120 (rally +25%)
    closes = _ramp(100, 130, 40)[:-1] + _ramp(130, 96, 40)[:-1] + _ramp(96, 120, 40)
    legs = swings(list(range(len(closes))) and [START + i * DAY for i in range(len(closes))],
                  closes, swing_pct=15.0)
    kinds = [l.kind for l in legs]
    assert "rally" in kinds and "correction" in kinds
    # first confirmed leg is the opening rally up to the peak
    assert legs[0].kind == "rally" and legs[0].pct > 25
    # a correction of >= 20% is captured
    assert any(l.kind == "correction" and l.pct < -20 for l in legs)


def test_below_threshold_noise_is_ignored() -> None:
    # gentle 5% wobble -> no 15% swing legs
    closes = [100 + 5 * (i % 2) for i in range(60)]
    legs = swings([START + i * DAY for i in range(len(closes))], closes, swing_pct=15.0)
    assert legs == []


def test_basket_index_normalises_and_averages() -> None:
    a = _candles([100, 110, 120])          # +20%
    b = _candles([10, 11, 12])             # +20% (different scale)
    ts, basket, norm = basket_index([("A", a), ("B", b)])
    assert len(ts) == 3
    assert basket[0] == 100.0               # base 100
    # both rose 20%, so the equal-weight index is ~120 at the end
    assert abs(basket[-1] - 120.0) < 1e-6
    assert norm["A"][-1] == norm["B"][-1]   # scale-independent after normalising


def test_report_has_months_corrections_and_rallies() -> None:
    closes = _ramp(100, 140, 60)[:-1] + _ramp(140, 100, 60)  # up then down, ~2 quarters
    report = render_timeline_report([("A", _candles(closes)), ("B", _candles(closes))],
                                    swing_pct=15.0, label="[test]")
    assert "Month-by-month" in report
    assert "Corrections" in report and "Rallies" in report
    assert "DESCRIPTIVE only" in report

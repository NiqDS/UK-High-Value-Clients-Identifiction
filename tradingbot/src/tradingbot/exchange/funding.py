"""Perp funding-rate history: model, CSV I/O, and a Binance-vision fetcher.

Funding rate is a hard-to-fake positioning signal — traders pay/receive it for
their leverage. Positive funding = crowded longs; very negative = crowded shorts
/ capitulation. We fetch it from Binance's public futures dumps (static CDN,
usually reachable even when the trading API is geo-blocked).

CSV format: timestamp,funding_rate  (timestamp ms; rate as a decimal, e.g. 0.0001).
"""

from __future__ import annotations

import csv
import io
import logging
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .binance_vision import _completed_months, normalize_symbol
from .coingecko import _ssl_context
from .models import Candle  # noqa: F401 (kept for symmetry / typing convenience)

logger = logging.getLogger(__name__)

_VISION = "https://data.binance.vision/data/futures/um/monthly/fundingRate"


@dataclass(frozen=True)
class FundingRate:
    timestamp: int  # ms
    rate: float     # decimal per funding interval (e.g. 0.0001 = 0.01%)


def save_funding_csv(path: str | Path, rates: list[FundingRate]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "funding_rate"])
        for r in rates:
            w.writerow([r.timestamp, r.rate])


def load_funding_csv(path: str | Path) -> list[FundingRate]:
    out: list[FundingRate] = []
    with Path(path).open(newline="") as f:
        for row in csv.DictReader(f):
            ts = row.get("timestamp") or row.get("calc_time")
            rate = row.get("funding_rate") or row.get("last_funding_rate") or row.get("rate")
            if ts is None or rate is None:
                continue
            out.append(FundingRate(_norm_ts(int(float(ts))), float(rate)))
    out.sort(key=lambda r: r.timestamp)
    return out


def _norm_ts(ts: int) -> int:
    return ts // 1000 if ts > 1e14 else ts  # microseconds -> ms


def parse_vision_funding(text: str) -> list[FundingRate]:
    """Parse a Binance funding CSV (header: calc_time,funding_interval_hours,
    last_funding_rate — or positional time,...,rate)."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    time_i, rate_i = 0, -1
    start = 0
    header = [c.strip().lower() for c in rows[0]]
    if any(not _is_float(c) for c in rows[0]):  # header row present
        start = 1
        if "calc_time" in header:
            time_i = header.index("calc_time")
        if "last_funding_rate" in header:
            rate_i = header.index("last_funding_rate")
    out: list[FundingRate] = []
    for r in rows[start:]:
        if len(r) <= max(time_i, rate_i if rate_i >= 0 else 0):
            continue
        try:
            ts = _norm_ts(int(float(r[time_i])))
            out.append(FundingRate(ts, float(r[rate_i])))
        except (ValueError, IndexError):
            continue
    return out


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def fetch_funding_vision(symbol: str = "BTCUSDT", months: int = 12) -> list[FundingRate]:
    sym = normalize_symbol(symbol)
    rates: list[FundingRate] = []
    for year, month in _completed_months(months):
        url = f"{_VISION}/{sym}/{sym}-fundingRate-{year:04d}-{month:02d}.zip"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tradingbot"})
            with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:  # noqa: S310
                raw = resp.read()
            zf = zipfile.ZipFile(io.BytesIO(raw))
            rates += parse_vision_funding(zf.read(zf.namelist()[0]).decode())
        except Exception as exc:
            logger.warning("funding-vision: skipped %04d-%02d (%s)", year, month, exc)
    rates.sort(key=lambda r: r.timestamp)
    return rates

"""Free daily OHLC for stocks/ETFs/bonds from Stooq (no API key).

The strategy + backtester are venue-agnostic — they consume Candles — so any
OHLC CSV runs through the same `compare` harness. This lets us test whether the
algo behaves differently on equities (e.g. spy.us), bonds (e.g. tlt.us / agg.us)
versus crypto. Stooq serves a plain CSV over HTTPS:

    https://stooq.com/q/d/l/?s=spy.us&i=d

Columns: Date,Open,High,Low,Close,Volume (Date = YYYY-MM-DD).
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import logging
import urllib.request

from .coingecko import _ssl_context
from .models import Candle

logger = logging.getLogger(__name__)

_URL = "https://stooq.com/q/d/l/"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def parse_stooq_csv(text: str) -> list[Candle]:
    candles: list[Candle] = []
    for row in csv.DictReader(io.StringIO(text)):
        date = row.get("Date") or row.get("date")
        if not date or row.get("Close") in (None, "", "N/A"):
            continue
        try:
            ts = int(_dt.datetime.strptime(date, "%Y-%m-%d")
                     .replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)
            candles.append(Candle(
                timestamp=ts, open=float(row["Open"]), high=float(row["High"]),
                low=float(row["Low"]), close=float(row["Close"]),
                volume=float(row.get("Volume") or 0.0),
            ))
        except (ValueError, KeyError):
            continue
    candles.sort(key=lambda c: c.timestamp)
    return candles


def fetch_stooq(symbol: str = "spy.us") -> list[Candle]:
    url = f"{_URL}?s={symbol.lower()}&i=d"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:  # noqa: S310
        text = resp.read().decode()
    candles = parse_stooq_csv(text)
    if not candles:
        logger.warning("stooq: no candles parsed for %s (rate-limited or bad symbol?)", symbol)
    return candles

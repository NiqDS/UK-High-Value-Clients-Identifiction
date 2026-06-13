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


def _num(s: str) -> float:
    return float(str(s).strip().lstrip("$").replace(",", ""))


def _ts(date: str) -> int:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return int(_dt.datetime.strptime(date.strip(), fmt)
                       .replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {date!r}")


def parse_stooq_csv(text: str) -> list[Candle]:
    """Parse a browser-downloaded daily CSV. Tolerant of Stooq / Yahoo / Nasdaq
    column names, ``$``-prefixed prices, and YYYY-MM-DD or MM/DD/YYYY dates."""
    candles: list[Candle] = []
    for row in csv.DictReader(io.StringIO(text)):
        # normalise keys (Nasdaq uses 'Close/Last', Yahoo 'Adj Close', etc.)
        r = {k.strip().lower(): v for k, v in row.items() if k}
        date = r.get("date")
        close = r.get("close") or r.get("close/last") or r.get("adj close")
        if not date or close in (None, "", "N/A"):
            continue
        try:
            candles.append(Candle(
                timestamp=_ts(date),
                open=_num(r.get("open") or close), high=_num(r.get("high") or close),
                low=_num(r.get("low") or close), close=_num(close),
                volume=_num(r.get("volume") or 0),
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
        logger.warning("stooq: 0 candles for %s. Server returned: %r",
                       symbol, text[:200])
    return candles

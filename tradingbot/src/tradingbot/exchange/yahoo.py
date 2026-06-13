"""Free daily OHLC for stocks/ETFs/bonds from Yahoo Finance's chart API.

Yahoo's CSV *download* is gated now, but the chart JSON API
(query1.finance.yahoo.com/v8/finance/chart/SYMBOL) is a plain endpoint that
works with a browser User-Agent and no key — the same shape of source as the
BGeometrics on-chain API. Lets us test the algo on equities (SPY) / bonds
(TLT, AGG) through the same venue-agnostic backtester.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from .coingecko import _ssl_context
from .models import Candle

logger = logging.getLogger(__name__)

_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def parse_yahoo_chart(payload: dict) -> list[Candle]:
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        return []
    ts = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    o, h, l, c, v = (quote.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
    candles: list[Candle] = []
    for i, t in enumerate(ts):
        try:
            close = c[i]
            if close is None:
                continue
            candles.append(Candle(
                timestamp=int(t) * 1000,
                open=float(o[i]), high=float(h[i]), low=float(l[i]),
                close=float(close), volume=float(v[i] or 0.0),
            ))
        except (IndexError, TypeError, ValueError):
            continue
    candles.sort(key=lambda x: x.timestamp)
    return candles


def fetch_yahoo(symbol: str = "SPY", range_: str = "max", retries: int = 4) -> list[Candle]:
    url = f"{_URL}{symbol.upper()}?range={range_}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries - 1:
                wait = 5 * (2 ** attempt)  # 5s, 10s, 20s — Yahoo throttles bursts
                logger.warning("yahoo: 429 for %s, retrying in %ds", symbol, wait)
                time.sleep(wait)
                continue
            raise
    candles = parse_yahoo_chart(payload)
    if not candles:
        logger.warning("yahoo: 0 candles for %s (blocked or bad symbol?)", symbol)
    return candles

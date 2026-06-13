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

_HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
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


def fetch_yahoo(symbol: str = "SPY", range_: str = "max", retries: int = 5) -> list[Candle]:
    """Fetch daily OHLC, rotating Yahoo hosts and backing off on 429 throttling."""
    suffix = f"/v8/finance/chart/{symbol.upper()}?range={range_}&interval=1d"
    last_exc: Exception | None = None
    for attempt in range(retries):
        host = _HOSTS[attempt % len(_HOSTS)]
        req = urllib.request.Request(host + suffix,
                                     headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:  # noqa: S310
                candles = parse_yahoo_chart(json.loads(resp.read().decode()))
            if candles:
                return candles
            logger.warning("yahoo: 0 candles for %s (bad symbol or empty response?)", symbol)
            return candles
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in (429, 401, 403) and attempt < retries - 1:
                wait = 5 * (2 ** attempt)  # 5,10,20,40s — Yahoo throttles bursts
                logger.warning("yahoo: HTTP %d for %s, retry %d/%d in %ds (host rotates)",
                               exc.code, symbol, attempt + 1, retries, wait)
                time.sleep(wait)
                continue
            raise
    raise last_exc  # type: ignore[misc]

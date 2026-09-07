"""Daily OHLC for ETFs/stocks from Nasdaq's historical API (no key).

The same data behind nasdaq.com's "Download historical data" button, via
api.nasdaq.com. Needs browser-like headers; may be Cloudflare-blocked on some
networks (fall back to a browser download + `--exchange localcsv`).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import urllib.request

from .coingecko import _ssl_context
from .models import Candle

logger = logging.getLogger(__name__)

_URL = "https://api.nasdaq.com/api/quote/{sym}/historical"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _num(s) -> float:
    return float(str(s).strip().lstrip("$").replace(",", ""))


def parse_nasdaq(payload: dict) -> list[Candle]:
    rows = (((payload or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
    out: list[Candle] = []
    for r in rows:
        try:
            ts = int(_dt.datetime.strptime(r["date"], "%m/%d/%Y")
                     .replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)
            out.append(Candle(
                timestamp=ts, open=_num(r["open"]), high=_num(r["high"]),
                low=_num(r["low"]), close=_num(r["close"]),
                volume=_num(r.get("volume", 0)),
            ))
        except (KeyError, ValueError, TypeError):
            continue
    out.sort(key=lambda c: c.timestamp)
    return out


def fetch_nasdaq(symbol: str = "QQQ", years: int = 10, assetclass: str = "etf") -> list[Candle]:
    today = _dt.date.today()
    frm = today.replace(year=today.year - years)
    qs = f"?assetclass={assetclass}&fromdate={frm}&todate={today}&limit=9999"
    url = _URL.format(sym=symbol.upper()) + qs
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nasdaq.com/",
    })
    with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:  # noqa: S310
        candles = parse_nasdaq(json.loads(resp.read().decode()))
    if not candles:
        logger.warning("nasdaq: 0 candles for %s (try assetclass=stocks, or browser download)", symbol)
    return candles

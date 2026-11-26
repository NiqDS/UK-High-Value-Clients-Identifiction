"""Real OHLCV from Binance's public data dumps (data.binance.vision).

These are static monthly CSV-in-zip files on a CDN — they usually work even
where the trading API (api.binance.com) is geo-blocked, and they carry REAL
intra-bar high/low/volume (unlike CoinGecko's close-only series), which is what
the mean-reversion / ATR exit logic needs to be tested fairly.

No API key. Symbol uses Binance's no-slash USDT form, e.g. BTCUSDT.
"""

from __future__ import annotations

import datetime as _dt
import io
import logging
import urllib.request
import zipfile

from .coingecko import _ssl_context  # reuse the certifi-backed TLS context
from .models import Candle

logger = logging.getLogger(__name__)

_BASE = "https://data.binance.vision/data/spot/monthly/klines"


def normalize_symbol(symbol: str) -> str:
    """BTC/USD or BTC/USDT -> BTCUSDT (Binance spot uses USDT)."""
    s = symbol.replace("/", "").upper()
    if s.endswith("USD") and not s.endswith("USDT") and not s.endswith("BUSD"):
        s = s[:-3] + "USDT"
    return s


def _completed_months(n: int) -> list[tuple[int, int]]:
    """The last ``n`` completed (year, month) pairs, oldest first."""
    now = _dt.datetime.now(_dt.timezone.utc)
    y, m = now.year, now.month
    out: list[tuple[int, int]] = []
    for i in range(1, n + 1):
        mm, yy = m - i, y
        while mm <= 0:
            mm += 12
            yy -= 1
        out.append((yy, mm))
    return sorted(out)


def parse_kline_csv(text: str) -> list[Candle]:
    """Parse a Binance kline CSV (no/optional header). Columns:
    open_time, open, high, low, close, volume, ... Timestamps are normalised to
    milliseconds (Binance switched some recent files to microseconds)."""
    candles: list[Candle] = []
    import math
    for index,line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        parts=line.split(',')
        if index==0 and parts[0] in ('open_time','timestamp','Open time'):
            continue
        if len(parts)<6:
            raise ValueError('Malformed archive row')
        ts=int(parts[0])
        if ts>1e14:
            ts//=1000
        values=[float(x) for x in parts[1:6]]
        if any(not math.isfinite(x) for x in values) or min(values[:4])<=0 or values[4]<0:
            raise ValueError('Invalid archive values')
        o,h,l,c,v=values
        if not l<=min(o,c)<=max(o,c)<=h:
            raise ValueError('Invalid archive OHLC range')
        candles.append(Candle(ts,o,h,l,c,v))
    return candles


def fetch_klines(symbol: str = "BTCUSDT", interval: str = "1h", months: int = 12, *, allowed_missing=frozenset()) -> list[Candle]:
    sym = normalize_symbol(symbol)
    candles: list[Candle] = []
    periods = _completed_months(months)
    for year, month in periods:
        url = f"{_BASE}/{sym}/{interval}/{sym}-{interval}-{year:04d}-{month:02d}.zip"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tradingbot"})
            with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:  # noqa: S310
                raw = resp.read()
            zf = zipfile.ZipFile(io.BytesIO(raw))
            text = zf.read(zf.namelist()[0]).decode()
            candles += parse_kline_csv(text)
        except Exception as exc:
            raise ValueError(f'Archive unavailable or invalid: {sym} {year:04d}-{month:02d}') from exc
    from ..app.runner import timeframe_ms
    step=timeframe_ms(interval)
    if not periods or not candles:
        raise ValueError('No requested archive data')
    first=_dt.datetime(*periods[0],1,tzinfo=_dt.timezone.utc)
    y,m=periods[-1]
    last=_dt.datetime(y+1,1,1,tzinfo=_dt.timezone.utc) if m==12 else _dt.datetime(y,m+1,1,tzinfo=_dt.timezone.utc)
    expected=set(range(int(first.timestamp()*1000),int(last.timestamp()*1000),step))
    actual=[c.timestamp for c in candles]
    if actual!=sorted(set(actual)):
        raise ValueError('Archive timestamps are duplicated or unordered')
    allowed=set(allowed_missing)
    if not allowed<=expected or allowed & set(actual):
        raise ValueError('Invalid declared suspension timestamps')
    if set(actual)!=expected-allowed:
        raise ValueError('Archive has missing or unexpected bars; supply verified suspension metadata')
    return candles

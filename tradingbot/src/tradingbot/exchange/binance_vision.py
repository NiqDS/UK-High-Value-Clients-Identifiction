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
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 6:
            continue
        try:
            ts = int(float(parts[0]))
        except ValueError:
            continue  # header row
        if ts > 1e14:  # microseconds -> milliseconds
            ts //= 1000
        try:
            candles.append(Candle(
                timestamp=ts, open=float(parts[1]), high=float(parts[2]),
                low=float(parts[3]), close=float(parts[4]), volume=float(parts[5]),
            ))
        except ValueError:
            continue
    return candles


def fetch_klines(symbol: str = "BTCUSDT", interval: str = "1h", months: int = 12) -> list[Candle]:
    sym = normalize_symbol(symbol)
    candles: list[Candle] = []
    for year, month in _completed_months(months):
        url = f"{_BASE}/{sym}/{interval}/{sym}-{interval}-{year:04d}-{month:02d}.zip"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tradingbot"})
            with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:  # noqa: S310
                raw = resp.read()
            zf = zipfile.ZipFile(io.BytesIO(raw))
            text = zf.read(zf.namelist()[0]).decode()
            candles += parse_kline_csv(text)
        except Exception as exc:  # missing month / network — skip, keep going
            logger.warning("binance-vision: skipped %04d-%02d (%s)", year, month, exc)
    candles.sort(key=lambda c: c.timestamp)
    return candles

"""CoinGecko fallback data source (public aggregator, no API key, no geo-block).

Used when exchange APIs are unreachable from your network. Returns ~daily
close + volume via plain HTTPS (urllib — also avoids the async DNS resolver).
There is one price per day, so open/high/low are set to the close: SMA, VWAP
(volume-weighted) and percent-based exits work; ATR-based exits do not (they
need real intra-bar ranges).
"""

from __future__ import annotations

import json
import ssl
import urllib.request

from .models import Candle


def _ssl_context() -> ssl.SSLContext:
    """A verified TLS context. Prefer certifi's CA bundle (works even when the
    macOS python.org build has no system certs installed); fall back to default."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

# common base-symbol -> CoinGecko coin id
_IDS: dict[str, str] = {
    "BTC": "bitcoin", "XBT": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin", "LTC": "litecoin",
    "BCH": "bitcoin-cash", "LINK": "chainlink", "MATIC": "matic-network",
    "AVAX": "avalanche-2", "DOT": "polkadot",
}


def coin_id(symbol: str) -> str:
    base = symbol.split("/")[0].upper()
    return _IDS.get(base, base.lower())


def parse_market_chart(data: dict) -> list[Candle]:
    """Turn a CoinGecko market_chart payload into daily candles.

    ``prices`` and ``total_volumes`` are ``[[ts_ms, value], ...]``. One value per
    day => open = previous close, high/low = max/min(open, close)."""
    prices = data.get("prices", [])
    volumes = {int(ts): float(v) for ts, v in data.get("total_volumes", [])}
    candles: list[Candle] = []
    prev: float | None = None
    for ts, price in prices:
        ts = int(ts)
        price = float(price)
        open_ = prev if prev is not None else price
        candles.append(Candle(
            timestamp=ts, open=open_, high=max(open_, price), low=min(open_, price),
            close=price, volume=volumes.get(ts, 0.0),
        ))
        prev = price
    return candles


def fetch_daily(symbol: str, days: int = 365, vs_currency: str = "usd") -> list[Candle]:
    """Fetch ~daily OHLCV(close+volume) history from CoinGecko (free, no auth).

    ``days`` is capped at 365 on the free tier; >90 days yields daily granularity.
    """
    days = min(max(days, 1), 365)
    coin = coin_id(symbol)
    url = (
        f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart"
        f"?vs_currency={vs_currency}&days={days}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "tradingbot"})
    with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:  # noqa: S310
        data = json.load(resp)
    return parse_market_chart(data)

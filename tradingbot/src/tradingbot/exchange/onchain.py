"""BTC MVRV Z-score from Coin Metrics' community API (free, no key).

MVRV Z = (market cap - realized cap) / std(market cap). We pull CapMrktCurUSD
(market cap) and CapRealUSD (realized cap) daily and compute the Z-score with an
**expanding** standard deviation (only data up to each date) so a backtest never
sees the future. The community endpoint is a plain HTTPS JSON API and usually
reachable where the trading API is geo-blocked.

CSV format: timestamp,mvrv_z  (timestamp ms).
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
import logging
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .coingecko import _ssl_context

logger = logging.getLogger(__name__)

_CM = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
# BGeometrics: free, no-key on-chain API serving the MVRV Z-score directly
# (realized cap is a paid metric on Coin Metrics, so we use this instead).
_BG = "https://bitcoin-data.com/v1/mvrv-zscore"
# Cloudflare-fronted APIs reject non-browser User-Agents with 403.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


@dataclass(frozen=True)
class MvrvPoint:
    timestamp: int  # ms
    z: float


def save_mvrv_csv(path: str | Path, points: list[MvrvPoint]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "mvrv_z"])
        for pt in points:
            w.writerow([pt.timestamp, pt.z])


def load_mvrv_csv(path: str | Path) -> list[MvrvPoint]:
    out: list[MvrvPoint] = []
    with Path(path).open(newline="") as f:
        for row in csv.DictReader(f):
            ts = row.get("timestamp")
            z = row.get("mvrv_z") or row.get("z")
            if ts is None or z is None:
                continue
            out.append(MvrvPoint(int(float(ts)), float(z)))
    out.sort(key=lambda p: p.timestamp)
    return out


def mvrv_z_from_caps(rows: list[tuple[int, float, float]]) -> list[MvrvPoint]:
    """rows = [(ts_ms, market_cap, realized_cap)] sorted ascending -> Z-score with
    an expanding (causal) standard deviation of market cap."""
    rows = sorted(rows, key=lambda r: r[0])
    out: list[MvrvPoint] = []
    n = 0
    mean = 0.0
    m2 = 0.0  # Welford running variance accumulator
    for ts, mcap, rcap in rows:
        n += 1
        delta = mcap - mean
        mean += delta / n
        m2 += delta * (mcap - mean)
        std = math.sqrt(m2 / n) if n > 1 else 0.0
        z = (mcap - rcap) / std if std > 0 else 0.0
        out.append(MvrvPoint(ts, round(z, 4)))
    return out


def fetch_mvrv_bgeometrics() -> list[MvrvPoint]:
    """MVRV Z-score directly from bitcoin-data.com (free, no key). Items look like
    {"d": "2024-01-01", "unixTs": "1704067200", "mvrvZscore": "2.34"}."""
    req = urllib.request.Request(_BG, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:  # noqa: S310
        data = json.loads(resp.read().decode())
    return parse_bgeometrics(data)


def parse_bgeometrics(data: list[dict]) -> list[MvrvPoint]:
    out: list[MvrvPoint] = []
    for item in data:
        ts_raw = item.get("unixTs") or item.get("unixts")
        val = item.get("mvrvZscore")
        if val is None:  # pick the lone non-date numeric field defensively
            for k, v in item.items():
                if k.lower() not in ("d", "date", "unixts", "theday", "daytime"):
                    val = v
                    break
        if val is None:
            continue
        if ts_raw is not None:
            ts = int(float(ts_raw))
            ts = ts * 1000 if ts < 1_000_000_000_000 else ts
        elif item.get("d"):
            ts = int(_dt.datetime.fromisoformat(item["d"]).replace(
                tzinfo=_dt.timezone.utc).timestamp() * 1000)
        else:
            continue
        try:
            out.append(MvrvPoint(ts, round(float(val), 4)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda p: p.timestamp)
    return out


def fetch_mvrv_coinmetrics(start: str = "2011-01-01") -> list[MvrvPoint]:
    params = {
        "assets": "btc",
        "metrics": "CapMrktCurUSD,CapRealUSD",
        "frequency": "1d",
        "page_size": "10000",
        "start_time": start,
    }
    rows: list[tuple[int, float, float]] = []
    url = f"{_CM}?{urllib.parse.urlencode(params)}"
    for _ in range(10):  # follow pagination defensively
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode())
        for d in payload.get("data", []):
            mcap, rcap = d.get("CapMrktCurUSD"), d.get("CapRealUSD")
            if mcap is None or rcap is None:
                continue
            ts = int(_dt.datetime.fromisoformat(
                d["time"].replace("Z", "+00:00")).timestamp() * 1000)
            rows.append((ts, float(mcap), float(rcap)))
        token = payload.get("next_page_token")
        if not token:
            break
        url = f"{_CM}?{urllib.parse.urlencode({**params, 'next_page_token': token})}"
    return mvrv_z_from_caps(rows)

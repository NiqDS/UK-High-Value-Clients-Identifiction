"""fetch-data must never send real API credentials to the (public) data venue."""

from __future__ import annotations

from argparse import Namespace

from tradingbot import __main__ as m
from tradingbot.config import Config, Secrets, Settings


class _Client:
    def parse_timeframe(self, tf):  # ccxt-style, returns seconds
        return 86400


class _FakeAdapter:
    _client = _Client()

    async def load_markets(self):
        return {}

    async def fetch_ohlcv(self, *a, **k):
        return []  # empty -> loop terminates immediately, no network needed

    async def close(self):
        pass


def test_fetch_data_builds_adapter_without_credentials(tmp_path, monkeypatch) -> None:
    # REGRESSION: fetching public OHLCV from a DIFFERENT venue (e.g. Binance for
    # deep history) must not forward THIS exchange's key — Binance rejected the
    # Bybit key with "Invalid Api-Key ID." during its authenticated load_markets.
    captured: dict = {}

    def fake_build(cfg, secrets):
        captured["exchange"] = cfg.name
        captured["has_creds"] = secrets.has_exchange_credentials
        return _FakeAdapter()

    monkeypatch.setattr("tradingbot.exchange.factory.build_adapter", fake_build)

    cfg = Config(exchange={"name": "bybit", "symbols_allowlist": ["BTC/USDT"],
                           "quote_currency": "USDT"})
    # credentials ARE present on the session — they must NOT reach the fetch venue
    secrets = Secrets(_env_file=None, exchange_api_key="bybitkey",
                      exchange_api_secret="bybitsecret")
    settings = Settings(config=cfg, secrets=secrets)
    args = Namespace(exchange="binance", symbol="BTC/USDT", timeframe="1d",
                     months=1, out=str(tmp_path / "out.csv"), csv=None)

    rc = m._fetch_data(settings, args)
    assert rc == 0
    assert captured["exchange"] == "binance"        # venue override honoured
    assert captured["has_creds"] is False           # THE FIX: no key forwarded

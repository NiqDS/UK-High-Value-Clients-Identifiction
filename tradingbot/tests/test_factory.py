"""Exchange adapter factory — sandbox handling for venues without a testnet."""

from __future__ import annotations

import pytest

from tradingbot.config import ExchangeConfig, Secrets
from tradingbot.exchange.factory import build_adapter

_SECRETS = Secrets()


def test_sandbox_on_venue_without_testnet_gives_clear_error() -> None:
    # Coinbase has no ccxt spot testnet URL -> the old code crashed with a cryptic
    # TypeError in clone(); now it must be a clear, actionable ValueError.
    cfg = ExchangeConfig(name="coinbase", sandbox=True, quote_currency="USD")
    with pytest.raises(ValueError, match="no ccxt sandbox/testnet endpoint"):
        build_adapter(cfg, _SECRETS)


def test_live_mode_builds_adapter() -> None:
    # sandbox=false must construct fine (no testnet needed); this is the paper /
    # live-data path used by config.kraken-prod.yaml.
    cfg = ExchangeConfig(name="kraken", sandbox=False, quote_currency="GBP")
    adapter = build_adapter(cfg, _SECRETS)
    assert adapter is not None


def test_sandbox_on_venue_with_testnet_builds() -> None:
    # binance DOES ship a testnet URL -> sandbox mode constructs without error.
    cfg = ExchangeConfig(name="binance", sandbox=True, quote_currency="USDT")
    adapter = build_adapter(cfg, _SECRETS)
    assert adapter is not None

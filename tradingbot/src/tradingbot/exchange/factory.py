"""Build a configured ccxt async client + adapter from settings.

Kept separate from the adapter so the adapter stays import-light and testable
without ccxt installed.
"""

from __future__ import annotations

import logging

from ..config import ExchangeConfig, Secrets
from ..logging_setup import register_secret
from .adapter import ExchangeAdapter

logger = logging.getLogger(__name__)


def build_adapter(exchange_cfg: ExchangeConfig, secrets: Secrets) -> ExchangeAdapter:
    """Construct an :class:`ExchangeAdapter` backed by a real ccxt async client.

    Honours the sandbox flag, the rate limiter, and request timeout. Registers
    secret values for log redaction. Imports ccxt lazily so unit tests that
    inject a fake client don't require ccxt to be installed.
    """
    import ccxt.async_support as ccxt  # lazy import

    name = exchange_cfg.name.lower()
    if not hasattr(ccxt, name):
        raise ValueError(f"Unknown ccxt exchange id: {exchange_cfg.name!r}")

    api_key = secrets.exchange_api_key.get_secret_value()
    api_secret = secrets.exchange_api_secret.get_secret_value()
    api_password = secrets.exchange_api_password.get_secret_value()
    for value in (api_key, api_secret, api_password):
        register_secret(value)

    params: dict[str, object] = {
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": exchange_cfg.enable_rate_limit,
        "timeout": exchange_cfg.request_timeout_ms,
    }
    if api_password:
        params["password"] = api_password

    client = getattr(ccxt, name)(params)

    if exchange_cfg.sandbox:
        # Not every venue ships a ccxt testnet URL (e.g. Coinbase, Kraken spot).
        # ccxt then raises a cryptic TypeError deep in clone(); turn that into a
        # clear, actionable error instead.
        test_url = (getattr(client, "urls", {}) or {}).get("test")
        if not test_url:
            raise ValueError(
                f"Exchange {name!r} has no ccxt sandbox/testnet endpoint. Either set "
                f"exchange.sandbox: false and use app.dry_run: true (paper fills on LIVE "
                f"market data — no real orders), or choose a venue with a testnet "
                f"(e.g. binance) for sandbox integration testing."
            )
        client.set_sandbox_mode(True)
        logger.info("Exchange %s: SANDBOX mode enabled", name)
    else:
        # LIVE endpoint = real market data. Whether ORDERS are real depends on
        # app.dry_run (paper uses the PaperBroker; no orders reach the exchange).
        logger.warning("Exchange %s: LIVE endpoint (real market data) — order routing "
                       "depends on app.dry_run", name)

    return ExchangeAdapter(client)

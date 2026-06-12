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
        client.set_sandbox_mode(True)
        logger.info("Exchange %s: SANDBOX mode enabled", name)
    else:
        logger.warning("Exchange %s: LIVE mode — real funds at risk", name)

    return ExchangeAdapter(client)

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
    if exchange_cfg.options:
        # pass-through ccxt options (e.g. {"defaultType": "spot"} so Bybit/OKX
        # route to the spot market, not derivatives)
        params["options"] = dict(exchange_cfg.options)

    client = getattr(ccxt, name)(params)

    if exchange_cfg.use_threaded_dns:
        _use_os_resolver(client)

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


def _use_os_resolver(client) -> None:
    """Make ccxt's aiohttp session resolve DNS via the OS (threaded getaddrinfo)
    instead of aiodns/c-ares. aiodns fails on some setups — notably macOS and
    fresh CPython builds — with "Could not contact DNS servers" even though the
    system resolver works fine (curl/ping succeed). Injecting a session with the
    ThreadedResolver sidesteps that. No-op outside a running event loop (e.g. unit
    tests that only construct the adapter) — ccxt then builds its own session."""
    try:
        import asyncio

        asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop yet; nothing to attach a session to

    try:
        import aiohttp

        connector = aiohttp.TCPConnector(
            resolver=aiohttp.ThreadedResolver(), ttl_dns_cache=300, limit=0,
        )
        client.session = aiohttp.ClientSession(connector=connector, trust_env=True)
        logger.info("Using OS (threaded) DNS resolver for %s", client.id)
    except Exception:  # pragma: no cover - defensive; fall back to ccxt's default
        logger.exception("could not install threaded DNS resolver; using ccxt default")

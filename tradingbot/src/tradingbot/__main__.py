"""CLI entry point.

Step 1 commands:
  - ``check-config``: load + validate config and secrets, print a redacted summary.
  - ``healthcheck``:  connect to the (sandbox) exchange and run read-only calls
                      (balance, ticker, order book) for the allowlisted symbols.

Run as: ``python -m tradingbot <command>``
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from .config import Settings, load_settings
from .logging_setup import setup_logging

logger = logging.getLogger("tradingbot")


def _print_config_summary(settings: Settings) -> None:
    c = settings.config
    print("=== tradingbot config summary ===")
    print(f"exchange         : {c.exchange.name} (sandbox={c.exchange.sandbox})")
    print(f"quote currency   : {c.exchange.quote_currency}")
    print(f"symbols allowlist: {', '.join(c.exchange.symbols_allowlist) or '(none)'}")
    print(f"dry_run (paper)  : {c.app.dry_run}")
    print(f"trading_enabled  : {c.app.trading_enabled}")
    print(f"floor            : {c.risk.floor_quote} {c.exchange.quote_currency} "
          f"(+buffer {c.risk.floor_buffer_quote})")
    print(f"max notional/trade: {c.risk.max_notional_per_trade_quote} "
          f"/ {c.risk.max_notional_per_trade_pct_equity}% equity")
    print(f"approval threshold: {c.telegram.approval_threshold_quote} "
          f"(0 = approve every trade)")
    creds = "present" if settings.secrets.has_exchange_credentials else "MISSING"
    print(f"exchange creds   : {creds}")
    print("=================================")


async def _healthcheck(settings: Settings) -> int:
    from .exchange.factory import build_adapter

    if not settings.secrets.has_exchange_credentials:
        logger.warning(
            "No exchange credentials in .env — public endpoints may still work, "
            "but fetch_balance will fail."
        )

    adapter = build_adapter(settings.config.exchange, settings.secrets)
    rc = 0
    try:
        await adapter.load_markets()

        if settings.secrets.has_exchange_credentials:
            try:
                balance = await adapter.fetch_balance()
                quote = settings.config.exchange.quote_currency
                logger.info(
                    "Balance %s: free=%.2f used=%.2f total=%.2f",
                    quote, balance.free(quote), balance.used(quote), balance.total(quote),
                )
            except Exception:
                logger.exception("fetch_balance failed")
                rc = 1

        for symbol in settings.config.exchange.symbols_allowlist:
            try:
                ticker = await adapter.fetch_ticker(symbol)
                book = await adapter.fetch_order_book(symbol, limit=5)
                logger.info(
                    "%s last=%s bid=%s ask=%s spread=%.4f%% bestbid=%s bestask=%s",
                    symbol, ticker.last, ticker.bid, ticker.ask,
                    ticker.spread_pct if ticker.spread_pct is not None else float("nan"),
                    book.best_bid, book.best_ask,
                )
            except Exception:
                logger.exception("Read-only calls failed for %s", symbol)
                rc = 1
    finally:
        await adapter.close()
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(prog="tradingbot")
    parser.add_argument("command", choices=["check-config", "healthcheck"])
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--env-file", default=".env", help="path to .env")
    args = parser.parse_args()

    settings = load_settings(args.config, args.env_file)
    setup_logging(settings.config.app.log_level, settings.config.app.log_json)

    if args.command == "check-config":
        _print_config_summary(settings)
        return 0
    if args.command == "healthcheck":
        _print_config_summary(settings)
        return asyncio.run(_healthcheck(settings))
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

"""CLI entry point.

Commands:
  - ``check-config``: load + validate config and secrets, print a redacted summary.
  - ``healthcheck``:  connect to the (sandbox) exchange and run read-only calls
                      (balance, ticker, order book) for the allowlisted symbols.
  - ``paper-run``:    one pass of the full pipeline (strategy → risk → approval →
                      paper execution) per allowlisted symbol. Always paper; no
                      live orders are ever placed by this command.

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


async def _paper_run(settings: Settings) -> int:
    """One full pipeline pass per symbol, in paper mode (no live orders)."""
    from datetime import datetime, timezone

    from .events.calendar import EventCalendar
    from .events.event_risk import EventRiskModule
    from .events.kill_switch import KillSwitch, Observation
    from .events.posture import PostureProvider
    from .exchange.factory import build_adapter
    from .execution.broker import PaperBroker
    from .execution.executor import Executor
    from .execution.pipeline import TradingPipeline, auto_approve
    from .risk.engine import AccountSnapshot, RiskEngine
    from .risk.state import InMemoryRiskStateStore
    from .strategy import MarketData, build_strategy

    cfg = settings.config
    store = InMemoryRiskStateStore(daily_reset_utc_hour=cfg.risk.daily_reset_utc_hour)
    engine = RiskEngine(cfg, store)
    executor = Executor(cfg, PaperBroker(cfg.fees))  # paper only — never live here
    event_module = EventRiskModule(cfg.events, EventCalendar.from_config(cfg.events))
    kill_switch = KillSwitch(cfg.kill_switch)
    posture = PostureProvider(event_module=event_module, kill_switch=kill_switch)
    pipeline = TradingPipeline(
        cfg, engine, executor, store, approver=auto_approve, posture_provider=posture
    )
    strategy = build_strategy(cfg.strategy)
    adapter = build_adapter(cfg.exchange, settings.secrets)

    quote = cfg.exchange.quote_currency
    rc = 0
    try:
        await adapter.load_markets()
        # account snapshot (synthetic if no credentials, so the demo still runs)
        equity = free = cfg.risk.floor_quote * 3
        if settings.secrets.has_exchange_credentials:
            try:
                bal = await adapter.fetch_balance()
                equity, free = bal.total(quote), bal.free(quote)
            except Exception:
                logger.exception("fetch_balance failed; using synthetic equity for demo")

        now = datetime.now(timezone.utc)
        for action, name in event_module.transitions(now):
            logger.info("EVENT WINDOW %s: %s", action.upper(), name)

        for symbol in cfg.exchange.symbols_allowlist:
            candles = await adapter.fetch_ohlcv(symbol, cfg.strategy.timeframe, cfg.strategy.ohlcv_limit)
            ticker = await adapter.fetch_ticker(symbol)
            # feed the volatility kill-switch from the candle stream
            for c in candles:
                kill_switch.observe(
                    Observation(
                        datetime.fromtimestamp(c.timestamp / 1000, tz=timezone.utc),
                        price=c.close, volume=c.volume,
                    )
                )
            ks = kill_switch.status(now)
            if ks.paused:
                logger.warning("%s: kill-switch PAUSED (%s)", symbol, ks.reason or "manual")

            market = MarketData(symbol=symbol, candles=candles, ticker=ticker)
            intents = strategy.generate_signals(market)
            logger.info("%s: strategy produced %d intent(s)", symbol, len(intents))
            snap = AccountSnapshot(equity_quote=equity, free_quote=free, ticker=ticker, now=now)
            for result in await pipeline.process_signals(intents, snap):
                gate = result.decision.gate.value if result.decision else "-"
                logger.info("  -> %s (%s)", result.outcome.value, gate)
    except Exception:
        logger.exception("paper-run failed")
        rc = 1
    finally:
        await adapter.close()
    return rc


def _backtest(settings: Settings, args) -> int:
    from .backtest.engine import Backtester, BacktestConfig
    from .backtest.synthetic import synthetic_candles
    from .strategy import build_strategy

    cfg = settings.config
    bt_cfg = BacktestConfig(
        initial_equity=args.equity, fee_pct=args.fee,
        slippage_pct=args.slippage, oos_ratio=args.oos,
    )

    if args.source == "exchange":
        from .exchange.factory import build_adapter

        async def _fetch():
            adapter = build_adapter(cfg.exchange, settings.secrets)
            try:
                await adapter.load_markets()
                symbol = cfg.exchange.symbols_allowlist[0]
                return symbol, await adapter.fetch_ohlcv(symbol, cfg.strategy.timeframe, args.bars)
            finally:
                await adapter.close()

        symbol, candles = asyncio.run(_fetch())
        data_note = f"{symbol} {cfg.strategy.timeframe} from {cfg.exchange.name}"
    else:
        symbol = cfg.exchange.symbols_allowlist[0]
        candles = synthetic_candles(n=args.bars, seed=args.seed)
        data_note = f"SYNTHETIC random walk (seed={args.seed}) — NOT market data"

    bt = Backtester(bt_cfg)
    in_s, oos = bt.run_oos(candles, lambda: build_strategy(cfg.strategy), symbol)

    report = (
        f"# Backtest report — {cfg.strategy.name}\n\n"
        f"- data: {data_note}\n"
        f"- bars: {len(candles)} (in-sample {len(in_s.equity_curve)}, "
        f"out-of-sample {len(oos.equity_curve)})\n"
        f"- fees: {bt_cfg.fee_pct}%/side, slippage: {bt_cfg.slippage_pct}%/fill\n"
        f"- strategy: SMA {cfg.strategy.fast_period}/{cfg.strategy.slow_period}, "
        f"TP {cfg.strategy.take_profit_pct}% / SL {cfg.strategy.stop_loss_pct}%\n"
        f"- valuation filter: VWAP({cfg.strategy.vwap_window}) "
        f"{'on' if cfg.strategy.vwap_filter_enabled else 'off'}, "
        f"buy floor {cfg.strategy.buy_valuation_floor_pct:+g}% / "
        f"force-exit ceiling {cfg.strategy.force_exit_overvaluation_pct:g}% vs VWAP\n\n"
        "```\n" + in_s.summary("IN-SAMPLE") + "\n\n" + oos.summary("OUT-OF-SAMPLE") + "\n```\n\n"
        "> A strategy that only works in-sample, or is profitable gross but not "
        "net of fees, should be treated as failed.\n"
    )
    print(report)
    if args.report:
        from pathlib import Path
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report)
        logger.info("wrote report to %s", args.report)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="tradingbot")
    parser.add_argument(
        "command", choices=["check-config", "healthcheck", "paper-run", "backtest"]
    )
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--env-file", default=".env", help="path to .env")
    # backtest options
    parser.add_argument("--source", choices=["synthetic", "exchange"], default="synthetic")
    parser.add_argument("--bars", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--fee", type=float, default=0.6, help="fee %% per side")
    parser.add_argument("--slippage", type=float, default=0.05, help="slippage %% per fill")
    parser.add_argument("--oos", type=float, default=0.30, help="out-of-sample fraction")
    parser.add_argument("--report", default=None, help="write the report to this path")
    args = parser.parse_args()

    settings = load_settings(args.config, args.env_file)
    setup_logging(settings.config.app.log_level, settings.config.app.log_json)

    if args.command == "check-config":
        _print_config_summary(settings)
        return 0
    if args.command == "healthcheck":
        _print_config_summary(settings)
        return asyncio.run(_healthcheck(settings))
    if args.command == "paper-run":
        _print_config_summary(settings)
        return asyncio.run(_paper_run(settings))
    if args.command == "backtest":
        return _backtest(settings, args)
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

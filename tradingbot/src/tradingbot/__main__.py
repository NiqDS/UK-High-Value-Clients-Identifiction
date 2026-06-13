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


def _fetch_data(settings: Settings, args) -> int:
    import time

    from .backtest.data import save_csv
    from .exchange.factory import build_adapter

    cfg = settings.config
    symbol = args.symbol or cfg.exchange.symbols_allowlist[0]

    # CoinGecko fallback: public aggregator, no API key, no geo-block, plain HTTPS.
    if (args.exchange or "").lower() == "coingecko":
        from .backtest.data import save_csv
        from .exchange.coingecko import fetch_daily

        candles = fetch_daily(symbol, days=min(365, args.months * 30))
        save_csv(args.out, candles)
        logger.info("Saved %d daily candles from CoinGecko to %s", len(candles), args.out)
        return 0

    # Binance public data dumps: REAL intra-bar OHLCV (static CDN files, usually
    # reachable even when the trading API is geo-blocked).
    if (args.exchange or "").lower() in ("binancevision", "binance-vision"):
        from .backtest.data import save_csv
        from .exchange.binance_vision import fetch_klines

        candles = fetch_klines(symbol or "BTCUSDT", interval=args.timeframe, months=args.months)
        save_csv(args.out, candles)
        logger.info("Saved %d %s candles from binance-vision to %s",
                    len(candles), args.timeframe, args.out)
        return 0

    # Stooq: free daily OHLC for stocks / ETFs / bonds (no key, plain HTTPS).
    # e.g. --exchange stooq --symbol spy.us  (equities), tlt.us / agg.us (bonds).
    if (args.exchange or "").lower() == "stooq":
        from .backtest.data import save_csv
        from .exchange.stooq import fetch_stooq

        candles = fetch_stooq(symbol or "spy.us")
        save_csv(args.out, candles)
        logger.info("Saved %d daily candles from Stooq (%s) to %s",
                    len(candles), symbol or "spy.us", args.out)
        return 0

    # Yahoo Finance chart API: free daily OHLC for stocks/ETFs/bonds (no key).
    # e.g. --exchange yahoo --symbol SPY  (equities), TLT / AGG (bonds).
    if (args.exchange or "").lower() == "yahoo":
        from .backtest.data import save_csv
        from .exchange.yahoo import fetch_yahoo

        years = max(1, args.months // 12)
        range_ = "max" if years >= 10 else f"{years}y"
        try:
            candles = fetch_yahoo(symbol or "SPY", range_=range_)
        except Exception as exc:
            logger.error("Yahoo fetch failed (%s) — likely IP rate-limited. Fallback: download "
                         "the CSV in a browser and use `--exchange localcsv --csv FILE`.", exc)
            return 2
        save_csv(args.out, candles)
        logger.info("Saved %d daily candles from Yahoo (%s, %s) to %s",
                    len(candles), symbol or "SPY", range_, args.out)
        return 0

    # Local CSV convert (no network): turn a browser-downloaded daily file
    # (Yahoo Finance: Date,Open,High,Low,Close,Adj Close,Volume) into our format.
    # e.g. --exchange localcsv --csv ~/Downloads/SPY.csv --out data/spy.csv
    if (args.exchange or "").lower() == "localcsv":
        from pathlib import Path

        from .backtest.data import save_csv
        from .exchange.stooq import parse_stooq_csv

        if not args.csv:
            logger.error("--csv PATH (the downloaded file) is required for localcsv")
            return 2
        if not Path(args.csv).exists():
            logger.error("file not found: %s — check the name in ~/Downloads", args.csv)
            return 2
        candles = parse_stooq_csv(Path(args.csv).read_text())
        if not candles:
            logger.error("parsed 0 candles from %s — is it a daily OHLC CSV with a Date column?",
                         args.csv)
            return 2
        save_csv(args.out, candles)
        logger.info("Converted %d daily candles from %s to %s",
                    len(candles), args.csv, args.out)
        return 0

    # data fetch is read-only public history: force the live endpoint, and allow
    # overriding the venue (e.g. fetch from a deep-history exchange for training).
    ex_cfg = cfg.exchange.model_copy(update={"sandbox": False})
    if args.exchange:
        ex_cfg = ex_cfg.model_copy(update={"name": args.exchange})

    async def _go() -> int:
        adapter = build_adapter(ex_cfg, settings.secrets)
        try:
            await adapter.load_markets()
            client = adapter._client  # for parse_timeframe / pagination
            step_ms = int(client.parse_timeframe(args.timeframe) * 1000)
            since = int((time.time() - args.months * 30 * 86400) * 1000)
            now_ms = int(time.time() * 1000)
            all_candles = []
            while since < now_ms:
                batch = await adapter.fetch_ohlcv(symbol, args.timeframe, limit=1000, since=since)
                if not batch:
                    break
                all_candles += batch
                last = batch[-1].timestamp
                if last + step_ms <= since:  # exchange made no forward progress -> stop
                    break
                since = last + step_ms
            logger.info("Fetched %d candles from %s", len(all_candles), ex_cfg.name)
            save_csv(args.out, all_candles)
            logger.info("Saved %d candles to %s", len(all_candles), args.out)
            return 0
        finally:
            await adapter.close()

    return asyncio.run(_go())


def _walkforward(settings: Settings, args) -> int:
    from .backtest.data import load_csv
    from .backtest.engine import BacktestConfig
    from .backtest.learners import ParamOptimizer, QLearner
    from .backtest.metrics import METRICS
    from .backtest.synthetic import synthetic_candles
    from .backtest.walkforward import walk_forward

    cfg = settings.config
    if args.source == "csv":
        if not args.csv:
            logger.error("--csv PATH is required with --source csv")
            return 2
        candles = load_csv(args.csv)
        if len(candles) < 50:
            logger.error("%s has only %d bars — the fetch almost certainly failed (stale/empty "
                         "file). Re-fetch and check it logged 'Saved N…' with N in the thousands.",
                         args.csv, len(candles))
            return 2
        data_note = f"CSV {args.csv} ({len(candles)} bars)"
    else:
        candles = synthetic_candles(n=args.bars, seed=args.seed)
        data_note = f"SYNTHETIC (seed={args.seed}, {len(candles)} bars) — NOT market data"

    bt_cfg = BacktestConfig(initial_equity=args.equity, fee_pct=args.fee, slippage_pct=args.slippage)
    metric = METRICS[args.metric]
    base = cfg.strategy
    learners = [
        ParamOptimizer(bt_cfg, metric, n_samples=args.samples, seed=args.seed),
        QLearner(bt_cfg, metric, seed=args.seed),
    ]

    header = (
        f"# Walk-forward report — {data_note}\n"
        f"- windows: {args.windows} | metric: {args.metric} "
        f"(net-of-fees return / max drawdown)\n"
        f"- fees: {bt_cfg.fee_pct}%/side, slippage: {bt_cfg.slippage_pct}%/fill\n\n"
        "`learned(OOS)` = params learned on the PREVIOUS window, scored on this "
        "unseen window. A learner that only wins in-sample but not OOS is overfitting.\n\n"
    )
    blocks = []
    for learner in learners:
        rep = walk_forward(candles, base, learner, bt_cfg, metric, n_windows=args.windows)
        blocks.append("```\n" + rep.render() + "\n```")
    report = header + "\n\n".join(blocks) + "\n"
    print(report)
    if args.report:
        from pathlib import Path
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report)
        logger.info("wrote walk-forward report to %s", args.report)
    return 0


def _regime(settings: Settings, args) -> int:
    from .backtest.data import load_csv
    from .regime.cycle import CycleRegimeOverlay

    if not args.csv:
        logger.error("--csv PATH is required (daily OHLC for the 200-week SMA)")
        return 2
    candles = load_csv(args.csv)
    # force-enable so the command always shows an assessment
    overlay = CycleRegimeOverlay(settings.config.regime.model_copy(update={"enabled": True}))
    r = overlay.assess(candles)
    print("# Cycle / volatility regime")
    print(f"phase:            {r.phase}")
    print(f"risk multiplier:  x{r.multiplier:.2f}  "
          f"(range {settings.config.regime.min_risk_multiplier}"
          f"–{settings.config.regime.max_risk_multiplier})")
    if r.price_to_200w is not None:
        print(f"price / 200w-SMA: {r.price_to_200w:.2f}x")
    if r.months_since_halving is not None:
        print(f"months since halving: {r.months_since_halving:.1f}")
    print("reasons: " + "; ".join(r.reasons))
    return 0


def _chart_events(settings: Settings, args) -> int:
    from .analysis.event_study import BUILTIN_EVENTS, load_events_csv, study_events
    from .backtest.data import load_csv

    if not args.csv:
        logger.error("--csv PATH is required (OHLC to analyse)")
        return 2
    candles = load_csv(args.csv)
    events = list(BUILTIN_EVENTS)
    if args.events_csv:
        events += load_events_csv(args.events_csv)
    events.sort(key=lambda e: e.date)
    study = study_events(candles, events, swing_window=args.swing, match_window_days=args.window)
    report = (f"# Event correspondence — {args.csv} ({len(candles)} bars)\n\n"
              f"{study.render()}\n")
    print(report)
    if args.report:
        from pathlib import Path
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report)
        logger.info("wrote event study to %s", args.report)
    return 0


def _fetch_funding(settings: Settings, args) -> int:
    from .exchange.funding import fetch_funding_vision, save_funding_csv

    venue = (args.exchange or "binancevision").lower()
    symbol = args.symbol or "BTCUSDT"
    if venue in ("binancevision", "binance-vision"):
        rates = fetch_funding_vision(symbol, months=args.months)
    else:
        from .exchange.factory import build_adapter

        ex_cfg = settings.config.exchange.model_copy(update={"sandbox": False, "name": venue})

        async def _go():
            adapter = build_adapter(ex_cfg, settings.secrets)
            try:
                await adapter.load_markets()
                return await adapter.fetch_funding_rate_history(symbol, limit=1000)
            finally:
                await adapter.close()

        rates = asyncio.run(_go())
    save_funding_csv(args.out, rates)
    logger.info("Saved %d funding rows to %s", len(rates), args.out)
    return 0


def _fetch_onchain(settings: Settings, args) -> int:
    from .exchange.onchain import fetch_mvrv_bgeometrics, save_mvrv_csv

    points = fetch_mvrv_bgeometrics()
    if not points:
        logger.error("no MVRV rows fetched (network policy block?). Fallback: download an "
                     "MVRV-Z series and drop it in as a `timestamp,mvrv_z` CSV.")
        return 2
    save_mvrv_csv(args.out, points)
    logger.info("Saved %d MVRV Z-score rows to %s (%s..%s)", len(points), args.out,
                points[0].timestamp, points[-1].timestamp)
    return 0


def _compare(settings: Settings, args) -> int:
    from .backtest.compare import (
        default_experiments, funding_experiments, mvrv_experiments, run_comparison,
    )
    from .backtest.data import load_csv
    from .backtest.engine import BacktestConfig
    from .backtest.metrics import METRICS
    from .backtest.synthetic import synthetic_candles

    cfg = settings.config
    if args.source == "csv":
        if not args.csv:
            logger.error("--csv PATH is required with --source csv")
            return 2
        candles = load_csv(args.csv)
        if len(candles) < 50:
            logger.error("%s has only %d bars — the fetch almost certainly failed (stale/empty "
                         "file). Re-fetch and check it logged 'Saved N…' with N in the thousands.",
                         args.csv, len(candles))
            return 2
        data_note = f"CSV {args.csv} ({len(candles)} bars)"
    else:
        candles = synthetic_candles(n=args.bars, seed=args.seed)
        data_note = f"SYNTHETIC (seed={args.seed}, {len(candles)} bars) — NOT market data"

    bt_cfg = BacktestConfig(initial_equity=args.equity, fee_pct=args.fee,
                            slippage_pct=args.slippage, oos_ratio=args.oos)
    metric = METRICS[args.metric]
    experiments = default_experiments(cfg.strategy)
    if args.funding_csv:
        from pathlib import Path

        from .exchange.funding import load_funding_csv
        from .strategy.funding import FundingOverlay, FundingSeries

        if not Path(args.funding_csv).exists():
            logger.error("funding CSV not found: %s — run `fetch-funding`, or drop in a "
                         "timestamp,funding_rate file", args.funding_csv)
            return 2
        series = FundingSeries(load_funding_csv(args.funding_csv))
        overlay = FundingOverlay(cfg.funding)
        gate = args.funding_gate or cfg.funding.gate_longs_when_crowded
        experiments += funding_experiments(cfg.strategy, series, overlay, gate)
        data_note += f" + funding {args.funding_csv} ({len(series.rates)} rows)"
    if args.mvrv_csv:
        from pathlib import Path

        from .exchange.onchain import load_mvrv_csv
        from .strategy.onchain import MvrvOverlay, MvrvSeries

        if not Path(args.mvrv_csv).exists():
            logger.error("mvrv CSV not found: %s — run `fetch-onchain`, or drop in a "
                         "timestamp,mvrv_z file", args.mvrv_csv)
            return 2
        mseries = MvrvSeries(load_mvrv_csv(args.mvrv_csv))
        moverlay = MvrvOverlay(cfg.mvrv)
        mgate = args.mvrv_gate or cfg.mvrv.gate_when_rich
        experiments += mvrv_experiments(cfg.strategy, mseries, moverlay, mgate)
        data_note += f" + mvrv {args.mvrv_csv} ({len(mseries.points)} rows)"
    _, table = run_comparison(candles, experiments, bt_cfg, metric)
    report = f"# Strategy comparison — {data_note}\n- fees {bt_cfg.fee_pct}%/side, " \
             f"slippage {bt_cfg.slippage_pct}%/fill\n\n{table}\n"
    print(report)
    if args.report:
        from pathlib import Path
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report)
        logger.info("wrote comparison report to %s", args.report)
    return 0


def _build_runner(settings: Settings):
    """Wire the full live runner. Heavy imports (ccxt/telegram) are local."""
    import signal

    from .app.health import HealthMonitor
    from .app.portfolio import PositionTracker
    from .app.runner import TradingRunner
    from .events.calendar import EventCalendar
    from .events.event_risk import EventRiskModule
    from .events.kill_switch import KillSwitch
    from .events.posture import PostureProvider
    from .exchange.factory import build_adapter
    from .execution.broker import LiveBroker, PaperBroker
    from .execution.executor import Executor
    from .execution.pipeline import TradingPipeline
    from .reporting.email_sender import build_email_sender
    from .reporting.weekly import WeeklyReporter
    from .risk.engine import RiskEngine
    from .store import SqliteRiskStateStore, TradeLog, make_session_factory
    from .strategy import build_strategy

    cfg = settings.config

    # persistence: apply saved runtime overrides, then build the SQLite store
    from .approval.controls import JsonOverrideStore
    JsonOverrideStore("runtime_overrides.json").apply(cfg)
    sf = make_session_factory(cfg.app.db_url)
    store = SqliteRiskStateStore(sf, daily_reset_utc_hour=cfg.risk.daily_reset_utc_hour)
    trade_log = TradeLog(sf)

    adapter = build_adapter(cfg.exchange, settings.secrets)
    strategy = build_strategy(cfg.strategy)
    engine = RiskEngine(cfg, store)
    broker = PaperBroker(cfg.fees) if cfg.app.dry_run else LiveBroker(adapter)
    executor = Executor(cfg, broker)
    event_module = EventRiskModule(cfg.events, EventCalendar.from_config(cfg.events))
    kill_switch = KillSwitch(cfg.kill_switch)
    posture = PostureProvider(event_module=event_module, kill_switch=kill_switch)
    health = HealthMonitor(cfg.app)
    portfolio = PositionTracker()
    reporter = WeeklyReporter(trade_log)
    email_sender = build_email_sender(cfg.reporting, settings.secrets)
    from .regime.cycle import CycleRegimeOverlay
    regime_overlay = CycleRegimeOverlay(cfg.regime) if cfg.regime.enabled else None

    # optional Telegram approval + control + alerts
    approver = None
    report_deliver = None
    emergency_alert = None
    bot = None
    token = settings.secrets.telegram_bot_token.get_secret_value()
    if cfg.telegram.enabled and token and cfg.telegram.allowed_chat_ids:
        from .approval.controls import SettingsController
        from .approval.manager import ApprovalManager
        from .approval.status import StatusReporter
        from .approval.telegram_bot import TelegramApprovalBot

        async def balance_provider():
            bal = await adapter.fetch_balance()
            q = cfg.exchange.quote_currency
            return bal.total(q), bal.free(q)

        status = StatusReporter(cfg, store, kill_switch=kill_switch, event_module=event_module,
                                balance_provider=balance_provider)
        controls = SettingsController(cfg, store, kill_switch=kill_switch,
                                      overrides=JsonOverrideStore("runtime_overrides.json"))
        bot_holder: dict = {}
        manager = ApprovalManager(_LazyNotifier(bot_holder), cfg.telegram.approval_timeout_seconds)
        bot = TelegramApprovalBot(token, cfg, manager, controls, status)
        bot_holder["bot"] = bot
        approver = manager.request
        report_deliver = bot.send_message
        emergency_alert = bot.send_message
    else:
        logger.warning("Telegram disabled or unconfigured (need token + allowed_chat_ids); "
                       "running without approval/alerts.")

    pipeline = TradingPipeline(cfg, engine, executor, store, approver=approver,
                               posture_provider=posture, health=health,
                               emergency_alert=emergency_alert)
    runner = TradingRunner(
        settings=settings, adapter=adapter, pipeline=pipeline, strategy=strategy,
        store=store, trade_log=trade_log, portfolio=portfolio, kill_switch=kill_switch,
        event_module=event_module, health=health, reporter=reporter,
        report_deliver=report_deliver, report_path="reports/weekly_latest.md",
        email_sender=email_sender, regime_overlay=regime_overlay,
    )
    return runner, bot, signal


class _LazyNotifier:
    """Defers to the Telegram bot once it is constructed (resolves a chicken/egg
    between the ApprovalManager and the bot that serves its notifications)."""

    def __init__(self, holder: dict) -> None:
        self._holder = holder

    async def send_approval_request(self, req) -> None:
        await self._holder["bot"].send_approval_request(req)

    async def send_message(self, text) -> None:
        await self._holder["bot"].send_message(text)


async def _run(settings: Settings) -> int:
    runner, bot, signal = _build_runner(settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, runner.request_stop)
        except NotImplementedError:  # pragma: no cover - e.g. Windows
            pass
    if bot is not None:
        await bot.start()
    try:
        await runner.run()
    finally:
        if bot is not None:
            await bot.stop()
    return 0


def _weekly_report(settings: Settings) -> int:
    from .reporting.weekly import WeeklyReporter
    from .store import TradeLog, make_session_factory

    cfg = settings.config
    sf = make_session_factory(cfg.app.db_url)
    reporter = WeeklyReporter(TradeLog(sf))
    print(reporter.generate(quote=cfg.exchange.quote_currency))
    return 0


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
        "command",
        choices=["check-config", "healthcheck", "paper-run", "backtest", "run",
                 "weekly-report", "fetch-data", "walkforward", "compare", "chart-events",
                 "regime", "fetch-funding", "fetch-onchain"],
    )
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--env-file", default=".env", help="path to .env")
    # backtest options
    parser.add_argument("--source", choices=["synthetic", "exchange", "csv"], default="synthetic")
    parser.add_argument("--bars", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--fee", type=float, default=0.6, help="fee %% per side")
    parser.add_argument("--slippage", type=float, default=0.05, help="slippage %% per fill")
    parser.add_argument("--oos", type=float, default=0.30, help="out-of-sample fraction")
    parser.add_argument("--report", default=None, help="write the report to this path")
    # fetch-data options
    parser.add_argument("--exchange", default=None,
                        help="override the venue for fetch-data (e.g. kraken, binance, kucoin)")
    parser.add_argument("--symbol", default=None, help="symbol for fetch-data")
    parser.add_argument("--timeframe", default="1h", help="OHLCV timeframe for fetch-data")
    parser.add_argument("--months", type=int, default=12, help="months of history to fetch")
    parser.add_argument("--out", default="data/ohlcv.csv", help="output CSV path")
    # walkforward options
    parser.add_argument("--csv", default=None, help="CSV path for --source csv")
    parser.add_argument("--windows", type=int, default=4, help="walk-forward windows (e.g. quarters)")
    parser.add_argument("--samples", type=int, default=40, help="optimizer samples per window")
    parser.add_argument("--metric", default="net_return_over_maxdd", help="scoring metric")
    # chart-events options
    parser.add_argument("--swing", type=int, default=20, help="swing window (bars) for extrema")
    parser.add_argument("--window", type=int, default=7, help="event match window (days)")
    parser.add_argument("--events-csv", default=None, help="extra events CSV (date,label,kind)")
    parser.add_argument("--funding-csv", default=None,
                        help="funding-rate CSV to add funding-overlay variants to compare")
    parser.add_argument("--funding-gate", action="store_true",
                        help="hard-skip long entries when funding is crowded (vs only resizing)")
    parser.add_argument("--mvrv-csv", default=None,
                        help="MVRV Z-score CSV to add on-chain-overlay variants to compare")
    parser.add_argument("--mvrv-gate", action="store_true",
                        help="hard-skip long entries when MVRV Z is rich (vs only resizing)")
    parser.add_argument("--start", default=None, help="fetch-onchain start date (YYYY-MM-DD)")
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
    if args.command == "run":
        _print_config_summary(settings)
        return asyncio.run(_run(settings))
    if args.command == "weekly-report":
        return _weekly_report(settings)
    if args.command == "fetch-data":
        return _fetch_data(settings, args)
    if args.command == "walkforward":
        return _walkforward(settings, args)
    if args.command == "compare":
        return _compare(settings, args)
    if args.command == "chart-events":
        return _chart_events(settings, args)
    if args.command == "regime":
        return _regime(settings, args)
    if args.command == "fetch-funding":
        return _fetch_funding(settings, args)
    if args.command == "fetch-onchain":
        return _fetch_onchain(settings, args)
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

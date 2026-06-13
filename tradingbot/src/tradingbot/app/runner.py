"""The live trading runner: assembles strategy → posture → risk → approval →
execution, plus the heartbeat monitor, kill-switch feed, position tracking,
persistent trade log, and the weekly performance report.

``run_once`` (one pass per symbol) is dependency-injected and unit-tested with
fakes. ``run`` is the long-lived loop (heartbeat task, weekly-report scheduling,
graceful shutdown) that the CLI ``run`` command drives against the sandbox.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ..domain import OrderIntent, OrderType, Side
from ..execution.pipeline import Outcome, PipelineResult, TradingPipeline
from ..risk.engine import AccountSnapshot
from ..strategy.base import MarketData, Strategy
from .health import HealthMonitor
from .portfolio import PositionTracker

logger = logging.getLogger(__name__)


def next_report_time(now: datetime, weekday: int, hour_utc: int) -> datetime:
    """Next UTC datetime at the given weekday (0=Mon..6=Sun) and hour, strictly
    after ``now``."""
    candidate = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    days_ahead = (weekday - now.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


class TradingRunner:
    def __init__(
        self,
        *,
        settings,
        adapter,
        pipeline: TradingPipeline,
        strategy: Strategy,
        store,
        trade_log,
        portfolio: PositionTracker,
        kill_switch=None,
        event_module=None,
        health: HealthMonitor | None = None,
        reporter=None,
        report_deliver=None,  # async (text) -> None  (e.g. Telegram)
        report_path: str | None = None,
        email_sender=None,    # EmailSender | None
        regime_overlay=None,  # CycleRegimeOverlay | None
    ) -> None:
        self.settings = settings
        self.cfg = settings.config
        self.adapter = adapter
        self.pipeline = pipeline
        self.strategy = strategy
        self.store = store
        self.trade_log = trade_log
        self.portfolio = portfolio
        self.kill_switch = kill_switch
        self.event_module = event_module
        self.health = health
        self.reporter = reporter
        self.report_deliver = report_deliver
        self.report_path = report_path
        self.email_sender = email_sender
        self.regime_overlay = regime_overlay
        self._stop = asyncio.Event()
        self._week_first_price: dict[str, float] = {}
        self._last_price: dict[str, float] = {}
        self._regime_refreshed: datetime | None = None

    async def refresh_regime(self, now: datetime, symbol: str) -> None:
        """Recompute the slow cycle/regime risk multiplier from daily candles
        (at most once per configured cadence)."""
        if self.regime_overlay is None:
            return
        cadence_h = self.cfg.regime.cadence_hours
        if (self._regime_refreshed is not None
                and (now - self._regime_refreshed).total_seconds() < cadence_h * 3600):
            return
        try:
            daily = await self.adapter.fetch_ohlcv(symbol, "1d", limit=1500)
            regime = self.regime_overlay.assess(daily, now)
            self.pipeline.regime_multiplier = regime.multiplier
            self._regime_refreshed = now
            logger.info("Regime: %s, x%.2f (%s)", regime.phase, regime.multiplier,
                        "; ".join(regime.reasons))
        except Exception:
            logger.exception("regime refresh failed; keeping previous multiplier")

    # -- balance ------------------------------------------------------------
    async def _equity_free(self) -> tuple[float, float]:
        quote = self.cfg.exchange.quote_currency
        if self.settings.secrets.has_exchange_credentials:
            try:
                bal = await self.adapter.fetch_balance()
                return bal.total(quote), bal.free(quote)
            except Exception:
                logger.exception("fetch_balance failed; using synthetic equity")
        synthetic = self.cfg.risk.floor_quote * 3
        return synthetic, synthetic

    # -- one pass for one symbol -------------------------------------------
    async def run_once(self, symbol: str, now: datetime | None = None) -> list[PipelineResult]:
        now = now or datetime.now(timezone.utc)
        await self.refresh_regime(now, symbol)
        candles = await self.adapter.fetch_ohlcv(
            symbol, self.cfg.strategy.timeframe, self.cfg.strategy.ohlcv_limit
        )
        ticker = await self.adapter.fetch_ticker(symbol)

        # feed the volatility kill-switch from the candle stream
        if self.kill_switch is not None:
            from ..events.kill_switch import Observation
            for c in candles[-self.cfg.kill_switch.rolling_window_minutes:]:
                self.kill_switch.observe(Observation(
                    datetime.fromtimestamp(c.timestamp / 1000, tz=timezone.utc),
                    price=c.close, volume=c.volume,
                ))

        # track prices for the weekly market benchmark
        if ticker.last:
            self._last_price[symbol] = ticker.last
            self._week_first_price.setdefault(symbol, ticker.last)

        md = MarketData(symbol=symbol, candles=candles, ticker=ticker,
                        holding=self.portfolio.holding(symbol))
        intents = list(self.strategy.generate_signals(md))
        intents += self._tp_stop_exits(symbol, ticker.last)

        equity, free = await self._equity_free()
        snapshot = AccountSnapshot(
            equity_quote=equity, free_quote=free, open_positions=len(self.portfolio.positions),
            ticker=ticker, now=now,
        )
        results = await self.pipeline.process_signals(intents, snapshot)
        self._apply_fills(results, now)
        return results

    def _tp_stop_exits(self, symbol: str, last: float | None) -> list[OrderIntent]:
        """Emit protective exits when a held position hits its take-profit or stop.
        A stop is an emergency (crosses the spread, bypasses approval); a
        take-profit rests as a normal limit exit."""
        pos = self.portfolio.get(symbol)
        if pos is None or not last:
            return []
        # ratchet a trailing stop up behind the latest price
        if pos.trail_distance is not None:
            trailed = last - pos.trail_distance
            pos.stop = trailed if pos.stop is None else max(pos.stop, trailed)
        if pos.stop is not None and last <= pos.stop:
            return [OrderIntent(symbol=symbol, side=Side.SELL, amount=pos.units,
                                order_type=OrderType.MARKET, price=last, is_entry=False,
                                reason="stop hit", metadata={"emergency": True})]
        if pos.take_profit is not None and last >= pos.take_profit:
            return [OrderIntent(symbol=symbol, side=Side.SELL, amount=pos.units,
                                order_type=OrderType.LIMIT, price=last, is_entry=False,
                                reason="take-profit hit")]
        return []

    def _apply_fills(self, results: list[PipelineResult], now: datetime) -> None:
        for r in results:
            if r.outcome is not Outcome.EXECUTED or r.execution is None or r.execution.fill is None:
                continue
            f = r.execution.fill
            intent = r.intent
            realized = self.portfolio.on_fill(
                f.symbol, f.side, f.price, f.amount, now,
                take_profit=intent.take_profit_price, stop=intent.stop_price,
                trail_distance=(intent.metadata or {}).get("trail_distance"),
            )
            if f.side == Side.SELL:
                self.store.record_realized_pnl(now, realized - f.fee_quote)
            self.trade_log.record(
                ts=f.timestamp, symbol=f.symbol, side=f.side.value, price=f.price,
                amount=f.amount, cost_quote=f.cost_quote, fee_quote=f.fee_quote,
                role=f.role.value, is_entry=intent.is_entry, realized_pnl=realized,
                reason=intent.reason, valuation_pct=(intent.metadata or {}).get("valuation_pct"),
                client_order_id=f.client_order_id,
            )

    # -- weekly report ------------------------------------------------------
    def _market_returns(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for sym, first in self._week_first_price.items():
            last = self._last_price.get(sym)
            if first and last:
                out[sym] = (last - first) / first * 100.0
        return out

    async def emit_weekly_report(self, now: datetime | None = None) -> str | None:
        if self.reporter is None:
            return None
        text = self.reporter.generate(
            now=now, days=7, market_returns_pct=self._market_returns(),
            quote=self.cfg.exchange.quote_currency,
        )
        if self.report_path:
            from pathlib import Path
            Path(self.report_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.report_path).write_text(text)
        if self.report_deliver is not None:
            try:
                await self.report_deliver(text)
            except Exception:
                logger.exception("weekly report telegram delivery failed")
        if self.email_sender is not None:
            try:
                subject = f"tradingbot weekly report — {(now or datetime.now(timezone.utc)).date()}"
                await asyncio.to_thread(self.email_sender.send, subject, text)
            except Exception:
                logger.exception("weekly report email delivery failed")
        self._week_first_price = dict(self._last_price)  # reset the benchmark window
        return text

    # -- main loop ----------------------------------------------------------
    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        from .self_check import trade_only_key_self_check

        safe = await trade_only_key_self_check(self.adapter)
        if not safe and not self.cfg.app.dry_run:
            logger.error("Refusing to start LIVE with a withdrawal-capable key.")
            return
        await self.adapter.load_markets()

        tasks: list[asyncio.Task] = []
        if self.health is not None:
            async def ping() -> None:
                await self.adapter.fetch_ticker(self.cfg.exchange.symbols_allowlist[0])
            tasks.append(asyncio.create_task(self.health.run(ping, self._stop)))

        interval = self.cfg.app.heartbeat_interval_seconds
        rep = self.cfg.reporting
        next_report = (
            next_report_time(datetime.now(timezone.utc), rep.weekly_day, rep.weekly_hour_utc)
            if rep.weekly_enabled else None
        )
        logger.info("Runner started (dry_run=%s, symbols=%s); next weekly report: %s",
                    self.cfg.app.dry_run, self.cfg.exchange.symbols_allowlist, next_report)
        try:
            while not self._stop.is_set():
                for symbol in self.cfg.exchange.symbols_allowlist:
                    try:
                        await self.run_once(symbol)
                    except Exception:
                        logger.exception("run_once failed for %s", symbol)
                now = datetime.now(timezone.utc)
                if next_report is not None and now >= next_report:
                    await self.emit_weekly_report(now)
                    next_report = next_report_time(now, rep.weekly_day, rep.weekly_hour_utc)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            for t in tasks:
                t.cancel()
            await self.adapter.close()
            logger.info("Runner stopped.")

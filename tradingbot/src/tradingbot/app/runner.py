"""The live trading runner: assembles strategy → posture → risk → approval →
execution, plus the heartbeat monitor, kill-switch feed, position tracking,
persistent trade log, and the weekly performance report.

``run_once`` (one pass per symbol) is dependency-injected and unit-tested with
fakes. ``run`` is the long-lived loop (heartbeat task, weekly-report scheduling,
graceful shutdown) that the CLI ``run`` command drives against the sandbox.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime, timedelta, timezone

from ..domain import OrderIntent, OrderType, Side
from ..execution.pipeline import Outcome, PipelineResult, TradingPipeline
from ..risk.engine import AccountSnapshot
from ..strategy.base import MarketData, Strategy
from .health import HealthMonitor
from .portfolio import Position, PositionTracker

logger = logging.getLogger(__name__)

_TF_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def timeframe_ms(timeframe: str) -> int:
    """'1m'/'4h'/'1d'/'1w' -> bar length in milliseconds."""
    try:
        return int(timeframe[:-1]) * _TF_MS[timeframe[-1]]
    except (KeyError, ValueError, IndexError):
        raise ValueError(
            f"unsupported timeframe {timeframe!r} for closed-bar mode "
            "(use Nm/Nh/Nd/Nw, e.g. '1d')"
        ) from None


def drop_forming_candle(candles, timeframe: str, now: datetime):
    """Drop the still-forming last bar(s) so signals see only CLOSED bars —
    parity with the backtest, which never evaluates an incomplete candle."""
    tf_ms = timeframe_ms(timeframe)
    now_ms = int(now.timestamp() * 1000)
    out = list(candles)
    while out and out[-1].timestamp + tf_ms > now_ms:
        out.pop()
    return out


def next_report_time(now: datetime, weekday: int, hour_utc: int) -> datetime:
    """Next UTC datetime at the given weekday (0=Mon..6=Sun) and hour, strictly
    after ``now``."""
    candidate = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    days_ahead = (weekday - now.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def next_monthly_time(now: datetime, day: int, hour_utc: int) -> datetime:
    """Next UTC datetime at the given day-of-month (1..28) and hour, strictly
    after ``now`` (rolls to next month if this month's slot has passed)."""
    day = max(1, min(28, day))
    candidate = now.replace(day=day, hour=hour_utc, minute=0, second=0, microsecond=0)
    if candidate <= now:
        year = now.year + (1 if now.month == 12 else 0)
        month = 1 if now.month == 12 else now.month + 1
        candidate = candidate.replace(year=year, month=month)
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
        trade_alert=None,     # async (text) -> None  (per-fill buy/sell alerts)
        report_path: str | None = None,
        email_sender=None,    # EmailSender | None
        regime_overlay=None,  # CycleRegimeOverlay | None
        position_store=None,  # JsonPositionStore | None — persists positions across restarts
        decision_log=None,    # DecisionLog | None — records every decision (RL/analysis feed)
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
        self.trade_alert = trade_alert
        self.report_path = report_path
        self.email_sender = email_sender
        self.regime_overlay = regime_overlay
        self.position_store = position_store
        self.decision_log = decision_log
        self._stop = asyncio.Event()
        self._week_first_price: dict[str, float] = {}
        self._last_price: dict[str, float] = {}
        self._regime_refreshed: datetime | None = None
        self._equity_cache: tuple[float, float] | None = None
        self._equity_cache_mono: float = 0.0

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
        # One pass polls 7 symbols back-to-back; the balance can't meaningfully
        # change between them, so cache for a few seconds (7 REST calls -> 1).
        import time as _time

        if (self._equity_cache is not None
                and _time.monotonic() - self._equity_cache_mono < 30.0):
            return self._equity_cache
        quote = self.cfg.exchange.quote_currency
        result: tuple[float, float] | None = None
        # PAPER (dry-run): size off the configured paper_equity, NOT the real
        # wallet — even when credentials are present. The keys are here only to
        # read PUBLIC market data; the real balance may be 0 (nothing deposited
        # yet), and reading that 0 would breach the floor and HALT trading,
        # starving the paper run. No real orders are placed in dry-run, so the
        # simulated equity is the correct sizing basis.
        if self.cfg.app.dry_run and self.cfg.app.paper_equity > 0:
            sim = self.cfg.app.paper_equity
            result = (sim, sim)
        elif self.settings.secrets.has_exchange_credentials:
            try:
                bal = await self.adapter.fetch_balance()
                result = (bal.total(quote), bal.free(quote))
            except Exception:
                logger.exception("fetch_balance failed; using simulated equity")
        if result is None:
            # No credentials (paper run): size off the configured paper_equity so
            # sleeves match the intended real capital; floor*3 if unset.
            simulated = self.cfg.app.paper_equity or self.cfg.risk.floor_quote * 3
            result = (simulated, simulated)
        self._equity_cache = result
        self._equity_cache_mono = _time.monotonic()
        return result

    # -- startup reconciliation --------------------------------------------
    async def reconcile_positions(self, now: datetime | None = None) -> None:
        """Reconcile the persisted tracker against the exchange's ACTUAL balances
        at startup. Closes the gap the in-memory order-idempotency set cannot:
        if the bot placed an order, it filled, and the process died before
        recording it, a naive restart would believe it is flat and re-buy on top
        (double exposure). Symmetrically, a position closed outside the bot
        (manual sell / liquidation) would otherwise leave a phantom the bot keeps
        trying to exit forever.

        Assumes a DEDICATED account (the deployment holds only the bot's quote
        float — see config), so any base-asset balance is the bot's. Live only:
        in paper/dry-run the exchange balance is the user's real wallet and must
        NOT be adopted as bot positions.
        """
        if self.cfg.app.dry_run or not self.settings.secrets.has_exchange_credentials:
            return
        now = now or datetime.now(timezone.utc)
        try:
            bal = await self.adapter.fetch_balance()
        except Exception:
            logger.exception("reconcile: fetch_balance failed — starting with the persisted "
                             "tracker UNVERIFIED; positions may be stale")
            return

        min_notional = self.cfg.risk.min_notional_per_trade_quote
        changed = False
        for symbol in self.cfg.exchange.symbols_allowlist:
            base = symbol.split("/")[0]
            ex_units = bal.total(base)
            try:
                price = (await self.adapter.fetch_ticker(symbol)).last or 0.0
            except Exception:
                logger.exception("reconcile: ticker fetch failed for %s — skipping", symbol)
                continue
            ex_value = ex_units * price
            pos = self.portfolio.get(symbol)

            if pos is None and ex_value >= min_notional:
                # untracked holding on the venue -> adopt so the exit logic manages
                # it (entry=current price; the channel exit still governs downside)
                logger.critical(
                    "RECONCILE: untracked %s holding on the exchange (%.8f = %.2f %s) — "
                    "adopting as a managed position at the current price. Likely a fill "
                    "the bot placed but crashed before recording.",
                    base, ex_units, ex_value, self.cfg.exchange.quote_currency)
                self.portfolio.positions[symbol] = Position(
                    symbol=symbol, units=ex_units, entry_price=price, entry_ts=now)
                changed = True
            elif pos is not None and ex_value < min_notional:
                # the tracked position is gone on the venue (manual sell / liquidation)
                logger.warning(
                    "RECONCILE: tracked %s position not present on the exchange "
                    "(%.2f %s < min) — dropping the phantom so no exits are emitted for it.",
                    symbol, ex_value, self.cfg.exchange.quote_currency)
                del self.portfolio.positions[symbol]
                changed = True
            elif pos is not None and ex_units > 0 and abs(pos.units - ex_units) > ex_units * 0.01:
                # material unit drift (partial external fill) -> trust the exchange
                logger.warning("RECONCILE: %s units %.8f (tracker) != %.8f (exchange) — "
                               "adopting the exchange amount.", symbol, pos.units, ex_units)
                pos.units = ex_units
                changed = True

        if changed and self.position_store is not None:
            self.position_store.save(self.portfolio)
        else:
            logger.info("reconcile: tracker matches the exchange (%d position(s)).",
                        len(self.portfolio.positions))

    # -- one pass for one symbol -------------------------------------------
    async def run_once(self, symbol: str, now: datetime | None = None) -> list[PipelineResult]:
        now = now or datetime.now(timezone.utc)
        await self.refresh_regime(now, symbol)
        # cancel resting bot orders left by prior passes/crashes BEFORE evaluating,
        # so they can never fill later untracked; the count feeds MAX_OPEN_ORDERS
        open_orders = await self._cancel_stale_orders(symbol)
        candles = await self.adapter.fetch_ohlcv(
            symbol, self.cfg.strategy.timeframe, self.cfg.strategy.ohlcv_limit
        )
        ticker = await self.adapter.fetch_ticker(symbol)
        if self.cfg.strategy.signal_on_closed_bar:
            candles = drop_forming_candle(candles, self.cfg.strategy.timeframe, now)

        # feed the volatility kill-switch PER SYMBOL (dedup + count-window handled
        # internally, so re-feeding recent bars each poll is safe)
        if self.kill_switch is not None:
            from ..events.kill_switch import Observation
            for c in candles[-self.cfg.kill_switch.rolling_window_minutes:]:
                self.kill_switch.observe(Observation(
                    datetime.fromtimestamp(c.timestamp / 1000, tz=timezone.utc),
                    price=c.close, volume=c.volume, symbol=symbol,
                ))

        # track prices for the weekly market benchmark
        if ticker.last:
            self._last_price[symbol] = ticker.last
            self._week_first_price.setdefault(symbol, ticker.last)

        md = MarketData(symbol=symbol, candles=candles, ticker=ticker,
                        holding=self.portfolio.holding(symbol))
        intents = list(self.strategy.generate_signals(md))
        intents += self._tp_stop_exits(symbol, ticker.last)
        intents = self._normalize_exits(symbol, intents)

        equity, free = await self._equity_free()
        snapshot = AccountSnapshot(
            equity_quote=equity, free_quote=free, open_positions=len(self.portfolio.positions),
            open_orders=open_orders, unrealized_pnl_quote=self._unrealized_pnl(),
            ticker=ticker, now=now,
        )
        results = await self.pipeline.process_signals(intents, snapshot)
        alerts = self._apply_fills(results, now)
        for a in alerts:
            await self._deliver_trade_alert(a)
        return results

    def _normalize_exits(self, symbol: str, intents: list[OrderIntent]) -> list[OrderIntent]:
        """Exits close the ACTUAL position. Strategies size exits from notional
        (target/price), which drifts from the held units as price moves — an
        oversell is rejected on spot and the position is stranded. Resize every
        exit to the tracked units, and keep at most ONE exit per pass (the
        strategy's channel exit and the stop monitor can both fire on the same
        breakdown; prefer the emergency stop — it crosses the spread and skips
        approval). Entries pass through untouched."""
        entries = [i for i in intents if i.is_entry]
        exits = [i for i in intents if not i.is_entry]
        if not exits:
            return entries
        pos = self.portfolio.get(symbol)
        if pos is None or pos.units <= 0:
            # nothing tracked to close (e.g. post-restart gap) — drop bogus exits
            logger.warning("%s: dropping %d exit intent(s) with no tracked position",
                           symbol, len(exits))
            return entries
        exits.sort(key=lambda i: not bool(i.metadata.get("emergency")))  # emergency first
        chosen = dataclasses.replace(exits[0], amount=pos.units)
        return entries + [chosen]

    def _unrealized_pnl(self) -> float:
        """Mark open positions against the latest seen prices so the daily-loss
        stop can react to open drawdown, not only realized losses."""
        total = 0.0
        for sym, pos in self.portfolio.positions.items():
            last = self._last_price.get(sym)
            if last:
                total += (last - pos.entry_price) * pos.units
        return total

    async def _cancel_stale_orders(self, symbol: str) -> int:
        """Live only: cancel any resting bot-owned orders (client id 'tb-*') so a
        stale bid can never fill untracked later. Returns the number of open
        orders remaining on the venue for this symbol."""
        if self.cfg.app.dry_run:
            return 0
        try:
            orders = await self.adapter.fetch_open_orders(symbol)
        except Exception:
            logger.exception("fetch_open_orders failed for %s", symbol)
            return 0
        remaining = 0
        for o in orders:
            cid = str(o.get("clientOrderId")
                      or (o.get("info") or {}).get("clientOrderId") or "")
            oid = o.get("id")
            if cid.startswith("tb-") and oid:
                logger.warning("%s: cancelling stale bot order %s (%s)", symbol, oid, cid)
                try:
                    await self.adapter.cancel_order(str(oid), symbol)
                except Exception:
                    logger.exception("cancel failed for %s", oid)
                    remaining += 1
            else:
                remaining += 1
        return remaining

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

    def _validate_fill(self, f, intent: OrderIntent) -> None:
        """Arithmetic invariants every fill must satisfy. Violations are logged
        CRITICAL (never raised — the loop must keep running and the books must
        still record what actually happened), so a venue/parsing bug is loud
        instead of silently corrupting position/PnL state."""
        problems: list[str] = []
        if f.amount <= 0 or f.price <= 0:
            problems.append(f"non-positive fill: amount={f.amount} price={f.price}")
        expected_cost = f.price * f.amount
        if expected_cost > 0 and abs(f.cost_quote - expected_cost) > max(0.01, expected_cost * 0.01):
            problems.append(
                f"cost_quote {f.cost_quote:.6f} != price*amount {expected_cost:.6f}")
        if f.fee_quote < 0 or (expected_cost > 0 and f.fee_quote > expected_cost * 0.05):
            problems.append(
                f"fee_quote {f.fee_quote:.6f} implausible vs notional {expected_cost:.6f}")
        if f.side == Side.SELL:
            pos = self.portfolio.get(f.symbol)
            held = pos.units if pos else 0.0
            if f.amount > held * 1.001 + 1e-12:
                problems.append(f"sold {f.amount} > tracked units {held}")
        for p in problems:
            logger.critical("FILL INVARIANT VIOLATION %s %s: %s (intent: %s)",
                            f.side.value, f.symbol, p, intent.reason)

    def _log_decision(self, r: PipelineResult, now: datetime) -> None:
        """Record EVERY decision (executed or not) to the decision log — the RL /
        risk-analysis substrate. Never raises: bookkeeping must not stop trading."""
        if self.decision_log is None:
            return
        d = r.decision
        try:
            self.decision_log.record(
                ts=now, symbol=r.intent.symbol, side=r.intent.side.value,
                is_entry=r.intent.is_entry, outcome=r.outcome.value,
                gate=(d.gate.value if d else ""), approved=(d.approved if d else False),
                notional=(d.notional if d else None), est_price=(d.est_price if d else None),
                risk_pct=(d.risk_pct_equity if d else None),
                stop_distance_pct=(d.stop_distance_pct if d else None),
                reason=r.intent.reason,
            )
        except Exception:
            logger.exception("decision-log write failed (continuing)")

    def _record_own_loss(self, fill, intent, entry_price, net_pnl: float, now: datetime) -> None:
        """Append a losing exit to the bad-trades folder (a human record; the
        weekly loop still assesses own trades from the full DB)."""
        lc = self.cfg.learning
        if not (lc.enabled and lc.write_own_losers):
            return
        from ..learning.samples import append_own_loss
        append_own_loss(
            lc.bad_trades_dir, symbol=fill.symbol, side=fill.side.value,
            entry_price=entry_price or 0.0, exit_price=fill.price, pnl=net_pnl,
            risk_pct=None, reason=intent.reason, ts=now.isoformat(),
            bucket=now.strftime("%GW%V"),
        )

    def _apply_fills(self, results: list[PipelineResult], now: datetime) -> list[dict]:
        """Record every fill; return a per-fill alert payload (incl. the DB entry
        id) so the caller can push a Telegram alert. Kept sync + testable."""
        filled_any = False
        alerts: list[dict] = []
        for r in results:
            self._log_decision(r, now)  # capture the full decision stream first
            if r.outcome is not Outcome.EXECUTED or r.execution is None or r.execution.fill is None:
                continue
            filled_any = True
            f = r.execution.fill
            self._validate_fill(f, r.intent)
            intent = r.intent
            entry_price = None
            if f.side == Side.SELL:
                held = self.portfolio.get(f.symbol)
                entry_price = held.entry_price if held else None
            realized = self.portfolio.on_fill(
                f.symbol, f.side, f.price, f.amount, now,
                take_profit=intent.take_profit_price, stop=intent.stop_price,
                trail_distance=(intent.metadata or {}).get("trail_distance"),
            )
            net = None
            if f.side == Side.SELL:
                net = realized - f.fee_quote
                self.store.record_realized_pnl(now, net)
                if net < 0:
                    self._record_own_loss(f, intent, entry_price, net, now)
            trade_id = self.trade_log.record(
                ts=f.timestamp, symbol=f.symbol, side=f.side.value, price=f.price,
                amount=f.amount, cost_quote=f.cost_quote, fee_quote=f.fee_quote,
                role=f.role.value, is_entry=intent.is_entry, realized_pnl=realized,
                reason=intent.reason, valuation_pct=(intent.metadata or {}).get("valuation_pct"),
                client_order_id=f.client_order_id,
                risk_pct=(r.decision.risk_pct_equity if r.decision else None),
            )
            alerts.append({
                "symbol": f.symbol, "side": f.side.value, "is_entry": intent.is_entry,
                "price": f.price, "amount": f.amount, "cost_quote": f.cost_quote,
                "fee_quote": f.fee_quote, "realized": realized, "net": net,
                "entry_price": entry_price, "trade_id": trade_id, "ts": f.timestamp,
            })
        if filled_any:
            # a fill moved real balance — the next symbol's floor/sizing checks
            # must see it, so drop the cached equity immediately
            self._equity_cache = None
            if self.position_store is not None:
                self.position_store.save(self.portfolio)
        return alerts

    async def _deliver_trade_alert(self, alert: dict) -> None:
        """Push a per-fill buy/sell alert to Telegram (never raises — a failed
        alert must not stop trading)."""
        if self.trade_alert is None or not self.cfg.telegram.trade_alerts:
            return
        try:
            from ..approval.messages import format_trade_alert
            await self.trade_alert(format_trade_alert(
                alert, self.cfg.exchange.quote_currency, label=self.cfg.telegram.label))
        except Exception:
            logger.exception("trade alert delivery failed (continuing)")

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

    async def emit_learning(self, now: datetime | None = None) -> str | None:
        """Weekly learning assessment: own trades (full DB) + new external logs in
        the bad-trades folder -> candidate adjustments. Delivered to Telegram and
        written to reports/. Advisory only — never changes config."""
        lc = self.cfg.learning
        if not lc.enabled:
            return None
        try:
            from ..learning.loop import assess, paired_samples_from_db, render_learning_report
            from ..learning.samples import scan_folder

            own = paired_samples_from_db(self.trade_log.all())
            external = [s for s in scan_folder(lc.bad_trades_dir, only_new=True)[0]
                        if s.source != "live"]
            report = render_learning_report(assess(
                own, external, min_trades=lc.min_trades_per_bucket,
                quote=self.cfg.exchange.quote_currency))
        except Exception:
            logger.exception("weekly learning assessment failed")
            return None
        try:
            from pathlib import Path
            Path("reports").mkdir(parents=True, exist_ok=True)
            Path("reports/learning_latest.md").write_text(report)
        except Exception:
            logger.exception("could not write learning report")
        if self.report_deliver is not None:
            try:
                await self.report_deliver(report)
            except Exception:
                logger.exception("learning report telegram delivery failed")
        return report

    def _render_missed(self, decisions) -> str:
        """Missed opportunities = entries the algo wanted but a gate/kill-switch
        blocked. Surfaces whether the risk caps are turning away real trades."""
        from collections import Counter
        entries = [d for d in decisions if d.is_entry]
        blocked = Counter(d.gate for d in entries
                          if not d.approved and d.gate and d.gate != "OK")
        lines = ["*Missed opportunities (30d)* — breakouts the algo wanted but didn't take:"]
        if not entries:
            lines.append("- no entry signals in the window")
        elif not blocked:
            lines.append("- none — every attempted entry went through.")
        else:
            for gate, n in blocked.most_common():
                lines.append(f"- {gate}: {n}")
            lines.append("(entries a risk gate / kill switch turned away — review whether the "
                         "caps are too tight, or if these were correctly avoided)")
        return "\n".join(lines)

    async def emit_monthly_review(self, now: datetime | None = None) -> str | None:
        """Monthly deep review: 30-day performance + bad-trade learning assessment
        + missed-opportunity summary. Delivered to Telegram, written to reports/."""
        now = now or datetime.now(timezone.utc)
        parts: list[str] = ["📅 *Monthly review* — performance, bad trades & missed opportunities"]
        if self.reporter is not None:
            try:
                parts.append(self.reporter.generate(
                    now=now, days=30, market_returns_pct=self._market_returns(),
                    quote=self.cfg.exchange.quote_currency))
            except Exception:
                logger.exception("monthly performance report failed")
        lc = self.cfg.learning
        if lc.enabled:
            try:
                from ..learning.loop import (assess, paired_samples_from_db,
                                             render_learning_report)
                own = paired_samples_from_db(self.trade_log.all())
                parts.append(render_learning_report(assess(
                    own, [], min_trades=lc.min_trades_per_bucket,
                    quote=self.cfg.exchange.quote_currency)))
            except Exception:
                logger.exception("monthly learning assessment failed")
        if self.decision_log is not None:
            try:
                decisions = self.decision_log.between(now - timedelta(days=30), now)
                parts.append(self._render_missed(decisions))
            except Exception:
                logger.exception("monthly missed-opportunity summary failed")
        text = "\n\n".join(p for p in parts if p)
        try:
            from pathlib import Path
            Path("reports").mkdir(parents=True, exist_ok=True)
            Path("reports/monthly_review_latest.md").write_text(text)
        except Exception:
            logger.exception("could not write monthly review")
        if self.report_deliver is not None:
            try:
                await self.report_deliver(text)
            except Exception:
                logger.exception("monthly review telegram delivery failed")
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
        # verify the persisted tracker against reality BEFORE trading (double-buy /
        # phantom-exit protection after a crash or an out-of-band fill)
        await self.reconcile_positions()

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
        next_monthly = (
            next_monthly_time(datetime.now(timezone.utc), rep.monthly_day, rep.monthly_hour_utc)
            if rep.monthly_enabled else None
        )
        logger.info("Runner started (dry_run=%s, symbols=%s); next weekly: %s; next monthly: %s",
                    self.cfg.app.dry_run, self.cfg.exchange.symbols_allowlist,
                    next_report, next_monthly)
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
                    if self.cfg.learning.enabled and self.cfg.learning.weekly:
                        await self.emit_learning(now)
                    next_report = next_report_time(now, rep.weekly_day, rep.weekly_hour_utc)
                if next_monthly is not None and now >= next_monthly:
                    await self.emit_monthly_review(now)
                    next_monthly = next_monthly_time(now, rep.monthly_day, rep.monthly_hour_utc)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            for t in tasks:
                t.cancel()
            await self.adapter.close()
            logger.info("Runner stopped.")

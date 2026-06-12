"""Build the /status report from the live components (balance, floor, daily
counters, event-risk window, kill-switch state)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable

from ..config import Config
from ..events.event_risk import EventRiskModule
from ..events.kill_switch import KillSwitch
from ..risk.state import RiskStateStore

# async () -> (equity, free)
BalanceProvider = Callable[[], Awaitable[tuple[float, float]]]


class StatusReporter:
    def __init__(
        self,
        config: Config,
        store: RiskStateStore,
        kill_switch: KillSwitch | None = None,
        event_module: EventRiskModule | None = None,
        balance_provider: BalanceProvider | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.kill_switch = kill_switch
        self.event_module = event_module
        self.balance_provider = balance_provider

    async def build(self, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        quote = self.config.exchange.quote_currency
        c = self.store.get_daily_counters(now)
        lines = ["📊 *Status*"]

        lines.append(f"trading enabled: {self.store.is_trading_enabled()}")
        if not self.store.is_trading_enabled() and self.store.disabled_reason():
            lines.append(f"  reason: {self.store.disabled_reason()}")
        lines.append(f"dry-run: {self.config.app.dry_run}")

        if self.balance_provider is not None:
            try:
                equity, free = await self.balance_provider()
                lines.append(f"equity: {equity:.2f} {quote}  free: {free:.2f} {quote}")
            except Exception:
                lines.append("balance: unavailable")
        lines.append(
            f"floor: {self.config.risk.floor_quote:.2f} "
            f"(+buffer {self.config.risk.floor_buffer_quote:.2f}) {quote}"
        )

        lines.append(
            f"today: {c.trades} trades, {c.traded_notional:.2f} {quote} notional, "
            f"pnl {c.realized_pnl:.2f}"
        )
        lines.append(
            f"approval threshold: {self.config.telegram.approval_threshold_quote:.2f} {quote}"
        )

        if self.kill_switch is not None:
            ks = self.kill_switch.status(now)
            lines.append(
                f"kill-switch: {'PAUSED ' + (ks.reason or 'manual') if ks.paused else 'clear'}"
            )
        if self.event_module is not None:
            ep = self.event_module.posture(now)
            if ep.in_window:
                lines.append(f"event-risk: ACTIVE ({', '.join(ep.events)})")
            else:
                lines.append("event-risk: none")
        return "\n".join(lines)

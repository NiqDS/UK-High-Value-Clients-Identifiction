"""Latency / heartbeat monitor with auto-suspend.

Tracks round-trip API latency and connection health. If the session degrades
(latency over the cap) or disconnects (ping fails) for a configurable number of
consecutive checks, it **auto-suspends new trading** and alerts — optionally
cancelling resting orders via a callback — and **never crash-loops**. When the
connection recovers for a configurable number of checks it resumes.

Suspension is kept *separate* from the persistent trading-enabled flag and the
manual pause, so a transient network blip doesn't get conflated with a manual
or floor halt. The pipeline consults :meth:`HealthMonitor.trading_allowed`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..config import AppConfig

logger = logging.getLogger(__name__)

AsyncHook = Callable[[], Awaitable[None]]
AlertHook = Callable[[str], Awaitable[None]]
PingCallable = Callable[[], Awaitable[None]]


@dataclass
class HealthState:
    suspended: bool = False
    reason: str = ""
    last_latency_ms: float | None = None
    consecutive_failures: int = 0


class HealthMonitor:
    def __init__(
        self,
        config: AppConfig,
        on_alert: AlertHook | None = None,
        on_suspend: AsyncHook | None = None,
        on_resume: AsyncHook | None = None,
    ) -> None:
        self.config = config
        self.on_alert = on_alert
        self.on_suspend = on_suspend
        self.on_resume = on_resume
        self._suspended = False
        self._reason = ""
        self._failures = 0
        self._healthy_streak = 0
        self._last_latency: float | None = None

    def trading_allowed(self) -> bool:
        return not self._suspended

    def status(self) -> HealthState:
        return HealthState(
            suspended=self._suspended,
            reason=self._reason,
            last_latency_ms=self._last_latency,
            consecutive_failures=self._failures,
        )

    async def _alert(self, text: str) -> None:
        logger.warning(text)
        if self.on_alert is not None:
            try:
                await self.on_alert(text)
            except Exception:  # alerting must never break the monitor
                logger.exception("health alert hook failed")

    async def record(self, latency_ms: float | None) -> HealthState:
        """Record one health sample. ``None`` latency means the ping failed."""
        self._last_latency = latency_ms
        degraded = latency_ms is None or latency_ms > self.config.max_api_latency_ms

        if degraded:
            self._failures += 1
            self._healthy_streak = 0
            if self._failures >= self.config.max_consecutive_failures and not self._suspended:
                self._suspended = True
                self._reason = (
                    "connection lost"
                    if latency_ms is None
                    else f"latency {latency_ms:.0f}ms > {self.config.max_api_latency_ms}ms"
                )
                await self._alert(f"⚠️ Trading auto-suspended: {self._reason}")
                if self.config.cancel_orders_on_suspend and self.on_suspend is not None:
                    try:
                        await self.on_suspend()
                    except Exception:
                        logger.exception("on_suspend hook failed")
        else:
            self._failures = 0
            self._healthy_streak += 1
            if self._suspended and self._healthy_streak >= self.config.health_recovery_samples:
                self._suspended = False
                self._reason = ""
                await self._alert("✅ Connection healthy — trading resumed")
                if self.on_resume is not None:
                    try:
                        await self.on_resume()
                    except Exception:
                        logger.exception("on_resume hook failed")
        return self.status()

    async def run(self, ping: PingCallable, stop_event: asyncio.Event) -> None:
        """Heartbeat loop: ping, measure round-trip latency, record. Never raises."""
        loop = asyncio.get_running_loop()
        interval = self.config.heartbeat_interval_seconds
        while not stop_event.is_set():
            try:
                t0 = loop.time()
                await ping()
                latency_ms = (loop.time() - t0) * 1000.0
                await self.record(latency_ms)
            except Exception:
                logger.warning("heartbeat ping failed", exc_info=True)
                await self.record(None)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

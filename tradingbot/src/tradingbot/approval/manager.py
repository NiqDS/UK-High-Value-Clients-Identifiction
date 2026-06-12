"""Transport-agnostic approval workflow.

The :class:`ApprovalManager` issues an approval request, waits for an
approve/reject decision, and **auto-rejects on timeout** (the conservative
default). It talks to the outside world only through a :class:`Notifier`, so it
is fully testable with a fake notifier and reused as-is behind Telegram.

``ApprovalManager.request`` matches the pipeline's ``Approver`` signature, so it
drops straight into :class:`TradingPipeline`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from ..domain import OrderIntent
from ..risk.engine import RiskDecision

logger = logging.getLogger(__name__)


@dataclass
class ApprovalRequest:
    id: str
    intent: OrderIntent
    decision: RiskDecision
    created_at: datetime
    future: "asyncio.Future[bool]" = field(repr=False)


class Notifier(Protocol):
    async def send_approval_request(self, req: ApprovalRequest) -> None: ...
    async def send_message(self, text: str) -> None: ...


class ApprovalManager:
    def __init__(self, notifier: Notifier, timeout_seconds: int = 300) -> None:
        self.notifier = notifier
        self.timeout_seconds = timeout_seconds
        self._pending: dict[str, ApprovalRequest] = {}

    @property
    def pending(self) -> list[ApprovalRequest]:
        return list(self._pending.values())

    async def request(self, intent: OrderIntent, decision: RiskDecision) -> bool:
        """Pipeline approver entry point. Returns True only on explicit approval."""
        loop = asyncio.get_running_loop()
        req = ApprovalRequest(
            id=uuid.uuid4().hex[:12],
            intent=intent,
            decision=decision,
            created_at=datetime.now(timezone.utc),
            future=loop.create_future(),
        )
        self._pending[req.id] = req
        logger.info("Approval requested %s: %s %s notional=%s",
                    req.id, intent.side.value, intent.symbol,
                    f"{decision.notional:.2f}" if decision.notional else "?")
        await self.notifier.send_approval_request(req)
        try:
            return await asyncio.wait_for(req.future, self.timeout_seconds)
        except asyncio.TimeoutError:
            logger.info("Approval %s timed out — auto-rejected", req.id)
            await self.notifier.send_message(f"⏱ Approval {req.id} timed out — auto-rejected.")
            return False
        finally:
            self._pending.pop(req.id, None)

    def resolve(self, request_id: str, approved: bool) -> bool:
        """Resolve a pending request (called by the Telegram callback handler).

        Returns True if a pending request was resolved, False otherwise (unknown
        id or already resolved)."""
        req = self._pending.get(request_id)
        if req is None or req.future.done():
            return False
        req.future.set_result(approved)
        return True

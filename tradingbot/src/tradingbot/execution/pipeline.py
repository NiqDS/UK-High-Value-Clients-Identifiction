"""The trading pipeline: intent → risk engine → (approval) → execution → log.

Single choke point so every order intent is audited and gated identically.
The approval step is pluggable: in dry-run we inject an auto-approver; the real
Telegram approver lands in a later milestone. With no approver, intents that
require approval are HELD (never executed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

from ..config import Config
from ..domain import OrderIntent, Side
from ..risk.engine import AccountSnapshot, RiskDecision, RiskEngine
from ..risk.state import RiskStateStore
from .executor import Executor
from .models import ExecutionResult

logger = logging.getLogger(__name__)

# (intent, decision) -> approved?  May be async.
Approver = Callable[[OrderIntent, RiskDecision], Awaitable[bool]]


async def auto_approve(intent: OrderIntent, decision: RiskDecision) -> bool:
    return True


class Outcome(str, Enum):
    EXECUTED = "executed"
    REJECTED_BY_RISK = "rejected_by_risk"
    PENDING_APPROVAL = "pending_approval"
    REJECTED_BY_APPROVAL = "rejected_by_approval"
    NOT_EXECUTED = "not_executed"  # execution-layer guard (slippage/taker)


@dataclass
class PipelineResult:
    intent: OrderIntent
    decision: RiskDecision
    outcome: Outcome
    execution: ExecutionResult | None = None


class TradingPipeline:
    def __init__(
        self,
        config: Config,
        engine: RiskEngine,
        executor: Executor,
        store: RiskStateStore,
        approver: Approver | None = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.executor = executor
        self.store = store
        self.approver = approver

    async def process(self, intent: OrderIntent, snapshot: AccountSnapshot) -> PipelineResult:
        decision = self.engine.evaluate(intent, snapshot)
        logger.info(
            "RISK %s %s %s notional=%s gate=%s approval=%s",
            "OK" if decision.approved else "REJECT",
            intent.side.value, intent.symbol,
            f"{decision.notional:.2f}" if decision.notional else "?",
            decision.gate.value, decision.requires_approval,
        )
        if not decision.approved:
            return PipelineResult(intent, decision, Outcome.REJECTED_BY_RISK)

        if decision.requires_approval:
            if self.approver is None:
                logger.info("Intent requires approval; no approver wired — holding")
                return PipelineResult(intent, decision, Outcome.PENDING_APPROVAL)
            approved = await self.approver(intent, decision)
            if not approved:
                return PipelineResult(intent, decision, Outcome.REJECTED_BY_APPROVAL)

        if snapshot.ticker is None:
            return PipelineResult(intent, decision, Outcome.NOT_EXECUTED)

        result = await self.executor.execute(intent, decision, snapshot.ticker)
        if result.filled and result.fill is not None:
            # entries consume the daily trade/notional budget on a real fill
            if intent.is_entry:
                self.store.record_trade(snapshot.now, result.fill.cost_quote)
            return PipelineResult(intent, decision, Outcome.EXECUTED, result)
        return PipelineResult(intent, decision, Outcome.NOT_EXECUTED, result)

    async def process_signals(
        self, intents: list[OrderIntent], snapshot: AccountSnapshot
    ) -> list[PipelineResult]:
        results = []
        for intent in intents:
            results.append(await self.process(intent, snapshot))
        return results

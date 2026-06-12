"""Fee-aware order execution: maker-first pricing + slippage guard.

Runs AFTER the risk engine has approved an intent. Responsibilities:
  - choose maker vs taker per policy (maker-first; taker only when allowed);
  - compute a passive maker limit price (offset away from mid so it rests as a
    maker), or a taker price that crosses;
  - re-check slippage against the decision price (the market may have moved
    since the risk decision) and skip if it exceeds the cap;
  - hand the order to the broker and return the result.
"""

from __future__ import annotations

import logging
import uuid

from ..config import Config
from ..domain import OrderIntent, OrderType, Side
from ..exchange.models import Ticker
from ..risk.engine import RiskDecision
from .broker import Broker
from .models import ExecStatus, ExecutionResult, LiquidityRole, Order

logger = logging.getLogger(__name__)


class Executor:
    def __init__(self, config: Config, broker: Broker) -> None:
        self.config = config
        self.broker = broker

    def _maker_price(self, side: Side, best_bid: float, best_ask: float) -> float:
        """Passive limit price that rests as a maker (offset away from mid)."""
        offset = self.config.fees.maker_offset_pct / 100.0
        if side == Side.BUY:
            return best_bid * (1 - offset)
        return best_ask * (1 + offset)

    def _build_order(
        self, intent: OrderIntent, decision: RiskDecision, ticker: Ticker
    ) -> ExecutionResult | Order:
        fees = self.config.fees
        best_bid, best_ask = ticker.bid, ticker.ask
        if not best_bid or not best_ask:
            return ExecutionResult(ExecStatus.REJECTED, None, reason="no bid/ask for execution")

        client_order_id = intent.client_order_id or f"tb-{uuid.uuid4().hex[:16]}"
        reference = decision.est_price or (best_ask if intent.side == Side.BUY else best_bid)

        is_market = intent.order_type is OrderType.MARKET
        use_maker = fees.maker_first and not is_market

        if use_maker:
            role = LiquidityRole.MAKER
            price = self._maker_price(intent.side, best_bid, best_ask)
            order_type = OrderType.LIMIT
        else:
            # taker path — only allowed when configured
            if not fees.allow_taker_fallback:
                return ExecutionResult(
                    ExecStatus.REJECTED, None,
                    reason="taker order required but allow_taker_fallback is false",
                )
            role = LiquidityRole.TAKER
            price = best_ask if intent.side == Side.BUY else best_bid
            order_type = intent.order_type

        return Order(
            client_order_id=client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            amount=abs(intent.amount),
            order_type=order_type,
            role=role,
            price=price,
            reference_price=reference,
        )

    def _slippage_ok(self, order: Order) -> bool:
        cap = self.config.fees.max_slippage_pct
        if cap <= 0 or order.reference_price <= 0 or order.price is None:
            return True
        dev = abs(order.price - order.reference_price) / order.reference_price * 100.0
        return dev <= cap

    async def execute(
        self, intent: OrderIntent, decision: RiskDecision, ticker: Ticker
    ) -> ExecutionResult:
        built = self._build_order(intent, decision, ticker)
        if isinstance(built, ExecutionResult):  # rejected during build
            logger.info("Execution rejected: %s", built.reason)
            return built
        order = built

        if not self._slippage_ok(order):
            dev = abs(order.price - order.reference_price) / order.reference_price * 100.0
            logger.info("Execution skipped: slippage %.3f%% > cap", dev)
            return ExecutionResult(
                ExecStatus.SKIPPED, order,
                reason=f"slippage {dev:.3f}% exceeds cap {self.config.fees.max_slippage_pct}%",
            )

        return await self.broker.place_order(order)

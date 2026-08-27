"""Fee-aware, maker-first execution layer with paper + live brokers."""

from .broker import Broker, LiveBroker, PaperBroker
from .executor import Executor
from .models import ExecStatus, ExecutionResult, Fill, LiquidityRole, Order
from .pipeline import Outcome, PipelineResult, TradingPipeline, auto_approve

__all__ = [
    "Broker",
    "PaperBroker",
    "LiveBroker",
    "Executor",
    "ExecStatus",
    "ExecutionResult",
    "Fill",
    "LiquidityRole",
    "Order",
    "Outcome",
    "PipelineResult",
    "TradingPipeline",
    "auto_approve",
]

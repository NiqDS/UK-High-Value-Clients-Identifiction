"""Message rendering + callback parsing (pure, unit-testable)."""

from __future__ import annotations

from ..config import Config
from ..domain import OrderIntent
from ..risk.engine import RiskDecision

APPROVE_PREFIX = "approve"
REJECT_PREFIX = "reject"


def callback_data(action: str, request_id: str) -> str:
    return f"{action}:{request_id}"


def parse_callback(data: str) -> tuple[str, str] | None:
    """Parse 'approve:<id>' / 'reject:<id>'. Returns None if malformed."""
    if ":" not in data:
        return None
    action, _, request_id = data.partition(":")
    if action not in (APPROVE_PREFIX, REJECT_PREFIX) or not request_id:
        return None
    return action, request_id


def _fmt(value: float | None, nd: int = 2) -> str:
    return f"{value:.{nd}f}" if value is not None else "—"


def render_approval_message(config: Config, intent: OrderIntent, decision: RiskDecision) -> str:
    quote = config.exchange.quote_currency
    floor = config.risk.floor_quote
    proj = decision.projected_free_quote
    floor_flag = ""
    if proj is not None and proj <= floor:
        floor_flag = "  ⚠️ BELOW FLOOR"
    lines = [
        "🔔 *Trade needs approval*",
        f"`{intent.side.value.upper()} {intent.symbol}`",
        f"size: {intent.amount:.8f}",
        f"est. price: {_fmt(decision.est_price, 4)} {quote}",
        f"notional: {_fmt(decision.notional)} {quote}",
        f"est. round-trip fees: {_fmt(decision.round_trip_fee_quote, 4)} {quote}",
        f"net-of-fee target: {_fmt(decision.net_after_fees_quote, 4)} {quote}",
    ]
    # Weighted-average-cost (VWAP) valuation, when the strategy supplies it.
    wac = intent.metadata.get("vwap") if intent.metadata else None
    if wac is not None:
        val_pct = intent.metadata.get("valuation_pct")
        label = intent.metadata.get("valuation", "")
        lines.append(f"VWAP (wtd avg cost): {_fmt(wac, 4)} {quote}")
        lines.append(f"valuation: {val_pct:+.2f}% ({label})")
    lines.append(f"projected free: {_fmt(proj)} vs floor {_fmt(floor)} {quote}{floor_flag}")
    return "\n".join(lines)


def render_emergency_exit_message(config: Config, intent: OrderIntent, decision: RiskDecision) -> str:
    quote = config.exchange.quote_currency
    meta = intent.metadata or {}
    return (
        "🚨 *EMERGENCY FORCE-EXIT*\n"
        f"`{intent.side.value.upper()} {intent.symbol}` (overvaluation ceiling hit)\n"
        f"VWAP (wtd avg cost): {_fmt(meta.get('vwap'), 4)} {quote}\n"
        f"valuation: {meta.get('valuation_pct', 0.0):+.2f}% ({meta.get('valuation', '')})\n"
        f"size: {intent.amount:.8f}  est. price: {_fmt(decision.est_price, 4)} {quote}\n"
        f"reason: {intent.reason}"
    )


def render_settings(settings: dict, quote: str = "USD") -> str:
    return (
        "⚙️ *Settings*\n"
        f"approval threshold: {settings['approval_threshold_quote']:.2f} {quote} "
        f"(0 = approve every trade)\n"
        f"max notional/trade: {settings['max_notional_per_trade_quote']:.2f} {quote}\n"
        f"max trades/day: {settings['max_trades_per_day']}\n"
        f"max daily loss: {settings['max_daily_loss_quote']:.2f} {quote}\n"
        f"buy valuation floor: {settings['buy_valuation_floor_pct']:+.2f}% vs VWAP\n"
        f"force-exit ceiling: {settings['force_exit_overvaluation_pct']:.2f}% above VWAP\n"
        f"trading enabled: {settings['trading_enabled']}\n\n"
        "Change with:\n"
        "`/set_threshold <amt>`  `/set_max_notional <amt>`\n"
        "`/set_max_trades <n>`  `/set_daily_loss <amt>`\n"
        "`/set_buy_floor <pct>`  `/set_force_exit <pct>`\n"
        "`/pause`  `/resume`"
    )

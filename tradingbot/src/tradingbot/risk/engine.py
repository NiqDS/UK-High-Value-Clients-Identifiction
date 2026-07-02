"""The risk engine — every OrderIntent must pass through here before it can
become a real order. This is the most important module in the bot.

Gates (evaluated in order; first failure wins):

  1. TRADING_DISABLED   master flag off (kill switch / prior floor halt)
  2. SYMBOL_NOT_ALLOWED not on the per-symbol allowlist
  3. INVALID_INTENT     non-positive size, or no usable price
  4. DAILY_LOSS_STOP    realized+unrealized daily loss exceeds the cap
  5. MAX_TRADES_PER_DAY daily trade count would be exceeded
  6. MAX_DAILY_NOTIONAL daily traded notional would be exceeded
  7. MAX_OPEN_POSITIONS too many open positions
  8. MAX_OPEN_ORDERS    too many concurrent open orders
  9. MIN_NOTIONAL       below dust / exchange minimum
 10. MAX_NOTIONAL       above absolute or %-of-equity cap (the tighter wins)
 11. PER_TRADE_RISK     risk-to-stop exceeds the per-trade risk budget
 12. SPREAD_TOO_WIDE    live bid/ask spread too wide to profit
 13. FEE_GATE           target move does not clear round-trip fees + margin
 14. FLOOR              projected post-trade free balance at/below the floor
                        → also HALTS trading (persistent flag flipped off)

A floor breach is the only gate that mutates persistent state (it disables
trading). Daily-loss and the rest simply reject the intent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from ..config import Config
from ..domain import OrderIntent, Side
from ..exchange.models import Ticker
from .state import RiskStateStore

logger = logging.getLogger(__name__)


class RiskGate(str, Enum):
    OK = "ok"
    TRADING_DISABLED = "trading_disabled"
    SYMBOL_NOT_ALLOWED = "symbol_not_allowed"
    INVALID_INTENT = "invalid_intent"
    DAILY_LOSS_STOP = "daily_loss_stop"
    MAX_TRADES_PER_DAY = "max_trades_per_day"
    MAX_DAILY_NOTIONAL = "max_daily_notional"
    MAX_OPEN_POSITIONS = "max_open_positions"
    MAX_OPEN_ORDERS = "max_open_orders"
    MIN_NOTIONAL = "min_notional"
    MAX_NOTIONAL = "max_notional"
    PER_TRADE_RISK = "per_trade_risk"
    SPREAD_TOO_WIDE = "spread_too_wide"
    FEE_GATE = "fee_gate"
    FLOOR = "floor"


@dataclass
class AccountSnapshot:
    """Live, exchange-derived state the engine evaluates against. Daily counters
    and the trading-enabled flag come from the :class:`RiskStateStore`, not here.
    """

    equity_quote: float
    free_quote: float
    used_quote: float = 0.0
    open_positions: int = 0
    open_orders: int = 0
    ticker: Ticker | None = None
    unrealized_pnl_quote: float = 0.0
    # Optional per-symbol overrides (e.g. pulled from the exchange fee tiers /
    # market metadata). Fall back to config when absent.
    maker_fee_pct: float | None = None
    taker_fee_pct: float | None = None
    market_min_notional_quote: float | None = None
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class RiskDecision:
    approved: bool
    gate: RiskGate
    reason: str
    requires_approval: bool = False
    halt: bool = False
    est_price: float | None = None
    notional: float | None = None
    projected_free_quote: float | None = None
    round_trip_fee_quote: float | None = None
    net_after_fees_quote: float | None = None

    @classmethod
    def reject(cls, gate: RiskGate, reason: str, **kw) -> "RiskDecision":
        return cls(approved=False, gate=gate, reason=reason, **kw)


class RiskEngine:
    def __init__(self, config: Config, store: RiskStateStore) -> None:
        self.config = config
        self.store = store

    # -- fee helpers --------------------------------------------------------
    def _effective_fee_pct(self, snapshot: AccountSnapshot) -> float:
        """Conservative per-leg fee rate (%). Uses taker when taker fallback is
        allowed (worst case we might actually pay), else maker. Prefers
        exchange-provided overrides on the snapshot."""
        fees = self.config.fees
        if fees.allow_taker_fallback:
            return snapshot.taker_fee_pct if snapshot.taker_fee_pct is not None else fees.taker_fee_pct
        return snapshot.maker_fee_pct if snapshot.maker_fee_pct is not None else fees.maker_fee_pct

    def _est_price(self, intent: OrderIntent, snapshot: AccountSnapshot) -> float | None:
        if intent.price is not None and intent.price > 0:
            return intent.price
        t = snapshot.ticker
        if t is None:
            return None
        px = t.ask if intent.side == Side.BUY else t.bid
        return px if px and px > 0 else None

    # -- main entry ---------------------------------------------------------
    def evaluate(self, intent: OrderIntent, snapshot: AccountSnapshot) -> RiskDecision:
        cfg = self.config
        risk = cfg.risk

        # 1. master trading flag
        if not self.store.is_trading_enabled():
            return RiskDecision.reject(
                RiskGate.TRADING_DISABLED,
                f"Trading disabled: {self.store.disabled_reason() or 'master flag off'}",
            )

        # 2. symbol allowlist
        if intent.symbol not in cfg.exchange.symbols_allowlist:
            return RiskDecision.reject(
                RiskGate.SYMBOL_NOT_ALLOWED, f"{intent.symbol} not in allowlist"
            )

        # 3. intent sanity + usable price
        if intent.amount <= 0:
            return RiskDecision.reject(RiskGate.INVALID_INTENT, "amount must be > 0")
        est_price = self._est_price(intent, snapshot)
        if est_price is None:
            return RiskDecision.reject(
                RiskGate.INVALID_INTENT, "no usable price (no limit price and no ticker)"
            )
        notional = intent.notional(est_price)

        # 4. daily loss / drawdown stop
        counters = self.store.get_daily_counters(snapshot.now)
        daily_pnl = counters.realized_pnl + snapshot.unrealized_pnl_quote
        if risk.max_daily_loss_quote > 0 and daily_pnl <= -risk.max_daily_loss_quote:
            return RiskDecision.reject(
                RiskGate.DAILY_LOSS_STOP,
                f"daily loss {daily_pnl:.2f} <= -{risk.max_daily_loss_quote}",
                est_price=est_price, notional=notional,
            )

        # 5/6. daily counts & notional (entries consume budget)
        if intent.is_entry:
            if counters.trades + 1 > risk.max_trades_per_day:
                return RiskDecision.reject(
                    RiskGate.MAX_TRADES_PER_DAY,
                    f"would exceed max {risk.max_trades_per_day} trades/day",
                    est_price=est_price, notional=notional,
                )
            if counters.traded_notional + notional > risk.max_daily_traded_notional_quote:
                return RiskDecision.reject(
                    RiskGate.MAX_DAILY_NOTIONAL,
                    f"would exceed daily notional cap {risk.max_daily_traded_notional_quote}",
                    est_price=est_price, notional=notional,
                )

            # 7/8. open positions / orders
            if snapshot.open_positions >= risk.max_open_positions:
                return RiskDecision.reject(
                    RiskGate.MAX_OPEN_POSITIONS,
                    f"open positions {snapshot.open_positions} >= {risk.max_open_positions}",
                    est_price=est_price, notional=notional,
                )
            if snapshot.open_orders >= risk.max_concurrent_open_orders:
                return RiskDecision.reject(
                    RiskGate.MAX_OPEN_ORDERS,
                    f"open orders {snapshot.open_orders} >= {risk.max_concurrent_open_orders}",
                    est_price=est_price, notional=notional,
                )

        # 9. min notional (dust / exchange minimum)
        min_notional = risk.min_notional_per_trade_quote
        if snapshot.market_min_notional_quote is not None:
            min_notional = max(min_notional, snapshot.market_min_notional_quote)
        if notional < min_notional:
            return RiskDecision.reject(
                RiskGate.MIN_NOTIONAL,
                f"notional {notional:.2f} < min {min_notional}",
                est_price=est_price, notional=notional,
            )

        # 10. max notional (absolute and %-of-equity; tighter wins)
        max_notional = risk.max_notional_per_trade_quote
        if risk.max_notional_per_trade_pct_equity > 0 and snapshot.equity_quote > 0:
            pct_cap = snapshot.equity_quote * risk.max_notional_per_trade_pct_equity / 100.0
            max_notional = min(max_notional, pct_cap)
        if notional > max_notional:
            return RiskDecision.reject(
                RiskGate.MAX_NOTIONAL,
                f"notional {notional:.2f} > max {max_notional:.2f}",
                est_price=est_price, notional=notional,
            )

        # 11. per-trade risk cap (needs a stop to evaluate)
        if intent.is_entry and intent.stop_price is not None and snapshot.equity_quote > 0:
            risk_quote = abs(intent.amount) * abs(est_price - intent.stop_price)
            budget = snapshot.equity_quote * risk.per_trade_risk_pct / 100.0
            if risk_quote > budget:
                return RiskDecision.reject(
                    RiskGate.PER_TRADE_RISK,
                    f"risk-to-stop {risk_quote:.2f} > budget {budget:.2f} "
                    f"({risk.per_trade_risk_pct}% of equity)",
                    est_price=est_price, notional=notional,
                )

        # 12. spread guard
        if snapshot.ticker is not None and cfg.fees.max_spread_pct > 0:
            spread = snapshot.ticker.spread_pct
            if spread is not None and spread > cfg.fees.max_spread_pct:
                return RiskDecision.reject(
                    RiskGate.SPREAD_TOO_WIDE,
                    f"spread {spread:.3f}% > max {cfg.fees.max_spread_pct}%",
                    est_price=est_price, notional=notional,
                )

        # 13. fee gate — entries must clear round-trip fees + safety margin.
        #     A trend-rider has no fixed take-profit (its exit is a channel/stop
        #     break), so the TP-based proof cannot apply. When require_take_profit is
        #     off AND there is no TP, skip this gate: the captured channel move
        #     dwarfs the maker round-trip fee, and the floor / per-trade-risk / sleeve
        #     caps still bound the trade. TP-bearing entries are always gated.
        fee_pct = self._effective_fee_pct(snapshot)
        rt_fee_quote = self._round_trip_fee_quote(intent, est_price, fee_pct)
        gate_fees = intent.is_entry and (
            cfg.fees.require_take_profit or intent.take_profit_price is not None
        )
        if gate_fees:
            decision = self._fee_gate(intent, est_price, fee_pct, notional)
            if not decision.approved:
                return decision

        # 14. account floor — projected post-trade free balance
        proj_free = self._projected_free(intent, snapshot, est_price, fee_pct)
        if proj_free <= risk.floor_quote:
            self.store.set_trading_enabled(
                False, f"Floor reached: projected free {proj_free:.2f} <= floor {risk.floor_quote}"
            )
            logger.warning("FLOOR BREACH — trading halted (projected free %.2f)", proj_free)
            return RiskDecision.reject(
                RiskGate.FLOOR,
                f"projected free {proj_free:.2f} <= floor {risk.floor_quote} — trading HALTED",
                halt=True, est_price=est_price, notional=notional,
                projected_free_quote=proj_free, round_trip_fee_quote=rt_fee_quote,
            )

        # approved. Only ENTRIES require human approval — an exit reduces risk
        # and must never wait on (or be dropped by) a missed approval tap.
        threshold = cfg.telegram.approval_threshold_quote
        requires_approval = intent.is_entry and notional >= threshold
        return RiskDecision(
            approved=True, gate=RiskGate.OK, reason="approved",
            requires_approval=requires_approval,
            est_price=est_price, notional=notional,
            projected_free_quote=proj_free, round_trip_fee_quote=rt_fee_quote,
        )

    # -- helpers ------------------------------------------------------------
    def _round_trip_fee_quote(self, intent: OrderIntent, est_price: float, fee_pct: float) -> float:
        rate = fee_pct / 100.0
        entry_notional = intent.notional(est_price)
        exit_price = intent.take_profit_price if intent.take_profit_price else est_price
        exit_notional = abs(intent.amount) * exit_price
        return entry_notional * rate + exit_notional * rate

    def _fee_gate(
        self, intent: OrderIntent, est_price: float, fee_pct: float, notional: float
    ) -> RiskDecision:
        if intent.take_profit_price is None or intent.take_profit_price <= 0:
            return RiskDecision.reject(
                RiskGate.FEE_GATE,
                "entry has no take-profit target; cannot prove it clears fees",
                est_price=est_price, notional=notional,
            )
        rate = fee_pct / 100.0
        entry_notional = notional
        exit_notional = abs(intent.amount) * intent.take_profit_price
        entry_fee = entry_notional * rate
        exit_fee = exit_notional * rate
        if intent.side == Side.BUY:
            gross = exit_notional - entry_notional
        else:  # short entry: profit if price falls to TP
            gross = entry_notional - exit_notional
        net = gross - entry_fee - exit_fee
        required = entry_notional * self.config.fees.round_trip_fee_safety_margin_pct / 100.0
        if net < required:
            return RiskDecision.reject(
                RiskGate.FEE_GATE,
                f"net after fees {net:.4f} < required margin {required:.4f} "
                f"(round-trip fee {entry_fee + exit_fee:.4f})",
                est_price=est_price, notional=notional,
                round_trip_fee_quote=entry_fee + exit_fee, net_after_fees_quote=net,
            )
        return RiskDecision(
            approved=True, gate=RiskGate.OK, reason="fee gate passed",
            est_price=est_price, notional=notional,
            round_trip_fee_quote=entry_fee + exit_fee, net_after_fees_quote=net,
        )

    def _projected_free(
        self, intent: OrderIntent, snapshot: AccountSnapshot, est_price: float, fee_pct: float
    ) -> float:
        rate = fee_pct / 100.0
        notional = intent.notional(est_price)
        fee = notional * rate
        if intent.side == Side.BUY:
            return snapshot.free_quote - notional - fee
        return snapshot.free_quote + notional - fee

    # -- resume helper ------------------------------------------------------
    def can_resume(self, snapshot: AccountSnapshot) -> bool:
        """Whether the balance has recovered above floor + buffer. Resume is a
        deliberate action (Telegram / restart), never silent — see the brief."""
        risk = self.config.risk
        return snapshot.free_quote >= risk.floor_quote + risk.floor_buffer_quote

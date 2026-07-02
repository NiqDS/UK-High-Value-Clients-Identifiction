"""Risk-engine tests — one per gate, plus approval routing and floor halt.

Strategy: build a base intent + snapshot that the engine APPROVES, then in each
test violate exactly one condition and assert the right gate rejects it.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from tradingbot.config import Config
from tradingbot.domain import OrderIntent, OrderType, Side
from tradingbot.exchange.models import Ticker
from tradingbot.risk.engine import AccountSnapshot, RiskEngine, RiskGate
from tradingbot.risk.state import InMemoryRiskStateStore

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def make_config(**overrides) -> Config:
    # Permissive-but-realistic base so individual gates can be isolated.
    base = {
        "exchange": {"symbols_allowlist": ["BTC/USD"], "quote_currency": "USD"},
        "telegram": {"approval_threshold_quote": 1000.0},  # base auto-executes
    }
    return Config(**_deep_merge(base, overrides))


def make_ticker(bid=99.95, ask=100.05, last=100.0) -> Ticker:
    return Ticker(symbol="BTC/USD", bid=bid, ask=ask, last=last,
                  base_volume=100.0, quote_volume=10000.0, timestamp=0)


def base_intent(**kw) -> OrderIntent:
    params = dict(
        symbol="BTC/USD", side=Side.BUY, amount=0.4, order_type=OrderType.LIMIT,
        price=100.0, take_profit_price=102.0, stop_price=99.0, is_entry=True,
    )
    params.update(kw)
    return OrderIntent(**params)


def base_snapshot(**kw) -> AccountSnapshot:
    params = dict(
        equity_quote=3000.0, free_quote=3000.0, used_quote=0.0,
        open_positions=0, open_orders=0, ticker=make_ticker(), now=NOW,
    )
    params.update(kw)
    return AccountSnapshot(**params)


def make_engine(config=None, store=None) -> RiskEngine:
    return RiskEngine(config or make_config(), store or InMemoryRiskStateStore())


# --- the happy path --------------------------------------------------------
def test_base_case_is_approved() -> None:
    d = make_engine().evaluate(base_intent(), base_snapshot())
    assert d.approved is True
    assert d.gate is RiskGate.OK
    assert d.notional == pytest.approx(40.0)
    assert d.requires_approval is False  # 40 < 1000 threshold


# --- gate 1: trading disabled ---------------------------------------------
def test_trading_disabled_blocks_everything() -> None:
    store = InMemoryRiskStateStore()
    store.set_trading_enabled(False, "kill switch")
    d = make_engine(store=store).evaluate(base_intent(), base_snapshot())
    assert d.gate is RiskGate.TRADING_DISABLED
    assert d.approved is False


# --- gate 2: symbol allowlist ---------------------------------------------
def test_symbol_not_allowed() -> None:
    d = make_engine().evaluate(base_intent(symbol="DOGE/USD"), base_snapshot())
    assert d.gate is RiskGate.SYMBOL_NOT_ALLOWED


# --- gate 3: invalid intent ------------------------------------------------
def test_non_positive_amount() -> None:
    d = make_engine().evaluate(base_intent(amount=0.0), base_snapshot())
    assert d.gate is RiskGate.INVALID_INTENT


def test_no_price_and_no_ticker() -> None:
    d = make_engine().evaluate(base_intent(price=None), base_snapshot(ticker=None))
    assert d.gate is RiskGate.INVALID_INTENT


def test_market_order_uses_ticker_price() -> None:
    # no limit price -> uses ask (100.05) for a BUY
    d = make_engine().evaluate(
        base_intent(price=None, order_type=OrderType.MARKET, take_profit_price=103.0),
        base_snapshot(),
    )
    assert d.approved is True
    assert d.est_price == pytest.approx(100.05)


# --- gate 4: daily loss stop ----------------------------------------------
def test_daily_loss_stop_via_unrealized() -> None:
    cfg = make_config(risk={"max_daily_loss_quote": 50.0})
    d = make_engine(cfg).evaluate(base_intent(), base_snapshot(unrealized_pnl_quote=-60.0))
    assert d.gate is RiskGate.DAILY_LOSS_STOP


def test_daily_loss_stop_via_realized() -> None:
    cfg = make_config(risk={"max_daily_loss_quote": 50.0})
    store = InMemoryRiskStateStore()
    store.record_realized_pnl(NOW, -55.0)
    d = make_engine(cfg, store).evaluate(base_intent(), base_snapshot())
    assert d.gate is RiskGate.DAILY_LOSS_STOP


# --- gates 5/6: daily counts & notional -----------------------------------
def test_max_trades_per_day() -> None:
    cfg = make_config(risk={"max_trades_per_day": 2})
    store = InMemoryRiskStateStore()
    store.record_trade(NOW, 10.0)
    store.record_trade(NOW, 10.0)  # at the cap; next entry exceeds
    d = make_engine(cfg, store).evaluate(base_intent(), base_snapshot())
    assert d.gate is RiskGate.MAX_TRADES_PER_DAY


def test_max_daily_notional() -> None:
    cfg = make_config(risk={"max_daily_traded_notional_quote": 50.0})
    store = InMemoryRiskStateStore()
    store.record_trade(NOW, 20.0)  # +40 would exceed 50
    d = make_engine(cfg, store).evaluate(base_intent(), base_snapshot())
    assert d.gate is RiskGate.MAX_DAILY_NOTIONAL


# --- gates 7/8: open positions / orders -----------------------------------
def test_max_open_positions() -> None:
    cfg = make_config(risk={"max_open_positions": 1})
    d = make_engine(cfg).evaluate(base_intent(), base_snapshot(open_positions=1))
    assert d.gate is RiskGate.MAX_OPEN_POSITIONS


def test_max_open_orders() -> None:
    cfg = make_config(risk={"max_concurrent_open_orders": 1})
    d = make_engine(cfg).evaluate(base_intent(), base_snapshot(open_orders=1))
    assert d.gate is RiskGate.MAX_OPEN_ORDERS


# --- gate 9: min notional --------------------------------------------------
def test_min_notional_dust() -> None:
    cfg = make_config(risk={"min_notional_per_trade_quote": 10.0})
    d = make_engine(cfg).evaluate(base_intent(amount=0.05), base_snapshot())  # 5 < 10
    assert d.gate is RiskGate.MIN_NOTIONAL


def test_exchange_min_notional_override() -> None:
    d = make_engine().evaluate(
        base_intent(amount=0.4), base_snapshot(market_min_notional_quote=100.0)  # 40 < 100
    )
    assert d.gate is RiskGate.MIN_NOTIONAL


# --- gate 10: max notional -------------------------------------------------
def test_max_notional_absolute() -> None:
    cfg = make_config(risk={"max_notional_per_trade_quote": 30.0,
                            "max_notional_per_trade_pct_equity": 0.0})
    d = make_engine(cfg).evaluate(base_intent(), base_snapshot())  # 40 > 30
    assert d.gate is RiskGate.MAX_NOTIONAL


def test_max_notional_pct_equity_is_tighter() -> None:
    # 1% of 3000 = 30 < absolute 50 -> pct cap wins
    cfg = make_config(risk={"max_notional_per_trade_quote": 50.0,
                            "max_notional_per_trade_pct_equity": 1.0})
    d = make_engine(cfg).evaluate(base_intent(), base_snapshot())  # 40 > 30
    assert d.gate is RiskGate.MAX_NOTIONAL


# --- gate 11: per-trade risk cap ------------------------------------------
def test_per_trade_risk_cap() -> None:
    # risk-to-stop = amount * |entry-stop| = 0.4 * 90 = 36 > 1% of 3000 (=30)
    cfg = make_config(risk={"per_trade_risk_pct": 1.0})
    d = make_engine(cfg).evaluate(base_intent(stop_price=10.0), base_snapshot())
    assert d.gate is RiskGate.PER_TRADE_RISK


def test_no_stop_skips_risk_cap() -> None:
    d = make_engine().evaluate(base_intent(stop_price=None), base_snapshot())
    assert d.approved is True


# --- gate 12: spread guard -------------------------------------------------
def test_spread_too_wide() -> None:
    cfg = make_config(fees={"max_spread_pct": 0.1})
    wide = make_ticker(bid=99.0, ask=101.0)  # ~2% spread
    d = make_engine(cfg).evaluate(base_intent(), base_snapshot(ticker=wide))
    assert d.gate is RiskGate.SPREAD_TOO_WIDE


# --- gate 13: fee gate -----------------------------------------------------
def test_fee_gate_rejects_unprofitable_move() -> None:
    # TP only 0.1% away cannot clear ~0.8% round-trip maker fees
    d = make_engine().evaluate(base_intent(take_profit_price=100.1), base_snapshot())
    assert d.gate is RiskGate.FEE_GATE
    assert d.net_after_fees_quote is not None and d.net_after_fees_quote < 0


def test_fee_gate_requires_take_profit() -> None:
    d = make_engine().evaluate(base_intent(take_profit_price=None), base_snapshot())
    assert d.gate is RiskGate.FEE_GATE


def test_no_tp_passes_fee_gate_when_not_required() -> None:
    # trend-rider config: no take-profit, but require_take_profit off -> approved
    cfg = make_config(fees={"require_take_profit": False})
    d = make_engine(cfg).evaluate(base_intent(take_profit_price=None), base_snapshot())
    assert d.approved is True and d.gate is RiskGate.OK


def test_tp_entry_still_gated_when_not_required() -> None:
    # turning the requirement off must NOT weaken the gate for entries that DO
    # carry a take-profit: an unprofitable TP is still rejected.
    cfg = make_config(fees={"require_take_profit": False})
    d = make_engine(cfg).evaluate(base_intent(take_profit_price=100.1), base_snapshot())
    assert d.gate is RiskGate.FEE_GATE


def test_fee_gate_uses_taker_when_fallback_allowed() -> None:
    # With taker fees (0.6%/leg) a marginal TP that passed on maker now fails.
    cfg_maker = make_config(fees={"allow_taker_fallback": False})
    cfg_taker = make_config(fees={"allow_taker_fallback": True})
    intent = base_intent(take_profit_price=101.2)  # clears maker fees, not taker
    assert make_engine(cfg_maker).evaluate(intent, base_snapshot()).approved is True
    assert make_engine(cfg_taker).evaluate(intent, base_snapshot()).gate is RiskGate.FEE_GATE


# --- gate 14: floor + halt -------------------------------------------------
def test_floor_breach_halts_trading() -> None:
    store = InMemoryRiskStateStore()
    cfg = make_config(risk={"floor_quote": 1000.0})
    # free 1020; buying 40 notional + fee drops projected free below 1000
    d = make_engine(cfg, store).evaluate(base_intent(), base_snapshot(free_quote=1030.0))
    assert d.gate is RiskGate.FLOOR
    assert d.halt is True
    assert store.is_trading_enabled() is False  # persistent halt
    # subsequent intents are now blocked by the master flag
    d2 = make_engine(cfg, store).evaluate(base_intent(), base_snapshot(free_quote=5000.0))
    assert d2.gate is RiskGate.TRADING_DISABLED


def test_sell_does_not_threaten_floor() -> None:
    cfg = make_config(risk={"floor_quote": 1000.0})
    d = make_engine(cfg).evaluate(
        base_intent(side=Side.SELL, is_entry=False, take_profit_price=None, stop_price=None),
        base_snapshot(free_quote=1010.0),
    )
    assert d.approved is True  # selling adds quote, floor not breached


def test_can_resume_requires_floor_plus_buffer() -> None:
    cfg = make_config(risk={"floor_quote": 1000.0, "floor_buffer_quote": 50.0})
    eng = make_engine(cfg)
    assert eng.can_resume(base_snapshot(free_quote=1049.0)) is False
    assert eng.can_resume(base_snapshot(free_quote=1050.0)) is True


# --- approval routing ------------------------------------------------------
def test_requires_approval_above_threshold() -> None:
    cfg = make_config(telegram={"approval_threshold_quote": 30.0})
    d = make_engine(cfg).evaluate(base_intent(), base_snapshot())  # notional 40 >= 30
    assert d.approved is True
    assert d.requires_approval is True


def test_auto_executes_below_threshold() -> None:
    cfg = make_config(telegram={"approval_threshold_quote": 100.0})
    d = make_engine(cfg).evaluate(base_intent(), base_snapshot())  # notional 40 < 100
    assert d.approved is True
    assert d.requires_approval is False


def test_exits_never_require_approval() -> None:
    # an exit reduces risk — it must execute immediately, never wait on a tap
    cfg = make_config(telegram={"approval_threshold_quote": 0.0})
    d = make_engine(cfg).evaluate(
        base_intent(is_entry=False, take_profit_price=None, stop_price=None),
        base_snapshot(),
    )
    assert d.approved is True
    assert d.requires_approval is False


def test_threshold_zero_requires_approval_for_all() -> None:
    cfg = make_config(telegram={"approval_threshold_quote": 0.0})
    d = make_engine(cfg).evaluate(base_intent(), base_snapshot())
    assert d.approved is True
    assert d.requires_approval is True

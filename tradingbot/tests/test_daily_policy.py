"""Expected-safe checks for the staged daily policy."""

from unittest.mock import AsyncMock
import pytest
from tradingbot.config import Config, Settings, Secrets
from tradingbot.domain import OrderIntent, OrderType, Side
from tradingbot.strategy.daily_policy import (
    EntryClaims,
    signal_identity,
    validate_daily_window,
    DAY_MS,
)
from tradingbot.exchange.models import Ticker, Candle, AccountBalance, Balance
from tradingbot.execution.pipeline import Outcome
from tradingbot.risk.engine import AccountSnapshot, RiskEngine, RiskGate
from tradingbot.risk.state import InMemoryRiskStateStore
from tradingbot.app.runner import AccountStateUnavailable
from tradingbot.app.portfolio import JsonPositionStore
from tests.test_runner import build, BuyOnceStrategy, NOW


def daily_config():
    return Config(
        exchange={"symbols_allowlist": ["BTC/USD"], "quote_currency": "USD"},
        strategy={
            "name": "donchian_breakout",
            "timeframe": "1d",
            "signal_on_closed_bar": True,
            "daily_policy_enabled": True,
            "donchian_entry_period": 50,
            "donchian_exit_period": 15,
            "target_notional_quote": 27,
        },
        risk={
            "floor_quote": 10,
            "floor_buffer_quote": 5,
            "max_notional_per_trade_quote": 30,
            "max_notional_per_trade_pct_equity": 15,
            "per_trade_risk_pct": 15,
        },
        fees={"allow_taker_fallback": True, "require_take_profit": False, "taker_fee_pct": 0.1},
        telegram={"enabled": False},
    )


def intent():
    return OrderIntent(
        "BTC/USD",
        Side.BUY,
        0.27,
        OrderType.MARKET,
        100,
        stop_price=90,
        client_order_id=signal_identity("BTC/USD", 123, 50, 15),
        metadata={"daily_policy": True},
    )


def test_claim_survives_restart_and_rejects_second_attempt(tmp_path):
    p = tmp_path / "claims.sqlite"
    i = intent()
    assert EntryClaims(p).claim(i)
    assert not EntryClaims(p).claim(i)
    assert signal_identity("BTC/USD", 124, 50, 15) != i.client_order_id


def test_daily_window_rejects_gap_stale_and_nonfinite():
    last = (int(NOW.timestamp() * 1000) // DAY_MS - 1) * DAY_MS
    bars = [Candle(last - (50 - j) * DAY_MS, 100, 102, 90, 101, 1) for j in range(51)]
    validate_daily_window(bars, int(NOW.timestamp() * 1000), 51)
    with pytest.raises(ValueError):
        validate_daily_window(bars[:-1], int(NOW.timestamp() * 1000), 50)
    broken = bars.copy()
    broken[1] = bars[0]
    with pytest.raises(ValueError):
        validate_daily_window(broken, int(NOW.timestamp() * 1000), 51)
    bad = bars.copy()
    bad[-1] = Candle(last, 100, float("nan"), 90, 101, 1)
    with pytest.raises(ValueError):
        validate_daily_window(bad, int(NOW.timestamp() * 1000), 51)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_account_state_rejects_entry(value):
    cfg = daily_config()
    snap = AccountSnapshot(
        equity_quote=value, free_quote=206, ticker=Ticker("BTC/USD", 100, 100, 100, 1, 1, 0)
    )
    assert not RiskEngine(cfg, InMemoryRiskStateStore()).evaluate(intent(), snap).approved


def test_market_budget_uses_current_ask():
    cfg = daily_config()
    snap = AccountSnapshot(
        equity_quote=206, free_quote=206, ticker=Ticker("BTC/USD", 149.9, 150, 150, 1, 1, 0)
    )
    decision = RiskEngine(cfg, InMemoryRiskStateStore()).evaluate(intent(), snap)
    assert decision.gate == RiskGate.MAX_NOTIONAL
    assert decision.notional == pytest.approx(40.5)


def test_daily_size_uses_cash_and_current_price(tmp_path):
    runner, _, _ = build(tmp_path, BuyOnceStrategy())
    runner.cfg = daily_config()
    snap = AccountSnapshot(
        equity_quote=206, free_quote=179, ticker=Ticker("BTC/USD", 149.9, 150, 150, 1, 1, 0)
    )
    sized = runner._size_daily_entries([intent()], snap)
    assert sized[0].amount * 150 == pytest.approx(27)
    assert sized[0].price == 150
    assert RiskEngine(runner.cfg, InMemoryRiskStateStore()).evaluate(sized[0], snap).approved


async def test_live_equity_includes_held_assets(tmp_path):
    runner, _, _ = build(tmp_path, BuyOnceStrategy())
    runner.cfg.app.dry_run = False
    runner.settings.secrets = Secrets(
        _env_file=None, exchange_api_key="fake", exchange_api_secret="fake"
    )
    runner.adapter.fetch_balance = AsyncMock(
        return_value=AccountBalance(
            {"USD": Balance("USD", 179, 0, 179), "BTC": Balance("BTC", 0.27, 0, 0.27)}
        )
    )
    runner.adapter.fetch_ticker = AsyncMock(return_value=Ticker("BTC/USD", 100, 100, 100, 1, 1, 0))
    assert await runner._equity_free() == (206, 179)


async def test_invalid_live_holding_mark_blocks_account_value(tmp_path):
    runner, _, _ = build(tmp_path, BuyOnceStrategy())
    runner.cfg.app.dry_run = False
    runner.settings.secrets = Secrets(
        _env_file=None, exchange_api_key="fake", exchange_api_secret="fake"
    )
    runner.adapter.fetch_balance = AsyncMock(
        return_value=AccountBalance(
            {"USD": Balance("USD", 179, 0, 179), "BTC": Balance("BTC", 0.27, 0, 0.27)}
        )
    )
    runner.adapter.fetch_ticker = AsyncMock(
        return_value=Ticker("BTC/USD", 100, 100, float("nan"), 1, 1, 0)
    )
    with pytest.raises(AccountStateUnavailable):
        await runner._equity_free()


async def test_paper_cash_and_positions_survive_together(tmp_path):
    from tradingbot.execution.models import Fill, ExecutionResult, ExecStatus, LiquidityRole
    from tradingbot.execution.pipeline import PipelineResult

    runner, _, portfolio = build(tmp_path, BuyOnceStrategy())
    runner.cfg.app.paper_equity = 500
    runner.position_store = JsonPositionStore(tmp_path / "positions.json", strict=True)
    f = Fill("tb-test", "BTC/USD", Side.BUY, 100, 1, 1, LiquidityRole.TAKER, 100, NOW)
    runner._apply_fills(
        [
            PipelineResult(
                intent(), None, Outcome.EXECUTED, ExecutionResult(ExecStatus.FILLED, None, fill=f)
            )
        ],
        NOW,
    )
    assert portfolio.cash_quote == 399
    recovered = runner.position_store.load()
    assert recovered.cash_quote == 399 and recovered.get("BTC/USD").units == 1
    runner.portfolio = recovered
    runner.adapter.fetch_ticker = AsyncMock(return_value=Ticker("BTC/USD", 110, 110, 110, 1, 1, 0))
    assert await runner._equity_free() == (509, 399)


async def test_pipeline_claim_prevents_duplicate_fill(tmp_path):
    runner, log, _ = build(tmp_path, BuyOnceStrategy())
    runner.cfg.fees.allow_taker_fallback = True
    runner.cfg.fees.require_take_profit = False
    runner.pipeline.entry_claim = EntryClaims(tmp_path / "claims.sqlite").claim
    i = intent()
    snap = AccountSnapshot(
        equity_quote=3000,
        free_quote=3000,
        ticker=Ticker("BTC/USD", 100, 100, 100, 1, 1, 0),
        now=NOW,
    )
    first = await runner.pipeline.process(i, snap)
    second = await runner.pipeline.process(i, snap)
    assert first.outcome == Outcome.EXECUTED and second.outcome == Outcome.NOT_EXECUTED


def test_live_builder_is_blocked_before_adapter_creation(tmp_path):
    from tradingbot.__main__ import _build_runner

    cfg = daily_config()
    cfg.app.dry_run = False
    with pytest.raises(RuntimeError, match="Live mode blocked"):
        _build_runner(Settings(config=cfg, secrets=Secrets(_env_file=None)))


async def test_daily_builder_wires_persistent_claims(tmp_path, monkeypatch):
    from tradingbot.__main__ import _build_runner

    monkeypatch.chdir(tmp_path)
    cfg = daily_config()
    cfg.exchange.name = "bybit"
    cfg.app.db_url = f"sqlite:///{tmp_path / 'test.db'}"
    runner, bot, signal = _build_runner(Settings(config=cfg, secrets=Secrets(_env_file=None)))
    assert runner.pipeline.entry_claim is not None
    await runner.adapter.close()


def test_disabled_four_hour_config_cannot_build():
    from pathlib import Path
    import yaml
    from tradingbot.__main__ import _build_runner

    cfg = Config(
        **yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "config.bybit-4h-paper.yaml").read_text()
        )
    )
    with pytest.raises(RuntimeError, match="Trading disabled"):
        _build_runner(Settings(config=cfg, secrets=Secrets(_env_file=None)))


async def test_daily_policy_cannot_use_untracked_paper_demo():
    from tradingbot.__main__ import _paper_run

    with pytest.raises(RuntimeError, match="persistent run command"):
        await _paper_run(Settings(config=daily_config(), secrets=Secrets(_env_file=None)))


async def test_daily_paper_holdings_without_cash_need_migration(tmp_path):
    runner, _, portfolio = build(tmp_path, BuyOnceStrategy())
    runner.cfg.strategy.daily_policy_enabled = True
    portfolio.on_fill("BTC/USD", Side.BUY, 100, 1, NOW)
    with pytest.raises(AccountStateUnavailable, match="verified starting cash"):
        await runner._equity_free()

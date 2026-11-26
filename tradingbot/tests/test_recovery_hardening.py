"""No-network regression coverage for the first recovery patch."""
from unittest.mock import AsyncMock
from pydantic import SecretStr
import pytest
from tradingbot.app.runtime_state import runtime_state_dir
from tradingbot.app.runner import AccountStateUnavailable
from tradingbot.config import ExchangeConfig, Secrets
from tradingbot.exchange.factory import build_adapter
from tradingbot.execution.pipeline import Outcome
from tradingbot.domain import Side
from tradingbot.app.portfolio import JsonPositionStore
from tradingbot.exchange.models import Ticker
from tests.test_runner import _live, _ReconcileClient
from tradingbot.exchange.adapter import ExchangeAdapter
from tests.test_runner import build, BuyOnceStrategy, NOW


def test_paper_and_live_do_not_share_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    url = 'sqlite:///data/account.db'
    live = runtime_state_dir(url, False)
    paper = runtime_state_dir(url, True)
    other = runtime_state_dir('sqlite:///data/other.db', True)
    assert len({live, paper, other}) == 3
    (paper / 'positions.json').write_text('paper')
    assert not (live / 'positions.json').exists()


def test_legacy_shared_state_blocks_live_migration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data').mkdir()
    old = tmp_path / 'data/positions.json'
    old.write_text('unverified')
    with pytest.raises(RuntimeError, match='reconciliation'):
        runtime_state_dir('sqlite:///data/account.db', False)
    runtime_state_dir('sqlite:///data/account.db', True)
    assert old.read_text() == 'unverified'


def test_legacy_overrides_are_not_silently_discarded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'runtime_overrides.json').write_text('{"app":{"trading_enabled":false}}')
    with pytest.raises(RuntimeError, match='reconciliation'):
        runtime_state_dir('sqlite:///data/account.db', False)


def test_paper_adapter_omits_exchange_credentials():
    secrets = Secrets(_env_file=None, exchange_api_key='fake-key', exchange_api_secret='fake-secret')
    adapter = build_adapter(ExchangeConfig(name='bybit', sandbox=False), secrets, public_only=True)
    assert not adapter._client.apiKey
    assert not adapter._client.secret


async def test_failed_live_balance_never_becomes_paper_equity(tmp_path):
    runner, _, _ = build(tmp_path, BuyOnceStrategy())
    runner.cfg.app.dry_run = False
    runner.settings.secrets.exchange_api_key = SecretStr('fake-key')
    runner.settings.secrets.exchange_api_secret = SecretStr('fake-secret')
    runner.adapter.fetch_balance = AsyncMock(side_effect=RuntimeError('fake outage'))
    with pytest.raises(AccountStateUnavailable):
        await runner._equity_free()
    assert runner._equity_cache is None


async def test_balance_outage_blocks_entries_but_preserves_known_stop_exit(tmp_path):
    runner, log, portfolio = build(tmp_path, BuyOnceStrategy())
    runner._equity_free = AsyncMock(side_effect=AccountStateUnavailable('fake outage'))
    results = await runner.run_once('BTC/USD', NOW)
    assert not any(r.outcome is Outcome.EXECUTED for r in results)
    portfolio.on_fill('BTC/USD', Side.BUY, 110, 0.4, NOW, stop=105)
    results = await runner.run_once('BTC/USD', NOW)
    assert any(r.outcome is Outcome.EXECUTED and not r.intent.is_entry for r in results)
    assert not portfolio.holding('BTC/USD')


async def test_market_loading_failure_still_closes_adapter(tmp_path):
    runner, _, _ = build(tmp_path, BuyOnceStrategy())
    runner.adapter.load_markets = AsyncMock(side_effect=RuntimeError('fake startup failure'))
    runner.adapter.close = AsyncMock()
    with pytest.raises(RuntimeError, match='fake startup failure'):
        await runner.run()
    runner.adapter.close.assert_awaited_once()


async def test_paper_balance_never_calls_private_endpoint(tmp_path):
    runner, _, _ = build(tmp_path, BuyOnceStrategy())
    runner.cfg.app.paper_equity = 0
    runner.adapter.fetch_balance = AsyncMock(side_effect=AssertionError('private call'))
    await runner._equity_free()
    runner.adapter.fetch_balance.assert_not_awaited()


async def test_unknown_open_orders_cannot_authorize_submission(tmp_path):
    runner, log, _ = build(tmp_path, BuyOnceStrategy())
    _live(runner)
    runner.adapter.fetch_open_orders = AsyncMock(side_effect=RuntimeError('fake outage'))
    runner.pipeline.process_signals = AsyncMock()
    with pytest.raises(AccountStateUnavailable, match='Open orders unavailable'):
        await runner.run_once('BTC/USD', NOW)
    runner.pipeline.process_signals.assert_not_awaited()
    assert not log.all()


async def test_failed_cancellation_blocks_submission(tmp_path):
    runner, _, _ = build(tmp_path, BuyOnceStrategy())
    _live(runner)
    runner.adapter.fetch_open_orders = AsyncMock(return_value=[{'id': '1', 'clientOrderId': 'tb-1'}])
    runner.adapter.cancel_order = AsyncMock(side_effect=RuntimeError('unknown outcome'))
    with pytest.raises(AccountStateUnavailable, match='Cancellation outcome unknown'):
        await runner._cancel_stale_orders('BTC/USD')


async def test_reconciliation_failure_preserves_portfolio(tmp_path):
    runner, _, portfolio = build(tmp_path, BuyOnceStrategy())
    _live(runner)
    portfolio.on_fill('BTC/USD', Side.BUY, 100, 0.4, NOW, stop=90)
    runner.adapter.fetch_balance = AsyncMock(side_effect=RuntimeError('fake outage'))
    with pytest.raises(AccountStateUnavailable, match='balance reconciliation'):
        await runner.reconcile_positions(NOW)
    assert portfolio.get('BTC/USD').units == 0.4
    assert portfolio.get('BTC/USD').stop == 90


@pytest.mark.parametrize('price', [0, -1, float('nan'), float('inf')])
async def test_invalid_reconciliation_price_cannot_delete_holding(tmp_path, price):
    runner, _, portfolio = build(tmp_path, BuyOnceStrategy())
    _live(runner)
    runner.adapter = ExchangeAdapter(_ReconcileClient({'BTC': 0.4}, price=price))
    portfolio.on_fill('BTC/USD', Side.BUY, 100, 0.4, NOW, stop=90)
    with pytest.raises(AccountStateUnavailable, match='Invalid startup'):
        await runner.reconcile_positions(NOW)
    assert portfolio.get('BTC/USD').units == 0.4


async def test_reconciliation_validates_all_symbols_before_any_mutation(tmp_path):
    runner, _, portfolio = build(tmp_path, BuyOnceStrategy())
    _live(runner)
    runner.cfg.exchange.symbols_allowlist = ['BTC/USD', 'ETH/USD']
    runner.adapter = ExchangeAdapter(_ReconcileClient({'BTC': 0.5, 'ETH': 1}))
    runner.adapter.fetch_ticker = AsyncMock(side_effect=[
        Ticker('BTC/USD', 100, 100, 100, 1, 1, 0), RuntimeError('fake outage')])
    with pytest.raises(AccountStateUnavailable, match='ticker unavailable'):
        await runner.reconcile_positions(NOW)
    assert not portfolio.positions


def test_corrupt_live_state_refuses_to_load(tmp_path):
    path = tmp_path / 'positions.json'
    path.write_text('{broken')
    with pytest.raises(ValueError, match='recovery required'):
        JsonPositionStore(path, strict=True).load()
    assert path.read_text() == '{broken'

"""Config loading + validation tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from tradingbot.config import Config, load_config, load_settings


def test_defaults_are_conservative() -> None:
    c = Config()
    assert c.app.dry_run is True
    assert c.exchange.sandbox is True
    assert c.fees.maker_first is True
    assert c.fees.allow_taker_fallback is False
    assert c.telegram.approval_threshold_quote == 0.0  # every trade needs approval
    assert c.regime.enabled is False
    assert 1.0 <= c.risk.per_trade_risk_pct <= 2.0


def test_missing_file_yields_defaults(tmp_path: Path) -> None:
    c = load_config(tmp_path / "does-not-exist.yaml")
    assert c.exchange.name == "coinbase"


def test_partial_yaml_overrides_only_given_fields(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(
        textwrap.dedent(
            """
            exchange:
              name: kraken
              sandbox: false
            risk:
              floor_quote: 5000
            """
        )
    )
    c = load_config(p)
    assert c.exchange.name == "kraken"
    assert c.exchange.sandbox is False
    assert c.risk.floor_quote == 5000
    # untouched fields keep defaults
    assert c.app.dry_run is True
    assert c.risk.max_trades_per_day == 20


def test_rejects_min_notional_above_max(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(
        textwrap.dedent(
            """
            risk:
              min_notional_per_trade_quote: 100
              max_notional_per_trade_quote: 50
            """
        )
    )
    with pytest.raises(ValidationError):
        load_config(p)


def test_rejects_out_of_range_percent(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("risk:\n  per_trade_risk_pct: 150\n")
    with pytest.raises(ValidationError):
        load_config(p)


def test_rejects_negative_floor(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("risk:\n  floor_quote: -1\n")
    with pytest.raises(ValidationError):
        load_config(p)


def test_regime_multiplier_order_validated(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("regime:\n  min_risk_multiplier: 0.9\n  max_risk_multiplier: 0.2\n")
    with pytest.raises(ValidationError):
        load_config(p)


def test_repo_config_yaml_is_valid() -> None:
    """The shipped config.yaml must load and validate."""
    repo_config = Path(__file__).resolve().parents[1] / "config.yaml"
    c = load_config(repo_config)
    assert c.exchange.name == "coinbase"


def test_secrets_redacted_and_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXCHANGE_API_KEY", "key-123")
    monkeypatch.setenv("EXCHANGE_API_SECRET", "secret-xyz")
    settings = load_settings("does-not-exist.yaml", env_file=None)
    assert settings.secrets.has_exchange_credentials is True
    # SecretStr never reveals the value in repr/str
    assert "secret-xyz" not in repr(settings.secrets)
    assert settings.secrets.exchange_api_secret.get_secret_value() == "secret-xyz"

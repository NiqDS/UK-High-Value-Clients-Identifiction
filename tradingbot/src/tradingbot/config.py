"""Configuration loading and validation.

Secrets come ONLY from the environment / ``.env`` (see :class:`Secrets`).
All non-secret tunables come from ``config.yaml`` (see :class:`Config`).
Every value is validated on load; nonsensical configs are rejected early so
the bot never starts in an unsafe state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Secrets (environment / .env only)
# ---------------------------------------------------------------------------
class Secrets(BaseSettings):
    """API credentials and tokens. Loaded from environment variables / ``.env``.

    Stored as :class:`~pydantic.SecretStr` so they are auto-redacted in logs
    and ``repr()``. Call ``.get_secret_value()`` only at the point of use.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    exchange_api_key: SecretStr = SecretStr("")
    exchange_api_secret: SecretStr = SecretStr("")
    exchange_api_password: SecretStr = SecretStr("")
    telegram_bot_token: SecretStr = SecretStr("")
    smtp_username: SecretStr = SecretStr("")
    smtp_password: SecretStr = SecretStr("")

    @property
    def has_exchange_credentials(self) -> bool:
        return bool(self.exchange_api_key.get_secret_value()) and bool(
            self.exchange_api_secret.get_secret_value()
        )


# ---------------------------------------------------------------------------
# Non-secret config sections (config.yaml)
# ---------------------------------------------------------------------------
def _pct(default: float, **kw: Any) -> Any:
    return Field(default=default, ge=0.0, le=100.0, **kw)


class ExchangeConfig(BaseModel):
    name: str = "coinbase"
    sandbox: bool = True
    quote_currency: str = "USD"
    symbols_allowlist: list[str] = Field(default_factory=lambda: ["BTC/USD", "ETH/USD"])
    enable_rate_limit: bool = True
    request_timeout_ms: int = Field(default=20000, gt=0)


class RiskConfig(BaseModel):
    floor_quote: float = Field(default=1000.0, ge=0.0)
    floor_buffer_quote: float = Field(default=50.0, ge=0.0)
    min_notional_per_trade_quote: float = Field(default=10.0, ge=0.0)
    max_notional_per_trade_quote: float = Field(default=50.0, gt=0.0)
    max_notional_per_trade_pct_equity: float = _pct(2.0)
    per_trade_risk_pct: float = _pct(1.0)
    max_open_positions: int = Field(default=2, ge=0)
    max_concurrent_open_orders: int = Field(default=4, ge=0)
    max_trades_per_day: int = Field(default=20, ge=0)
    max_daily_traded_notional_quote: float = Field(default=500.0, ge=0.0)
    max_daily_loss_quote: float = Field(default=50.0, ge=0.0)
    daily_reset_utc_hour: int = Field(default=0, ge=0, le=23)

    @model_validator(mode="after")
    def _check_consistency(self) -> "RiskConfig":
        if self.min_notional_per_trade_quote > self.max_notional_per_trade_quote:
            raise ValueError(
                "risk.min_notional_per_trade_quote must be <= max_notional_per_trade_quote"
            )
        return self


class FeeConfig(BaseModel):
    maker_fee_pct: float = _pct(0.40)
    taker_fee_pct: float = _pct(0.60)
    pull_fee_tiers_from_exchange: bool = True
    round_trip_fee_safety_margin_pct: float = _pct(0.20)
    maker_first: bool = True
    allow_taker_fallback: bool = False
    maker_offset_pct: float = _pct(0.05)
    max_spread_pct: float = _pct(0.30)
    max_slippage_pct: float = _pct(0.20)


class EventConfig(BaseModel):
    enabled: bool = True
    timezone: str = "UTC"
    pre_event_window_minutes: int = Field(default=60, ge=0)
    post_event_window_minutes: int = Field(default=30, ge=0)
    pause_entries: bool = True
    reduce_size_pct: float = _pct(50.0)
    widen_maker_offset_pct: float = _pct(0.10)
    tighten_stops: bool = True
    calendar: list[dict[str, Any]] = Field(default_factory=list)
    calendar_csv: str = ""           # optional CSV feed: name,timestamp_utc
    include_halvings: bool = True    # add BTC halving dates to the event calendar


class KillSwitchConfig(BaseModel):
    enabled: bool = True
    rolling_window_minutes: int = Field(default=30, gt=0)
    price_velocity_sigma: float = Field(default=4.0, gt=0.0)
    volume_spike_sigma: float = Field(default=4.0, gt=0.0)
    cooldown_minutes: int = Field(default=60, ge=0)
    pull_resting_orders: bool = True


class RegimeConfig(BaseModel):
    enabled: bool = False
    cadence_hours: int = Field(default=24, gt=0)
    min_risk_multiplier: float = Field(default=0.25, ge=0.0, le=1.0)
    max_risk_multiplier: float = Field(default=1.0, ge=0.0, le=1.0)
    sma_weeks: int = Field(default=200, gt=0)           # the "generational" long SMA
    cycle_months: float = Field(default=48.0, gt=0.0)   # halving cycle length

    @model_validator(mode="after")
    def _check_range(self) -> "RegimeConfig":
        if self.min_risk_multiplier > self.max_risk_multiplier:
            raise ValueError("regime.min_risk_multiplier must be <= max_risk_multiplier")
        return self


class StrategyConfig(BaseModel):
    name: str = "sma_crossover"
    timeframe: str = "1m"
    ohlcv_limit: int = Field(default=200, gt=0)
    fast_period: int = Field(default=10, gt=0)
    slow_period: int = Field(default=30, gt=0)
    take_profit_pct: float = _pct(1.5)
    stop_loss_pct: float = _pct(1.0)
    target_notional_quote: float = Field(default=40.0, gt=0.0)
    # Valuation filter: weighted-average-cost (VWAP) over/under-valuation.
    vwap_filter_enabled: bool = True
    vwap_window: int = Field(default=50, gt=0)
    # Buy only when price is at/below this % vs VWAP (the undervaluation floor;
    # 0 = buy only at/below the weighted average cost). Lower = stricter.
    buy_valuation_floor_pct: float = Field(default=0.0)
    # Force-exit an open position (+ emergency alert) when its price rises this
    # far above VWAP (the overvaluation ceiling).
    force_exit_overvaluation_pct: float = Field(default=3.0, gt=0.0)

    # --- fee-drag controls: fewer, higher-conviction trades ---------------
    trade_cooldown_bars: int = Field(default=0, ge=0)          # min bars between entries
    min_crossover_strength_pct: float = Field(default=0.0, ge=0.0)  # require (fast-slow)/slow >= this

    # --- active VWAP position sizing --------------------------------------
    sizing_mode: Literal["fixed", "vwap_scaled"] = "fixed"
    vwap_size_min_mult: float = Field(default=0.5, ge=0.0)
    vwap_size_max_mult: float = Field(default=1.5, ge=0.0)
    vwap_full_size_discount_pct: float = Field(default=2.0, gt=0.0)  # discount giving max size

    # --- adaptive (ATR) exits ---------------------------------------------
    exit_mode: Literal["pct", "atr"] = "pct"
    atr_period: int = Field(default=14, gt=0)
    atr_tp_mult: float = Field(default=2.0, gt=0.0)
    atr_sl_mult: float = Field(default=1.5, gt=0.0)
    trailing_enabled: bool = False
    trailing_atr_mult: float = Field(default=2.0, gt=0.0)

    # --- trend/regime filter (only long with the higher-timeframe trend) ---
    trend_filter_enabled: bool = False
    trend_period: int = Field(default=100, gt=0)

    # --- volatility / mean-reversion thesis (vwap_reversion strategy) ------
    reversion_entry_pct: float = Field(default=1.0, gt=0.0)  # buy when this % below VWAP

    @model_validator(mode="after")
    def _check_sizing(self) -> "StrategyConfig":
        if self.vwap_size_min_mult > self.vwap_size_max_mult:
            raise ValueError("strategy.vwap_size_min_mult must be <= vwap_size_max_mult")
        return self

    @model_validator(mode="after")
    def _check_periods(self) -> "StrategyConfig":
        if self.fast_period >= self.slow_period:
            raise ValueError("strategy.fast_period must be < slow_period")
        return self


class FundingConfig(BaseModel):
    """Perp funding-rate positioning overlay (contrarian: fade crowded leverage)."""

    enabled: bool = False
    z_window: int = Field(default=30, gt=1)         # funding periods for the z-score
    extreme_z: float = Field(default=1.5, gt=0.0)   # |z| beyond this = crowded positioning
    min_mult: float = Field(default=0.25, ge=0.0, le=1.0)
    max_mult: float = Field(default=1.0, ge=0.0, le=1.0)
    gate_longs_when_crowded: bool = False           # hard-skip longs when funding is extreme

    @model_validator(mode="after")
    def _check_bounds(self) -> "FundingConfig":
        if self.min_mult > self.max_mult:
            raise ValueError("funding.min_mult must be <= max_mult")
        return self


class TelegramConfig(BaseModel):
    enabled: bool = True
    approval_threshold_quote: float = Field(default=0.0, ge=0.0)
    approval_timeout_seconds: int = Field(default=300, gt=0)
    allowed_chat_ids: list[int] = Field(default_factory=list)


class ReportingConfig(BaseModel):
    weekly_enabled: bool = True
    weekly_day: int = Field(default=0, ge=0, le=6)  # 0=Mon .. 6=Sun
    weekly_hour_utc: int = Field(default=8, ge=0, le=23)
    email_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(default=587, gt=0)
    smtp_use_tls: bool = True
    email_from: str = ""
    email_to: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    dry_run: bool = True
    trading_enabled: bool = True
    log_level: str = "INFO"
    log_json: bool = False
    db_url: str = "sqlite:///data/tradingbot.db"
    heartbeat_interval_seconds: int = Field(default=15, gt=0)
    max_api_latency_ms: int = Field(default=2000, gt=0)
    max_consecutive_failures: int = Field(default=3, gt=0)
    health_recovery_samples: int = Field(default=3, gt=0)
    cancel_orders_on_suspend: bool = True


class Config(BaseModel):
    """Top-level non-secret config. Every section has conservative defaults,
    so an empty / partial ``config.yaml`` still yields a safe configuration."""

    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    fees: FeeConfig = Field(default_factory=FeeConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    events: EventConfig = Field(default_factory=EventConfig)
    kill_switch: KillSwitchConfig = Field(default_factory=KillSwitchConfig)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)
    funding: FundingConfig = Field(default_factory=FundingConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)
    app: AppConfig = Field(default_factory=AppConfig)

    @model_validator(mode="after")
    def _cross_section_checks(self) -> "Config":
        if self.exchange.quote_currency.upper() != self.exchange.quote_currency:
            # normalise rather than reject
            object.__setattr__(
                self.exchange, "quote_currency", self.exchange.quote_currency.upper()
            )
        return self


class Settings(BaseModel):
    """Everything the app needs: validated config + secrets."""

    config: Config
    secrets: Secrets

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def load_config(path: str | Path) -> Config:
    """Load and validate ``config.yaml``. A missing file yields all-defaults."""
    p = Path(path)
    if not p.exists():
        return Config()
    raw = yaml.safe_load(p.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{p}: top-level YAML must be a mapping, got {type(raw).__name__}")
    return Config(**raw)


def load_settings(
    config_path: str | Path = "config.yaml",
    env_file: str | Path | None = ".env",
) -> Settings:
    """Load validated config + secrets together."""
    config = load_config(config_path)
    secrets = Secrets(_env_file=env_file) if env_file else Secrets()  # type: ignore[call-arg]
    return Settings(config=config, secrets=secrets)

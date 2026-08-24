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
    use_threaded_dns: bool = True   # use the OS resolver, not aiodns (avoids the
                                    # "Could not contact DNS servers" failure on macOS)
    options: dict[str, Any] = Field(default_factory=dict)  # extra ccxt options passed
                                    # straight through (e.g. {"defaultType": "spot"} for
                                    # Bybit/OKX so orders route to spot, not derivatives)


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
    # Trend strategies ride the move and have no fixed take-profit (their exit is a
    # channel/stop break), so the TP-based fee proof cannot apply. Keep this True
    # for take-profit strategies (SMA/reversion); set False to let no-TP trend
    # entries through the fee gate (the captured channel move dwarfs the fee).
    require_take_profit: bool = True


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
    rolling_window_minutes: int = Field(default=30, gt=0)  # bars kept per symbol
    # Ratio thresholds vs the MEDIAN of the recent window (median is robust to
    # heavy tails, unlike a z-score/sigma which blows up on spiky volume). A
    # trigger reads e.g. "volume 11.4x median" — interpretable, not a fake 51σ.
    price_velocity_ratio: float = Field(default=5.0, gt=0.0)  # |latest move| >= N x median move
    volume_spike_ratio: float = Field(default=8.0, gt=0.0)    # latest volume >= N x median volume
    cooldown_minutes: int = Field(default=60, ge=0)
    pull_resting_orders: bool = True


class LearningConfig(BaseModel):
    """Weekly evidence-driven learning loop. Reads the week's own trades (from the
    DB) plus any logs dropped in ``bad_trades_dir`` (own losing trades the bot
    writes there, and external bots' logs you upload), assesses them, and emits
    CANDIDATE parameter adjustments — never auto-applied (backtest first)."""

    enabled: bool = True
    bad_trades_dir: str = "bad_trades"
    weekly: bool = True                              # run on the weekly report cadence
    min_trades_per_bucket: int = Field(default=5, ge=1)  # below this = 'noise, not signal'
    write_own_losers: bool = True                    # append own losing exits to the folder


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
    # Live/backtest parity: when true, signals are evaluated against the last
    # CLOSED bar's close (the runner also drops the still-forming candle), not
    # the live intraday tick — matching how the strategy was validated in
    # backtests. Recommended true for live daily-bar trend trading.
    signal_on_closed_bar: bool = False
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

    # --- trend-following thesis (donchian_breakout strategy) ---------------
    # BTC trends, so this rides breakouts instead of fading them: go long when
    # close breaks above the prior N-bar high; exit when it breaks the M-bar low.
    donchian_entry_period: int = Field(default=20, gt=1)
    donchian_exit_period: int = Field(default=10, gt=1)

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


class MvrvConfig(BaseModel):
    """On-chain MVRV Z-score valuation overlay (slow; multi-year cycle signal).

    Low Z (historically <0) = undervalued/accumulation => favorable; high Z
    (historically >7) = euphoria/overvalued => scale down / gate. Scales size,
    never direction. NOTE: this is a *cycle* metric — evaluate it on multi-year
    DAILY data; a one-year hourly backtest barely moves it.
    """

    enabled: bool = False
    low_z: float = 0.0      # at/below this Z => fully favorable
    high_z: float = 7.0     # at/above this Z => fully unfavorable
    min_mult: float = Field(default=0.25, ge=0.0, le=1.0)
    max_mult: float = Field(default=1.0, ge=0.0, le=1.0)
    gate_when_rich: bool = False  # hard-skip new longs when Z >= high_z

    @model_validator(mode="after")
    def _check(self) -> "MvrvConfig":
        if self.low_z >= self.high_z:
            raise ValueError("mvrv.low_z must be < high_z")
        if self.min_mult > self.max_mult:
            raise ValueError("mvrv.min_mult must be <= max_mult")
        return self


class TelegramConfig(BaseModel):
    enabled: bool = True
    approval_threshold_quote: float = Field(default=0.0, ge=0.0)
    approval_timeout_seconds: int = Field(default=300, gt=0)
    allowed_chat_ids: list[int] = Field(default_factory=list)
    # require_approval False => entries auto-approve (the validated systematic
    # posture); Telegram stays wired for alerts + /status + /pause.
    require_approval: bool = True
    # trade_alerts True => a Telegram message on every fill (buy + sell) with
    # time, amount/PnL, a 🟢/🔴 header on sells, and the DB entry number.
    trade_alerts: bool = True
    # headline tag on every alert (e.g. "DAILY" / "4h") so multiple buckets are
    # distinguishable in one Telegram chat.
    label: str = ""
    # commands_enabled False => alerts-only: the bot sends messages but does NOT
    # poll for /commands. Set False on a SECOND bucket sharing one bot token, so
    # only ONE service polls getUpdates (Telegram allows just one poller/token).
    commands_enabled: bool = True


class ReportingConfig(BaseModel):
    weekly_enabled: bool = True
    weekly_day: int = Field(default=0, ge=0, le=6)  # 0=Mon .. 6=Sun
    weekly_hour_utc: int = Field(default=8, ge=0, le=23)
    # Monthly deep-review (bad trades + missed opportunities) on day-of-month.
    monthly_enabled: bool = True
    monthly_day: int = Field(default=1, ge=1, le=28)   # 1..28 (safe for all months)
    monthly_hour_utc: int = Field(default=8, ge=0, le=23)
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
    # Persist logs to a rotating file (in addition to stdout) so an unattended
    # run leaves a durable, reviewable trail. None/"" = stdout only.
    log_file: str | None = None
    log_file_max_bytes: int = Field(default=5_000_000, gt=0)  # ~5 MB per file
    log_file_backups: int = Field(default=5, ge=0)            # keep 5 rotations
    db_url: str = "sqlite:///data/tradingbot.db"
    heartbeat_interval_seconds: int = Field(default=15, gt=0)
    max_api_latency_ms: int = Field(default=2000, gt=0)
    max_consecutive_failures: int = Field(default=3, gt=0)
    health_recovery_samples: int = Field(default=3, gt=0)
    cancel_orders_on_suspend: bool = True
    # Paper sizing: when there are no exchange credentials (so no real balance to
    # read), size the paper run off this simulated equity instead of the synthetic
    # floor*3 fallback. Set it to your intended real capital (e.g. 500) so paper
    # sleeve sizes match what you'll trade live. 0 = use the floor*3 fallback.
    paper_equity: float = Field(default=0.0, ge=0.0)


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
    mvrv: MvrvConfig = Field(default_factory=MvrvConfig)
    learning: LearningConfig = Field(default_factory=LearningConfig)
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

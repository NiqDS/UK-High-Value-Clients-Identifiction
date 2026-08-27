"""Runtime controls + settings, adjustable from inside the Telegram bot.

Changes mutate the live :class:`Config` (the running engine holds a reference,
so they take effect immediately) and are persisted via an optional JSON
override file so a restart keeps them — and, critically, so a manual pause does
not silently resume on restart.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import Config
from ..events.kill_switch import KillSwitch
from ..risk.state import RiskStateStore

logger = logging.getLogger(__name__)


def is_authorized(chat_id: int, allowlist: list[int]) -> bool:
    """Most conservative: an empty allowlist authorises no one."""
    return chat_id in allowlist


class JsonOverrideStore:
    """Persists runtime setting overrides to a small JSON file and re-applies
    them onto a Config at startup."""

    def __init__(self, path: str | Path = "runtime_overrides.json") -> None:
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except Exception:
            logger.warning("Could not read %s; ignoring overrides", self.path)
            return {}

    def save(self, overrides: dict) -> None:
        self.path.write_text(json.dumps(overrides, indent=2, sort_keys=True))

    def apply(self, config: Config) -> None:
        ov = self.load()
        tg = ov.get("telegram", {})
        risk = ov.get("risk", {})
        strat = ov.get("strategy", {})
        app = ov.get("app", {})
        if "approval_threshold_quote" in tg:
            config.telegram.approval_threshold_quote = tg["approval_threshold_quote"]
        for key in ("max_notional_per_trade_quote", "max_trades_per_day", "max_daily_loss_quote"):
            if key in risk:
                setattr(config.risk, key, risk[key])
        for key in ("buy_valuation_floor_pct", "force_exit_overvaluation_pct"):
            if key in strat:
                setattr(config.strategy, key, strat[key])
        if "trading_enabled" in app:
            config.app.trading_enabled = app["trading_enabled"]


class SettingsController:
    """Validated runtime mutations. Each setter persists via the override store."""

    def __init__(
        self,
        config: Config,
        store: RiskStateStore,
        kill_switch: KillSwitch | None = None,
        overrides: JsonOverrideStore | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.kill_switch = kill_switch
        self.overrides = overrides

    # -- persistence helper -------------------------------------------------
    def _persist(self) -> None:
        if self.overrides is None:
            return
        self.overrides.save(
            {
                "telegram": {
                    "approval_threshold_quote": self.config.telegram.approval_threshold_quote
                },
                "risk": {
                    "max_notional_per_trade_quote": self.config.risk.max_notional_per_trade_quote,
                    "max_trades_per_day": self.config.risk.max_trades_per_day,
                    "max_daily_loss_quote": self.config.risk.max_daily_loss_quote,
                },
                "strategy": {
                    "buy_valuation_floor_pct": self.config.strategy.buy_valuation_floor_pct,
                    "force_exit_overvaluation_pct": self.config.strategy.force_exit_overvaluation_pct,
                },
                "app": {"trading_enabled": self.store.is_trading_enabled()},
            }
        )

    # -- limit setters (validated) -----------------------------------------
    def set_approval_threshold(self, value: float) -> float:
        if value < 0:
            raise ValueError("approval threshold must be >= 0")
        self.config.telegram.approval_threshold_quote = float(value)
        self._persist()
        return value

    def set_max_notional(self, value: float) -> float:
        if value <= 0:
            raise ValueError("max notional must be > 0")
        if value < self.config.risk.min_notional_per_trade_quote:
            raise ValueError("max notional must be >= min notional")
        self.config.risk.max_notional_per_trade_quote = float(value)
        self._persist()
        return value

    def set_max_trades_per_day(self, value: int) -> int:
        if value < 0:
            raise ValueError("max trades/day must be >= 0")
        self.config.risk.max_trades_per_day = int(value)
        self._persist()
        return value

    def set_max_daily_loss(self, value: float) -> float:
        if value < 0:
            raise ValueError("max daily loss must be >= 0")
        self.config.risk.max_daily_loss_quote = float(value)
        self._persist()
        return value

    def set_buy_valuation_floor(self, value: float) -> float:
        """Buy only when price is at/below this % vs VWAP (undervaluation floor)."""
        self.config.strategy.buy_valuation_floor_pct = float(value)
        self._persist()
        return value

    def set_force_exit_overvaluation(self, value: float) -> float:
        """Force-exit ceiling: sell when price runs this % above VWAP."""
        if value <= 0:
            raise ValueError("force-exit overvaluation must be > 0")
        self.config.strategy.force_exit_overvaluation_pct = float(value)
        self._persist()
        return value

    # -- kill switch / trading flag ----------------------------------------
    def pause_trading(self, reason: str = "manual pause via Telegram") -> None:
        self.store.set_trading_enabled(False, reason)
        if self.kill_switch is not None:
            self.kill_switch.manual_pause()
        self._persist()

    def resume_trading(self) -> None:
        self.store.set_trading_enabled(True)
        if self.kill_switch is not None:
            self.kill_switch.manual_resume()
        self._persist()

    def current_settings(self) -> dict:
        return {
            "approval_threshold_quote": self.config.telegram.approval_threshold_quote,
            "max_notional_per_trade_quote": self.config.risk.max_notional_per_trade_quote,
            "max_trades_per_day": self.config.risk.max_trades_per_day,
            "max_daily_loss_quote": self.config.risk.max_daily_loss_quote,
            "buy_valuation_floor_pct": self.config.strategy.buy_valuation_floor_pct,
            "force_exit_overvaluation_pct": self.config.strategy.force_exit_overvaluation_pct,
            "trading_enabled": self.store.is_trading_enabled(),
        }

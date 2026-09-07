"""Event-proximity: bucket trades near vs away from macro events."""

from __future__ import annotations

from datetime import datetime, timezone

from tradingbot.analysis.event_proximity import render_event_proximity_report
from tradingbot.backtest.engine import BacktestConfig, Trade
from tradingbot.config import StrategyConfig
from tradingbot.events.calendar import ScheduledEvent, load_calendar_csv

DAY = 86_400_000


def _ev(day_iso: str) -> ScheduledEvent:
    return ScheduledEvent(name="FOMC",
                          timestamp=datetime.fromisoformat(day_iso).replace(tzinfo=timezone.utc))


def _trade(entry_iso: str, pnl: float) -> tuple[str, Trade]:
    ts = int(datetime.fromisoformat(entry_iso).replace(tzinfo=timezone.utc).timestamp() * 1000)
    t = Trade(entry_ts=ts, exit_ts=ts + 5 * DAY, entry_price=100.0, exit_price=100.0 + pnl,
              units=1.0, gross_pnl=pnl, fees=0.0, net_pnl=pnl, reason="signal exit")
    return ("BTC", t)


def _cfg():
    return StrategyConfig(donchian_entry_period=50, donchian_exit_period=15)


def test_near_vs_away_bucketing() -> None:
    events = [_ev("2024-03-20")]  # one event
    trades = [
        _trade("2024-03-19", -5.0),   # 1 day before -> NEAR
        _trade("2024-03-21", -4.0),   # 1 day after  -> NEAR
        _trade("2024-06-01", +8.0),   # far          -> AWAY
        _trade("2024-09-01", +6.0),   # far          -> AWAY
    ]
    report = render_event_proximity_report(trades, events, window_days=3, base=_cfg(),
                                           bt=BacktestConfig(), label="test")
    # two near, two away
    assert "NEAR event       |      2 |" in report
    assert "AWAY from events |      2 |" in report
    assert "Counterfactual" in report and "Verdict" in report


def test_no_effect_verdict_when_near_not_worse() -> None:
    events = [_ev("2024-03-20")]
    # near trades are FINE (positive), away also positive -> "No event effect"
    trades = [_trade("2024-03-20", +7.0), _trade("2024-03-19", +6.0),
              _trade("2024-06-01", +6.5), _trade("2024-07-01", +7.5)]
    report = render_event_proximity_report(trades, events, window_days=3, base=_cfg(),
                                           bt=BacktestConfig(), label="test", min_trades=1)
    assert "No event effect" in report


def test_shipped_calendar_parses() -> None:
    events = load_calendar_csv("calendars/macro_events.csv")
    assert len(events) >= 40
    assert all(e.name == "FOMC" for e in events)
    # spans the backtest window
    years = {e.timestamp.year for e in events}
    assert {2020, 2022, 2024, 2026} <= years

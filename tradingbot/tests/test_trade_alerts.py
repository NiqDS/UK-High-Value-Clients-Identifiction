"""Per-fill Telegram trade alerts (format + delivery) and monthly review."""

from __future__ import annotations

from datetime import datetime, timezone

from tradingbot.app.runner import next_monthly_time
from tradingbot.approval.messages import format_trade_alert

TS = 1_755_700_800_000  # a fixed ms timestamp


def test_buy_alert_has_time_amount_and_db_id() -> None:
    a = {"symbol": "BTC/USDT", "side": "buy", "is_entry": True, "price": 63780.1,
         "amount": 0.00036, "cost_quote": 23.0, "fee_quote": 0.02, "net": None,
         "trade_id": 142, "ts": TS}
    msg = format_trade_alert(a, "USDT")
    assert "🔵" in msg and "BUY" in msg and "BTC/USDT" in msg
    assert "spent 23.00 USDT" in msg
    assert "UTC" in msg                        # time present
    assert "DB entry #142" in msg              # audit id present


def test_sell_profit_is_green_with_net_and_id() -> None:
    a = {"symbol": "BTC/USDT", "side": "sell", "is_entry": False, "price": 67700.0,
         "amount": 0.00036, "cost_quote": 24.4, "fee_quote": 0.02, "realized": 1.45,
         "net": 1.43, "entry_price": 63780.1, "trade_id": 143, "ts": TS}
    msg = format_trade_alert(a, "USDT")
    assert msg.startswith("🟢")               # green circle in header
    assert "+1.43 USDT" in msg                 # net gain
    assert "DB entry #143" in msg


def test_sell_loss_is_red() -> None:
    a = {"symbol": "ADA/USDT", "side": "sell", "is_entry": False, "price": 0.1712,
         "amount": 134.0, "cost_quote": 22.9, "fee_quote": 0.02, "realized": -0.30,
         "net": -0.32, "entry_price": 0.1856, "trade_id": 144, "ts": TS}
    msg = format_trade_alert(a, "USDT")
    assert msg.startswith("🔴")               # red circle for a loss
    assert "-0.32 USDT" in msg


def test_next_monthly_time_rolls_forward() -> None:
    # mid-month, day 1 already passed -> next month's day 1
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    nxt = next_monthly_time(now, day=1, hour_utc=8)
    assert (nxt.year, nxt.month, nxt.day, nxt.hour) == (2026, 9, 1, 8)
    # December rolls to January of next year
    dec = datetime(2026, 12, 20, 12, 0, tzinfo=timezone.utc)
    assert next_monthly_time(dec, 1, 8).year == 2027

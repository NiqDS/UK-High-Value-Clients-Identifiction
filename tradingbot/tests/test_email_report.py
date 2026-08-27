"""Email sender (mocked SMTP) + weekly report scheduling helper."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingbot.app.runner import next_report_time
from tradingbot.config import ReportingConfig, Secrets
from tradingbot.reporting.email_sender import EmailSender, build_email_sender


class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None) -> None:
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in = None
        self.sent = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, msg):
        self.sent = msg


def test_email_sender_builds_and_sends(monkeypatch) -> None:
    FakeSMTP.instances.clear()
    monkeypatch.setattr("tradingbot.reporting.email_sender.smtplib.SMTP", FakeSMTP)
    sender = EmailSender("smtp.gmail.com", 587, True, "bot@example.com",
                         ["me@example.com"], username="bot@example.com", password="app-pw")
    sender.send("subject here", "body text")
    smtp = FakeSMTP.instances[-1]
    assert smtp.started_tls is True
    assert smtp.logged_in == ("bot@example.com", "app-pw")
    assert smtp.sent["To"] == "me@example.com"
    assert smtp.sent["Subject"] == "subject here"
    assert "body text" in smtp.sent.get_content()


def test_build_email_sender_disabled_returns_none() -> None:
    cfg = ReportingConfig(email_enabled=False)
    assert build_email_sender(cfg, Secrets(_env_file=None)) is None


def test_build_email_sender_missing_recipients_returns_none() -> None:
    cfg = ReportingConfig(email_enabled=True, email_from="a@b.com", email_to=[])
    assert build_email_sender(cfg, Secrets(_env_file=None)) is None


def test_build_email_sender_configured() -> None:
    cfg = ReportingConfig(email_enabled=True, email_from="a@b.com", email_to=["c@d.com"])
    sender = build_email_sender(cfg, Secrets(_env_file=None))
    assert isinstance(sender, EmailSender)
    assert sender.recipients == ["c@d.com"]


# --- scheduling ------------------------------------------------------------
def test_next_report_time_same_week_future_hour() -> None:
    # Mon 2026-06-15 06:00 UTC -> next Monday-08:00 is the same day
    now = datetime(2026, 6, 15, 6, 0, tzinfo=timezone.utc)  # Monday
    nxt = next_report_time(now, weekday=0, hour_utc=8)
    assert nxt == datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)


def test_next_report_time_rolls_to_next_week_when_passed() -> None:
    now = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)  # Monday, past 08:00
    nxt = next_report_time(now, weekday=0, hour_utc=8)
    assert nxt == datetime(2026, 6, 22, 8, 0, tzinfo=timezone.utc)  # next Monday


def test_next_report_time_future_weekday() -> None:
    now = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)  # Monday
    nxt = next_report_time(now, weekday=4, hour_utc=8)  # Friday
    assert nxt == datetime(2026, 6, 19, 8, 0, tzinfo=timezone.utc)


async def test_runner_emits_email_on_report(tmp_path) -> None:
    # end-to-end: emit_weekly_report should call the email sender
    from tradingbot.config import Config, Settings
    from tradingbot.app.runner import TradingRunner
    from tradingbot.reporting.weekly import WeeklyReporter
    from tradingbot.store import TradeLog, make_session_factory

    sf = make_session_factory(f"sqlite:///{tmp_path / 'tb.db'}")
    sent = {}

    class Sender:
        def send(self, subject, body):
            sent["subject"], sent["body"] = subject, body

    settings = Settings(config=Config(), secrets=Secrets(_env_file=None))
    runner = TradingRunner(
        settings=settings, adapter=None, pipeline=None, strategy=None, store=None,
        trade_log=TradeLog(sf), portfolio=None,
        reporter=WeeklyReporter(TradeLog(sf)), email_sender=Sender(),
    )
    text = await runner.emit_weekly_report(now=datetime(2026, 6, 15, tzinfo=timezone.utc))
    assert "Weekly performance report" in text
    assert "weekly report" in sent["subject"]
    assert sent["body"] == text

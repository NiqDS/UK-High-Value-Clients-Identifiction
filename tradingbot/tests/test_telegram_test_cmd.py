"""telegram-test command: sends a verification message to the allowlist."""

from __future__ import annotations

from argparse import Namespace

from tradingbot import __main__ as m
from tradingbot.config import Config, Secrets, Settings


class _FakeBot:
    sent: list = []
    updates: list = []

    def __init__(self, token):
        self.token = token

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send_message(self, chat_id, text):
        _FakeBot.sent.append((chat_id, text))

    async def get_updates(self, timeout=0):
        return _FakeBot.updates


def _settings(token: str, chat_ids: list) -> Settings:
    cfg = Config(telegram={"enabled": True, "allowed_chat_ids": chat_ids})
    return Settings(config=cfg, secrets=Secrets(_env_file=None, telegram_bot_token=token))


def test_sends_to_each_allowlisted_chat(monkeypatch) -> None:
    _FakeBot.sent = []
    monkeypatch.setattr("telegram.Bot", _FakeBot)
    rc = m._telegram_test(_settings("tok", [111, 222]),
                          Namespace(message="ping"))
    assert rc == 0
    assert _FakeBot.sent == [(111, "ping"), (222, "ping")]


def test_chat_id_flag_overrides_config(monkeypatch) -> None:
    _FakeBot.sent = []
    monkeypatch.setattr("telegram.Bot", _FakeBot)
    # config allowlist is EMPTY, but --chat-id supplies the target
    rc = m._telegram_test(_settings("tok", []),
                          Namespace(message="ping", chat_id=999))
    assert rc == 0
    assert _FakeBot.sent == [(999, "ping")]


def test_get_chat_ids_prints_the_id(monkeypatch, caplog) -> None:
    import logging
    from types import SimpleNamespace

    chat = SimpleNamespace(id=555111, first_name="Nick", username="nick")
    _FakeBot.updates = [SimpleNamespace(message=SimpleNamespace(chat=chat), edited_message=None)]
    monkeypatch.setattr("telegram.Bot", _FakeBot)
    caplog.set_level(logging.INFO)
    rc = m._telegram_test(_settings("tok", []), Namespace(get_chat_ids=True))
    assert rc == 0
    assert "555111" in caplog.text


def test_errors_without_token() -> None:
    rc = m._telegram_test(_settings("", [111]), Namespace(message=None))
    assert rc == 2


def test_errors_without_chat_ids() -> None:
    rc = m._telegram_test(_settings("tok", []), Namespace(message=None))
    assert rc == 2

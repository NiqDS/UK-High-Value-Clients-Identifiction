"""The /report Telegram command — delivers the db-stats summary to the chat."""

from __future__ import annotations

from tradingbot.approval.telegram_bot import TelegramApprovalBot
from tradingbot.config import Config


class _Msg:
    def __init__(self):
        self.sent: list = []

    async def reply_text(self, text, parse_mode=None):
        self.sent.append((text, parse_mode))


class _Update:
    def __init__(self, chat_id: int, msg: _Msg):
        self.effective_chat = type("C", (), {"id": chat_id})()
        self.message = msg


def _bot(report_provider=None) -> TelegramApprovalBot:
    cfg = Config(telegram={"enabled": True, "allowed_chat_ids": [42]})
    return TelegramApprovalBot("tok", cfg, manager=None, settings=None, status=None,
                               report_provider=report_provider)


async def test_report_sends_stats_wrapped_in_code_block() -> None:
    async def provider():
        return "# DB stats\nfills: 5  (3 entries, 2 exits)"
    bot = _bot(provider)
    msg = _Msg()
    await bot._on_report(_Update(42, msg), None)
    assert len(msg.sent) == 1
    text, mode = msg.sent[0]
    assert "# DB stats" in text
    assert text.startswith("```") and text.rstrip().endswith("```")   # monospace block
    assert mode == "Markdown"


async def test_report_ignores_unauthorised_chat() -> None:
    async def provider():
        return "secret stats"
    bot = _bot(provider)
    msg = _Msg()
    await bot._on_report(_Update(999, msg), None)   # not in allowlist [42]
    assert msg.sent == []


async def test_report_without_provider_is_graceful() -> None:
    bot = _bot(report_provider=None)
    msg = _Msg()
    await bot._on_report(_Update(42, msg), None)
    assert len(msg.sent) == 1 and "not available" in msg.sent[0][0].lower()


async def test_command_menu_registers_visible_commands() -> None:
    bot = _bot()
    captured: dict = {}

    class _TgBot:
        async def set_my_commands(self, commands):
            captured["cmds"] = commands

    bot._app = type("App", (), {"bot": _TgBot()})()
    await bot._register_command_menu()
    names = [c.command for c in captured["cmds"]]
    assert {"status", "report", "pause", "resume", "settings"} <= set(names)

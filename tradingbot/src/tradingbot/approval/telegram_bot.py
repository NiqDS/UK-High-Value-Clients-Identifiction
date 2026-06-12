"""python-telegram-bot (v20+, async) wiring.

This is the thin transport layer. All decision logic lives in the
unit-tested modules (`manager`, `controls`, `status`, `messages`); the handlers
here just authenticate the chat and delegate. ``telegram`` is imported lazily so
the rest of the package (and the test suite) does not require it installed.

It implements :class:`Notifier`, so the same object both sends approval requests
and receives the Approve/Reject taps that resolve them.
"""

from __future__ import annotations

import logging

from ..config import Config
from .controls import SettingsController, is_authorized
from .manager import ApprovalManager, ApprovalRequest
from .messages import (
    REJECT_PREFIX,
    callback_data,
    parse_callback,
    render_approval_message,
    render_settings,
)
from .status import StatusReporter

logger = logging.getLogger(__name__)


class TelegramApprovalBot:
    def __init__(
        self,
        token: str,
        config: Config,
        manager: ApprovalManager,
        settings: SettingsController,
        status: StatusReporter,
    ) -> None:
        self.token = token
        self.config = config
        self.manager = manager
        self.settings = settings
        self.status = status
        self._app = None  # telegram Application, built in start()

    @property
    def allowlist(self) -> list[int]:
        return self.config.telegram.allowed_chat_ids

    # -- Notifier interface -------------------------------------------------
    async def send_approval_request(self, req: ApprovalRequest) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        text = render_approval_message(self.config, req.intent, req.decision)
        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅ Approve", callback_data=callback_data("approve", req.id)),
                InlineKeyboardButton("❌ Reject", callback_data=callback_data("reject", req.id)),
            ]]
        )
        for chat_id in self.allowlist:
            await self._app.bot.send_message(
                chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="Markdown"
            )

    async def send_message(self, text: str) -> None:
        for chat_id in self.allowlist:
            await self._app.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")

    # -- handlers -----------------------------------------------------------
    def _authed(self, update) -> bool:
        chat = update.effective_chat
        if chat is None or not is_authorized(chat.id, self.allowlist):
            logger.warning("Ignoring message from unauthorised chat %s",
                           getattr(chat, "id", "?"))
            return False
        return True

    async def _on_callback(self, update, context) -> None:
        query = update.callback_query
        await query.answer()
        if not self._authed(update):
            return
        parsed = parse_callback(query.data or "")
        if parsed is None:
            return
        action, request_id = parsed
        approved = action != REJECT_PREFIX
        ok = self.manager.resolve(request_id, approved)
        verdict = "✅ Approved" if approved else "❌ Rejected"
        suffix = "" if ok else " (already resolved / expired)"
        await query.edit_message_text(f"{verdict}{suffix}")

    async def _on_status(self, update, context) -> None:
        if not self._authed(update):
            return
        await update.message.reply_text(await self.status.build(), parse_mode="Markdown")

    async def _on_settings(self, update, context) -> None:
        if not self._authed(update):
            return
        text = render_settings(self.settings.current_settings(), self.config.exchange.quote_currency)
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _on_pause(self, update, context) -> None:
        if not self._authed(update):
            return
        self.settings.pause_trading()
        await update.message.reply_text("⛔ Trading paused (kill switch engaged).")

    async def _on_resume(self, update, context) -> None:
        if not self._authed(update):
            return
        self.settings.resume_trading()
        await update.message.reply_text("▶️ Trading resumed.")

    def _make_setter(self, fn, label: str, cast):
        async def handler(update, context) -> None:
            if not self._authed(update):
                return
            try:
                value = cast(context.args[0])
                applied = fn(value)
                await update.message.reply_text(f"{label} set to {applied}")
            except (IndexError, ValueError) as exc:
                await update.message.reply_text(f"Usage error: {exc}")
        return handler

    # -- lifecycle ----------------------------------------------------------
    def build_application(self):
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
        )

        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("status", self._on_status))
        app.add_handler(CommandHandler(["settings", "menu"], self._on_settings))
        app.add_handler(CommandHandler("pause", self._on_pause))
        app.add_handler(CommandHandler("resume", self._on_resume))
        app.add_handler(CommandHandler(
            "set_threshold", self._make_setter(self.settings.set_approval_threshold, "threshold", float)))
        app.add_handler(CommandHandler(
            "set_max_notional", self._make_setter(self.settings.set_max_notional, "max notional", float)))
        app.add_handler(CommandHandler(
            "set_max_trades", self._make_setter(self.settings.set_max_trades_per_day, "max trades/day", int)))
        app.add_handler(CommandHandler(
            "set_daily_loss", self._make_setter(self.settings.set_max_daily_loss, "max daily loss", float)))
        app.add_handler(CallbackQueryHandler(self._on_callback))
        self._app = app
        return app

    async def start(self) -> None:
        if self._app is None:
            self.build_application()
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("Telegram bot started (allowlist: %s)", self.allowlist or "EMPTY — nobody authorised")

    async def stop(self) -> None:
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

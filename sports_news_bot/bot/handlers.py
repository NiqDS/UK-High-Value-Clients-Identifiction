import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.database import (
    get_user,
    set_user_active,
    set_user_language,
    set_user_team,
    upsert_user,
)
from bot.scheduler import _process_group
from bot.translator import SUPPORTED_LANGUAGES, is_supported, lang_display

logger = logging.getLogger(__name__)

_HELP_TEXT = """
👋 <b>Sports News Bot</b>

I monitor sports news for your team across sources in multiple languages and deliver updates translated into your language — every 15 minutes, automatically.

<b>Commands</b>
/setlang <code>CODE</code> — Set your language (e.g. <code>ru</code>, <code>en</code>, <code>de</code>)
/setteam <code>Name</code>  — Set the team to follow (e.g. <code>Real Madrid</code>)
/status             — Show your current settings
/latest             — Get latest news right now
/stop               — Pause automatic notifications
/resume             — Resume notifications
/help               — Show this message
""".strip()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await upsert_user(u.id, u.username or "", u.first_name or "")
    await update.message.reply_html(
        f"👋 Hello, <b>{u.first_name or 'there'}</b>!\n\n" + _HELP_TEXT
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(_HELP_TEXT)


async def setlang_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await upsert_user(u.id, u.username or "", u.first_name or "")

    if not context.args:
        langs = "\n".join(
            f"  <code>{code}</code> — {name}"
            for code, name in sorted(SUPPORTED_LANGUAGES.items())
        )
        await update.message.reply_html(
            f"🌐 <b>Set your language</b>\n\n"
            f"Usage: /setlang <code>CODE</code>\n\n"
            f"<b>Available languages:</b>\n{langs}"
        )
        return

    code = context.args[0].lower().strip()
    if not is_supported(code):
        await update.message.reply_text(
            f"❌ Language code '{code}' is not supported.\n"
            "Use /setlang without arguments to see all options."
        )
        return

    await set_user_language(u.id, code)
    await update.message.reply_html(
        f"✅ Language set to <b>{lang_display(code)}</b> (<code>{code}</code>)"
    )


async def setteam_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await upsert_user(u.id, u.username or "", u.first_name or "")

    if not context.args:
        await update.message.reply_html(
            "⚽ <b>Set your team</b>\n\n"
            "Usage: /setteam <code>Team Name</code>\n\n"
            "Examples:\n"
            "  /setteam <code>Real Madrid</code>\n"
            "  /setteam <code>Liverpool FC</code>\n"
            "  /setteam <code>Bayern Munich</code>"
        )
        return

    team = " ".join(context.args).strip()
    if len(team) < 2:
        await update.message.reply_text("❌ Team name must be at least 2 characters.")
        return

    await set_user_team(u.id, team)
    await update.message.reply_html(
        f"✅ Now tracking: <b>{team}</b>\n\n"
        "I'll check for news every 15 minutes.\n"
        "Use /latest to fetch news immediately."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row = await get_user(update.effective_user.id)
    if not row:
        await update.message.reply_text(
            "You haven't started yet. Send /start to begin."
        )
        return

    lang   = row["language"] or "en"
    team   = row["team_name"] or "Not set"
    status = "✅ Active" if row["active"] else "⏸ Paused"

    await update.message.reply_html(
        f"⚙️ <b>Your settings</b>\n\n"
        f"🌐 Language: <b>{lang_display(lang)}</b> (<code>{lang}</code>)\n"
        f"⚽ Team:     <b>{team}</b>\n"
        f"📬 Status:   {status}"
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await set_user_active(update.effective_user.id, False)
    await update.message.reply_text(
        "⏸ Notifications paused. Use /resume to start again."
    )


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await set_user_active(update.effective_user.id, True)
    await update.message.reply_text(
        "▶️ Notifications resumed! You'll receive news every 15 minutes."
    )


async def latest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row = await get_user(update.effective_user.id)
    if not row:
        await update.message.reply_text("Send /start first to set up the bot.")
        return
    if not row["team_name"]:
        await update.message.reply_text(
            "Please set a team first: /setteam <code>Team Name</code>",
            parse_mode="HTML",
        )
        return

    team = row["team_name"]
    lang = row["language"] or "en"
    await update.message.reply_text(f"🔍 Fetching latest news for {team}…")

    try:
        await _process_group(
            team_name=team,
            target_lang=lang,
            user_ids=[row["user_id"]],
            bot=context.bot,
        )
    except Exception as exc:
        logger.error("latest_command failed for user %d: %s", row["user_id"], exc)
        await update.message.reply_text(
            "⚠️ Could not fetch news at this moment. Please try again later."
        )

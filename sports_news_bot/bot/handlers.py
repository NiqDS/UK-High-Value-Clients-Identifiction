import logging
from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.database import (
    get_user,
    set_user_active,
    set_user_language,
    set_user_team,
    upsert_user,
)
from bot.scheduler import _process_group
from bot.team_lookup import lookup_team
from bot.translator import SUPPORTED_LANGUAGES, lang_display

logger = logging.getLogger(__name__)

# ──────────────────────────── Keyboards ────────────────────────────

def _lang_keyboard() -> InlineKeyboardMarkup:
    """3-column grid of all supported languages."""
    buttons = [
        InlineKeyboardButton(f"{name} ({code})", callback_data=f"lang:{code}")
        for code, name in sorted(SUPPORTED_LANGUAGES.items(), key=lambda x: x[1])
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    return InlineKeyboardMarkup(rows)


def _team_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, that's my team",  callback_data="team:yes"),
        InlineKeyboardButton("❌ No, search again",     callback_data="team:no"),
    ]])


def _start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Choose language", callback_data="menu:lang")],
        [InlineKeyboardButton("📰 Get latest news", callback_data="menu:latest"),
         InlineKeyboardButton("⚙️ My status",       callback_data="menu:status")],
    ])


def _status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Change language", callback_data="menu:lang"),
         InlineKeyboardButton("📰 Get news now",    callback_data="menu:latest")],
        [InlineKeyboardButton("⏸ Pause",  callback_data="menu:stop"),
         InlineKeyboardButton("▶️ Resume", callback_data="menu:resume")],
    ])


# ──────────────────────────── Commands ────────────────────────────

_HELP = (
    "⚽ <b>Sports News Bot</b>\n\n"
    "I search news about your team in multiple languages and send it "
    "translated into yours — every 15 minutes.\n\n"
    "<b>Commands</b>\n"
    "/setlang — Pick your language (shows a menu)\n"
    "/setteam <code>Name</code> — Set team, e.g. <code>/setteam Real Madrid</code>\n"
    "/status — Your current settings\n"
    "/latest — Fetch news right now\n"
    "/stop — Pause notifications\n"
    "/resume — Resume notifications\n"
    "/help — This message"
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await upsert_user(u.id, u.username or "", u.first_name or "")
    await update.message.reply_html(
        f"👋 Hello, <b>{u.first_name or 'there'}</b>!\n\n"
        "To get started:\n"
        "1️⃣ Tap <b>Choose language</b> below\n"
        "2️⃣ Then type: /setteam <code>Your Team</code>\n\n"
        "You can also tap <b>/</b> at any time to see all commands.",
        reply_markup=_start_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(_HELP)


async def setlang_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await upsert_user(u.id, u.username or "", u.first_name or "")

    if not context.args:
        # No code given → show the keyboard
        await update.message.reply_html(
            "🌐 <b>Choose your language:</b>",
            reply_markup=_lang_keyboard(),
        )
        return

    code = context.args[0].lower().strip()
    if code not in SUPPORTED_LANGUAGES:
        await update.message.reply_html(
            f"❌ <code>{code}</code> is not a supported language code.\n\n"
            "Tap the button below to pick from the full list:",
            reply_markup=_lang_keyboard(),
        )
        return

    await set_user_language(u.id, code)
    await update.message.reply_html(
        f"✅ Language set to <b>{lang_display(code)}</b> (<code>{code}</code>)\n\n"
        "Now set your team: /setteam <code>Team Name</code>"
    )


async def setteam_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await upsert_user(u.id, u.username or "", u.first_name or "")

    if not context.args:
        await update.message.reply_html(
            "⚽ <b>Set your team</b>\n\n"
            "Usage: /setteam <code>Team Name</code>\n\n"
            "Examples:\n"
            "  /setteam <code>Arsenal</code>\n"
            "  /setteam <code>Real Madrid</code>\n"
            "  /setteam <code>Bayern Munich</code>"
        )
        return

    raw_name = " ".join(context.args).strip()
    if len(raw_name) < 2:
        await update.message.reply_text("❌ Team name must be at least 2 characters.")
        return

    msg = await update.message.reply_text(f"🔍 Looking up \"{raw_name}\"…")

    info = await lookup_team(raw_name)
    if info:
        context.user_data["pending_team"] = info
        preview = (
            f"Found: <b>{info['name']}</b>\n"
            f"{info['flag']} {info['country']}  {info['emoji']} {info['sport']}\n\n"
            "Is this the team you want to follow?"
        )
    else:
        # Fallback — no DB match, just confirm the raw name
        context.user_data["pending_team"] = {
            "name": raw_name,
            "country": "Unknown",
            "flag": "🌍",
            "sport": "Sports",
            "emoji": "🏆",
        }
        preview = (
            f"Team: <b>{raw_name}</b>\n"
            "(Could not find details — confirm anyway?)"
        )

    await msg.edit_text(preview, parse_mode="HTML", reply_markup=_team_confirm_keyboard())


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row = await get_user(update.effective_user.id)
    if not row:
        await update.message.reply_text("Send /start to set up the bot.")
        return

    lang   = row["language"] or "en"
    team   = row["team_name"] or "Not set"
    status = "✅ Active" if row["active"] else "⏸ Paused"

    await update.message.reply_html(
        f"⚙️ <b>Your settings</b>\n\n"
        f"🌐 Language: <b>{lang_display(lang)}</b> (<code>{lang}</code>)\n"
        f"⚽ Team:     <b>{team}</b>\n"
        f"📬 Status:   {status}",
        reply_markup=_status_keyboard(),
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await set_user_active(update.effective_user.id, False)
    await update.message.reply_text("⏸ Notifications paused. Use /resume to start again.")


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await set_user_active(update.effective_user.id, True)
    await update.message.reply_text("▶️ Notifications resumed! News every 15 minutes.")


async def latest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row = await get_user(update.effective_user.id)
    if not row:
        await update.message.reply_text("Send /start first.")
        return
    if not row["team_name"]:
        await update.message.reply_html(
            "Please set a team first: /setteam <code>Team Name</code>"
        )
        return

    await update.message.reply_text(f"🔍 Fetching latest news for {row['team_name']}…")
    try:
        await _process_group(
            team_name=row["team_name"],
            target_lang=row["language"] or "en",
            user_ids=[row["user_id"]],
            bot=context.bot,
        )
    except Exception as exc:
        logger.error("latest_command failed for user %d: %s", row["user_id"], exc)
        await update.message.reply_text(
            "⚠️ Could not fetch news right now. Please try again later."
        )


# ──────────────────────────── Inline button callback ────────────────────────────

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()          # dismiss the loading spinner
    data  = query.data or ""
    user  = query.from_user

    # ── Language selection ──────────────────────────────────────────
    if data.startswith("lang:"):
        code = data[5:]
        if code not in SUPPORTED_LANGUAGES:
            await query.edit_message_text("❌ Unknown language code.")
            return
        await upsert_user(user.id, user.username or "", user.first_name or "")
        await set_user_language(user.id, code)
        await query.edit_message_text(
            f"✅ Language set to <b>{lang_display(code)}</b> (<code>{code}</code>)\n\n"
            "Now set your team: /setteam <code>Team Name</code>",
            parse_mode="HTML",
        )
        return

    # ── Team confirmation ───────────────────────────────────────────
    if data == "team:yes":
        pending = context.user_data.get("pending_team")
        if not pending:
            await query.edit_message_text(
                "⚠️ Session expired. Please run /setteam again."
            )
            return
        await set_user_team(user.id, pending["name"])
        context.user_data.pop("pending_team", None)
        await query.edit_message_text(
            f"✅ Now tracking: <b>{pending['name']}</b>\n"
            f"{pending['flag']} {pending['country']}  "
            f"{pending['emoji']} {pending['sport']}\n\n"
            "You'll receive news every 15 minutes.\n"
            "Use /latest to get news right now!",
            parse_mode="HTML",
        )
        return

    if data == "team:no":
        context.user_data.pop("pending_team", None)
        await query.edit_message_text(
            "❌ Cancelled.\n\nTry again: /setteam <code>Team Name</code>",
            parse_mode="HTML",
        )
        return

    # ── Quick-menu buttons ──────────────────────────────────────────
    if data == "menu:lang":
        await query.edit_message_text(
            "🌐 <b>Choose your language:</b>",
            parse_mode="HTML",
            reply_markup=_lang_keyboard(),
        )
        return

    if data == "menu:status":
        row = await get_user(user.id)
        if not row:
            await query.edit_message_text("Use /start to set up the bot.")
            return
        lang   = row["language"] or "en"
        team   = row["team_name"] or "Not set"
        status = "✅ Active" if row["active"] else "⏸ Paused"
        await query.edit_message_text(
            f"⚙️ <b>Your settings</b>\n\n"
            f"🌐 Language: <b>{lang_display(lang)}</b> (<code>{lang}</code>)\n"
            f"⚽ Team:     <b>{team}</b>\n"
            f"📬 Status:   {status}",
            parse_mode="HTML",
            reply_markup=_status_keyboard(),
        )
        return

    if data == "menu:latest":
        row = await get_user(user.id)
        if not row or not row["team_name"]:
            await query.edit_message_text(
                "Please set a team first: /setteam <code>Team Name</code>",
                parse_mode="HTML",
            )
            return
        await query.edit_message_text(
            f"🔍 Fetching latest news for {row['team_name']}…"
        )
        try:
            await _process_group(
                team_name=row["team_name"],
                target_lang=row["language"] or "en",
                user_ids=[user.id],
                bot=context.bot,
            )
        except Exception as exc:
            logger.error("menu:latest failed for user %d: %s", user.id, exc)
        return

    if data == "menu:stop":
        await set_user_active(user.id, False)
        await query.edit_message_text(
            "⏸ Notifications paused. Use /resume or tap Resume below.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("▶️ Resume", callback_data="menu:resume")
            ]]),
        )
        return

    if data == "menu:resume":
        await set_user_active(user.id, True)
        await query.edit_message_text(
            "▶️ Notifications resumed! News every 15 minutes.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏸ Pause again", callback_data="menu:stop")
            ]]),
        )
        return

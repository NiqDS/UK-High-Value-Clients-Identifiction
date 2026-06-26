"""Telegram command, message and callback-query handlers."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import (
    DEFAULT_CHECK_INTERVAL_MINUTES,
    MAX_MONITORED_ITEMS_PER_USER,
    MIN_CHECK_INTERVAL_MINUTES,
)
from database import Database
from scheduler import MonitorScheduler
from scraper import ScrapeError, scrape

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)

# Keys placed on application.bot_data by bot.py.
DB_KEY = "db"
SCHED_KEY = "scheduler"
START_TIME_KEY = "start_time"

# Pending confirmations keyed by chat_id (one in-flight URL per chat).
PENDING_KEY = "pending_confirmations"


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data[DB_KEY]


def _sched(context: ContextTypes.DEFAULT_TYPE) -> MonitorScheduler:
    return context.application.bot_data[SCHED_KEY]


def _md(text: str) -> str:
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


# --- Commands ---------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 *Welcome to Price Monitor Bot!*\n\n"
        "I watch product pages and ping you when the price changes.\n\n"
        "*How to use me:*\n"
        "• Just paste a product URL, or use /monitor <url>\n"
        "• I'll grab the item name and price, then ask you to confirm\n"
        "• Once confirmed, I'll check it on a schedule\n\n"
        "*Commands:*\n"
        "/monitor <url> — start monitoring a URL\n"
        "/list — show monitored items\n"
        "/remove — remove a monitored item\n"
        f"/interval <minutes> — set check interval (min {MIN_CHECK_INTERVAL_MINUTES})\n"
        "/stop — stop all monitoring\n"
        "/status — bot uptime and active monitors\n"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        url = context.args[0]
        await _begin_monitor_flow(update, context, url)
    else:
        await update.effective_message.reply_text(
            "Send me a URL like: /monitor https://example.com/product"
        )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a bare pasted URL."""
    message = update.effective_message
    if not message or not message.text:
        return
    match = _URL_RE.search(message.text)
    if not match:
        return
    await _begin_monitor_flow(update, context, match.group(0))


async def _begin_monitor_flow(
    update: Update, context: ContextTypes.DEFAULT_TYPE, url: str
) -> None:
    chat_id = update.effective_chat.id
    db = _db(context)

    count = await db.count_active_for_chat(chat_id)
    if count >= MAX_MONITORED_ITEMS_PER_USER:
        await update.effective_message.reply_text(
            f"⚠️ You've reached the limit of {MAX_MONITORED_ITEMS_PER_USER} "
            "monitored items. Remove one with /remove first."
        )
        return

    status = await update.effective_message.reply_text("🔍 Scraping the page…")

    try:
        result = await scrape(url)
    except ScrapeError as exc:
        logger.info("Scrape failed for %s: %s", url, exc)
        await status.edit_text(
            "⚠️ Couldn't load that page (it may be blocking bots or be "
            "temporarily unreachable). Please try again later."
        )
        return

    if result is None or result.price is None:
        await status.edit_text(
            "⚠️ Could not detect a price on this page. "
            "The site may require JavaScript rendering."
        )
        return

    # Stash the pending confirmation for this chat.
    pending = context.application.bot_data.setdefault(PENDING_KEY, {})
    pending[chat_id] = {
        "url": url,
        "item_name": result.item_name,
        "price": round(result.price, 2),
        "currency_symbol": result.currency_symbol,
    }

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Yes, Monitor", callback_data="confirm:yes"),
                InlineKeyboardButton("❌ No, Cancel", callback_data="confirm:no"),
            ]
        ]
    )
    sym = result.currency_symbol
    text = (
        f"🔍 Found: *{_md(result.item_name)}*\n"
        f"💰 Current price: *{sym}{result.price:.2f}*\n\n"
        "Start monitoring this item?"
    )
    await status.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    items = await _db(context).list_active_for_chat(chat_id)
    if not items:
        await update.effective_message.reply_text(
            "You're not monitoring anything yet. Paste a product URL to begin."
        )
        return

    lines = ["📋 *Monitored items:*\n"]
    for i, item in enumerate(items, start=1):
        sym = item.currency_symbol
        price = f"{sym}{item.last_price:.2f}" if item.last_price is not None else "—"
        lines.append(
            f"{i}. *{_md(item.item_name)}*\n"
            f"    💰 {price}  •  ⏱ every {item.check_interval_minutes} min\n"
            f"    🔗 {item.url}"
        )
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
    )


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    items = await _db(context).list_active_for_chat(chat_id)
    if not items:
        await update.effective_message.reply_text("Nothing to remove.")
        return

    buttons = [
        [
            InlineKeyboardButton(
                f"❌ {item.item_name[:40]}", callback_data=f"remove:{item.id}"
            )
        ]
        for item in items
    ]
    await update.effective_message.reply_text(
        "Select an item to stop monitoring:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def cmd_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not context.args:
        await update.effective_message.reply_text(
            f"Usage: /interval <minutes> (minimum {MIN_CHECK_INTERVAL_MINUTES}, "
            f"default {DEFAULT_CHECK_INTERVAL_MINUTES})."
        )
        return

    try:
        minutes = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Please give a whole number of minutes.")
        return

    if minutes < MIN_CHECK_INTERVAL_MINUTES:
        await update.effective_message.reply_text(
            f"⚠️ Minimum interval is {MIN_CHECK_INTERVAL_MINUTES} minutes."
        )
        return

    db = _db(context)
    sched = _sched(context)
    updated = await db.set_interval_for_chat(chat_id, minutes)
    # Reschedule each affected job.
    for item in await db.list_active_for_chat(chat_id):
        sched.reschedule_item(item)

    await update.effective_message.reply_text(
        f"✅ Check interval set to {minutes} minutes "
        f"({updated} item(s) updated)."
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    db = _db(context)
    sched = _sched(context)

    items = await db.list_active_for_chat(chat_id)
    for item in items:
        sched.remove_item(item.id)
    count = await db.deactivate_all_for_chat(chat_id)

    if count:
        await update.effective_message.reply_text(
            f"🛑 Stopped monitoring all {count} item(s)."
        )
    else:
        await update.effective_message.reply_text("Nothing was being monitored.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    start_time = context.application.bot_data.get(START_TIME_KEY, time.time())
    uptime = int(time.time() - start_time)
    days, rem = divmod(uptime, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")

    total_active = await _db(context).count_all_active()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    await update.effective_message.reply_text(
        "🤖 *Bot status*\n"
        f"⏱ Uptime: {' '.join(parts)}\n"
        f"📊 Active monitors: {total_active}\n"
        f"🕒 Now: {now}",
        parse_mode=ParseMode.MARKDOWN,
    )


# --- Callback queries -------------------------------------------------------


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    action, _, payload = query.data.partition(":")
    if action == "confirm":
        await _handle_confirm(update, context, payload)
    elif action == "remove":
        await _handle_remove(update, context, payload)


async def _handle_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str
) -> None:
    query = update.callback_query
    chat_id = update.effective_chat.id
    pending = context.application.bot_data.get(PENDING_KEY, {})
    data = pending.pop(chat_id, None)

    if payload == "no":
        await query.edit_message_text("❌ Cancelled. Nothing will be monitored.")
        return

    if data is None:
        await query.edit_message_text(
            "⚠️ This confirmation expired. Please paste the URL again."
        )
        return

    db = _db(context)
    sched = _sched(context)

    # Re-check the per-user cap in case it changed meanwhile.
    if await db.count_active_for_chat(chat_id) >= MAX_MONITORED_ITEMS_PER_USER:
        await query.edit_message_text(
            f"⚠️ You've reached the limit of {MAX_MONITORED_ITEMS_PER_USER} items."
        )
        return

    item = await db.add_item(
        chat_id=chat_id,
        url=data["url"],
        item_name=data["item_name"],
        last_price=data["price"],
        currency_symbol=data["currency_symbol"],
        check_interval_minutes=DEFAULT_CHECK_INTERVAL_MINUTES,
    )
    sched.schedule_item(item)

    sym = item.currency_symbol
    await query.edit_message_text(
        f"✅ Now monitoring *{_md(item.item_name)}* at {sym}{item.last_price:.2f}\n"
        f"⏱ Checking every {item.check_interval_minutes} minutes.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _handle_remove(
    update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str
) -> None:
    query = update.callback_query
    try:
        item_id = int(payload)
    except ValueError:
        return

    db = _db(context)
    sched = _sched(context)
    item = await db.get_item(item_id)

    if item is None or item.chat_id != update.effective_chat.id:
        await query.edit_message_text("⚠️ That item no longer exists.")
        return

    sched.remove_item(item_id)
    await db.deactivate_item(item_id)
    await query.edit_message_text(
        f"🗑 Removed *{_md(item.item_name)}* from monitoring.",
        parse_mode=ParseMode.MARKDOWN,
    )

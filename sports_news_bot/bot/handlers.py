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
from bot.team_lookup import (
    COUNTRY_FLAGS,
    FOOTBALL_COUNTRIES,
    get_football_leagues,
    get_league_teams,
    lookup_team,
)
from bot.translator import SUPPORTED_LANGUAGES, lang_display

logger = logging.getLogger(__name__)

TEAMS_PER_PAGE = 18   # 3 columns × 6 rows per page


# ══════════════════════════════════════════════════════════════════
#  Keyboard builders
# ══════════════════════════════════════════════════════════════════

def _lang_keyboard() -> InlineKeyboardMarkup:
    """3-column grid of all supported languages."""
    buttons = [
        InlineKeyboardButton(f"{name} ({code})", callback_data=f"lang:{code}")
        for code, name in sorted(SUPPORTED_LANGUAGES.items(), key=lambda x: x[1])
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    return InlineKeyboardMarkup(rows)


def _country_keyboard() -> InlineKeyboardMarkup:
    """2-column grid of football countries."""
    buttons = [
        InlineKeyboardButton(f"{flag} {name}", callback_data=f"ts_c:{name}")
        for name, flag in FOOTBALL_COUNTRIES
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("✏️ Type team name directly", callback_data="ts_direct")])
    return InlineKeyboardMarkup(rows)


def _leagues_keyboard(leagues: List[dict], country: str) -> InlineKeyboardMarkup:
    flag = COUNTRY_FLAGS.get(country, "🌍")
    rows = [
        [InlineKeyboardButton(f"🏆 {l['name']}", callback_data=f"ts_l:{l['id']}")]
        for l in leagues
    ]
    rows.append([InlineKeyboardButton("🔙 Change country", callback_data="ts_bc")])
    return InlineKeyboardMarkup(rows)


def _teams_keyboard(teams: List[dict], page: int) -> InlineKeyboardMarkup:
    start = page * TEAMS_PER_PAGE
    slice_ = teams[start : start + TEAMS_PER_PAGE]

    buttons = [
        InlineKeyboardButton(t["name"], callback_data=f"ts_t:{start + i}")
        for i, t in enumerate(slice_)
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]

    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"ts_pg:{page - 1}"))
    if start + TEAMS_PER_PAGE < len(teams):
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"ts_pg:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 Change division", callback_data="ts_bl")])
    return InlineKeyboardMarkup(rows)


def _ts_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data="ts_ok"),
        InlineKeyboardButton("🔙 Back",    callback_data="ts_bt"),
    ]])


def _team_confirm_keyboard() -> InlineKeyboardMarkup:
    """Used by the direct /setteam Name flow (old path)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, that's my team", callback_data="team:yes"),
        InlineKeyboardButton("❌ No, search again",    callback_data="team:no"),
    ]])


def _start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Choose language",  callback_data="menu:lang")],
        [InlineKeyboardButton("⚽ Pick team",        callback_data="menu:team"),
         InlineKeyboardButton("⚙️ My status",       callback_data="menu:status")],
    ])


def _status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Change language", callback_data="menu:lang"),
         InlineKeyboardButton("📰 Get news now",    callback_data="menu:latest")],
        [InlineKeyboardButton("⏸ Pause",  callback_data="menu:stop"),
         InlineKeyboardButton("▶️ Resume", callback_data="menu:resume")],
    ])


# ══════════════════════════════════════════════════════════════════
#  Command handlers
# ══════════════════════════════════════════════════════════════════

_HELP = (
    "⚽ <b>Sports News Bot</b>\n\n"
    "I search news about your team in multiple languages and send it "
    "translated into yours — every 15 minutes.\n\n"
    "<b>Commands</b>\n"
    "/setlang — Pick your language (shows a menu)\n"
    "/setteam — Pick your team step-by-step, or /setteam <code>Name</code>\n"
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
        "2️⃣ Tap <b>Pick team</b> and follow the steps\n\n"
        "You can also tap <b>/</b> to see all commands.",
        reply_markup=_start_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(_HELP)


async def setlang_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await upsert_user(u.id, u.username or "", u.first_name or "")

    if not context.args:
        await update.message.reply_html(
            "🌐 <b>Choose your language:</b>",
            reply_markup=_lang_keyboard(),
        )
        return

    code = context.args[0].lower().strip()
    if code not in SUPPORTED_LANGUAGES:
        await update.message.reply_html(
            f"❌ <code>{code}</code> is not supported.\n\nPick from the list:",
            reply_markup=_lang_keyboard(),
        )
        return

    await set_user_language(u.id, code)
    await update.message.reply_html(
        f"✅ Language set to <b>{lang_display(code)}</b> (<code>{code}</code>)\n\n"
        "Now pick your team: /setteam"
    )


async def setteam_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await upsert_user(u.id, u.username or "", u.first_name or "")

    if not context.args:
        # Step-by-step flow
        await update.message.reply_html(
            "⚽ <b>Pick your team</b>  (step 1 of 3)\n\n"
            "Choose the <b>country</b> of the league:",
            reply_markup=_country_keyboard(),
        )
        return

    # Direct name shortcut: /setteam Arsenal
    raw = " ".join(context.args).strip()
    if len(raw) < 2:
        await update.message.reply_text("❌ Team name must be at least 2 characters.")
        return

    msg = await update.message.reply_text(f'🔍 Looking up "{raw}"…')
    info = await lookup_team(raw)
    if info:
        context.user_data["pending_team"] = info
        preview = (
            f"Found: <b>{info['name']}</b>\n"
            f"{info['flag']} {info['country']}  {info['emoji']} {info['sport']}\n\n"
            "Confirm this team?"
        )
    else:
        context.user_data["pending_team"] = {
            "name": raw, "country": "Unknown",
            "flag": "🌍", "sport": "Sports", "emoji": "🏆",
        }
        preview = f"Team: <b>{raw}</b>\n(No details found — confirm anyway?)"

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
        await update.message.reply_html("Set a team first: /setteam")
        return
    await update.message.reply_text(f"🔍 Fetching latest news for {row['team_name']}…")
    try:
        sent = await _process_group(
            team_name=row["team_name"],
            target_lang=row["language"] or "en",
            user_ids=[row["user_id"]],
            bot=context.bot,
        )
        if sent == 0:
            await update.message.reply_text(
                "📭 No new articles found right now. I'll check again in 15 minutes!"
            )
    except Exception as exc:
        logger.error("latest_command failed for user %d: %s", row["user_id"], exc)
        await update.message.reply_text("⚠️ Could not fetch news. Try again later.")


# ══════════════════════════════════════════════════════════════════
#  Shared: save team + send test post
# ══════════════════════════════════════════════════════════════════

async def _confirm_and_post(
    query,          # CallbackQuery
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    team_name: str,
    country: str,
    flag: str,
    sport: str,
    league: str,
) -> None:
    """Save the team and immediately send a test news post."""
    await set_user_team(user_id, team_name)
    context.user_data.pop("ts", None)
    context.user_data.pop("pending_team", None)

    await query.edit_message_text(
        f"✅ <b>{team_name}</b> saved!\n"
        f"{flag} {country}  🏆 {league}\n\n"
        "⏳ Fetching your first news post…",
        parse_mode="HTML",
    )

    row = await get_user(user_id)
    target_lang = (row["language"] if row else None) or "en"
    try:
        sent = await _process_group(
            team_name=team_name,
            target_lang=target_lang,
            user_ids=[user_id],
            bot=context.bot,
        )
        if sent == 0:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "📭 No news found at the moment.\n"
                    "I'll send updates automatically every 15 minutes.\n"
                    "Use /latest to check again anytime."
                ),
            )
    except Exception as exc:
        logger.error("Test news post failed for user %d: %s", user_id, exc)


# ══════════════════════════════════════════════════════════════════
#  Inline button callback — handles ALL button presses
# ══════════════════════════════════════════════════════════════════

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data  = query.data or ""
    user  = query.from_user

    # ── Language selection ────────────────────────────────────────
    if data.startswith("lang:"):
        code = data[5:]
        if code not in SUPPORTED_LANGUAGES:
            await query.edit_message_text("❌ Unknown language code.")
            return
        await upsert_user(user.id, user.username or "", user.first_name or "")
        await set_user_language(user.id, code)
        await query.edit_message_text(
            f"✅ Language set to <b>{lang_display(code)}</b> (<code>{code}</code>)\n\n"
            "Now pick your team — tap /setteam",
            parse_mode="HTML",
        )
        return

    # ── Old direct-name confirm flow (team:yes / team:no) ─────────
    if data == "team:yes":
        pending = context.user_data.get("pending_team")
        if not pending:
            await query.edit_message_text("⚠️ Session expired. Use /setteam again.")
            return
        await _confirm_and_post(
            query=query, context=context, user_id=user.id,
            team_name=pending["name"],
            country=pending.get("country", "Unknown"),
            flag=pending.get("flag", "🌍"),
            sport=pending.get("sport", "Sports"),
            league=pending.get("sport", "Sports"),
        )
        return

    if data == "team:no":
        context.user_data.pop("pending_team", None)
        await query.edit_message_text(
            "❌ Cancelled.\n\nUse /setteam to try again.",
            parse_mode="HTML",
        )
        return

    # ── Step-by-step team flow: ts_* ─────────────────────────────

    # "Type name directly" shortcut inside the country picker
    if data == "ts_direct":
        await query.edit_message_text(
            "✏️ Type: /setteam <code>Team Name</code>\n\n"
            "Example: /setteam <code>Arsenal</code>",
            parse_mode="HTML",
        )
        return

    # Step 1 → 2: country selected, fetch leagues
    if data.startswith("ts_c:"):
        country = data[5:]
        await query.edit_message_text(f"⏳ Loading leagues for {country}…")
        leagues = await get_football_leagues(country)
        if not leagues:
            await query.edit_message_text(
                f"❌ No football leagues found for {country}.\n"
                "Try another country:",
                reply_markup=_country_keyboard(),
            )
            return
        context.user_data["ts"] = {"country": country, "leagues": leagues}
        flag = COUNTRY_FLAGS.get(country, "🌍")
        await query.edit_message_text(
            f"⚽ <b>Pick your team</b>  (step 2 of 3)\n\n"
            f"{flag} <b>{country}</b> — choose a division:",
            parse_mode="HTML",
            reply_markup=_leagues_keyboard(leagues, country),
        )
        return

    # Step 2 → 3: league selected, fetch teams
    if data.startswith("ts_l:"):
        league_id = data[5:]
        ts = context.user_data.get("ts", {})
        leagues = ts.get("leagues", [])
        league_name = next(
            (l["name"] for l in leagues if l["id"] == league_id), "League"
        )
        await query.edit_message_text(f"⏳ Loading teams for {league_name}…")
        teams = await get_league_teams(league_id)
        if not teams:
            country = ts.get("country", "")
            await query.edit_message_text(
                f"❌ No teams found for {league_name}.\n"
                "Try another division:",
                reply_markup=_leagues_keyboard(leagues, country),
            )
            return
        ts.update({"league_id": league_id, "league_name": league_name,
                   "teams": teams, "page": 0})
        context.user_data["ts"] = ts
        country = ts.get("country", "")
        flag = COUNTRY_FLAGS.get(country, "🌍")
        await query.edit_message_text(
            f"⚽ <b>Pick your team</b>  (step 3 of 3)\n\n"
            f"{flag} {league_name} — {len(teams)} teams:",
            parse_mode="HTML",
            reply_markup=_teams_keyboard(teams, 0),
        )
        return

    # Step 3: team selected, show confirmation card
    if data.startswith("ts_t:"):
        idx = int(data[5:])
        ts = context.user_data.get("ts", {})
        teams = ts.get("teams", [])
        if idx >= len(teams):
            await query.edit_message_text("⚠️ Team not found. Please start over with /setteam.")
            return
        team = teams[idx]
        ts["selected_idx"] = idx
        context.user_data["ts"] = ts
        country = team.get("country") or ts.get("country", "Unknown")
        flag    = COUNTRY_FLAGS.get(country, "🌍")
        league  = ts.get("league_name", "Unknown")
        await query.edit_message_text(
            f"⚽ <b>{team['name']}</b>\n"
            f"{flag} {country}\n"
            f"🏆 {league}\n\n"
            "Confirm selection?",
            parse_mode="HTML",
            reply_markup=_ts_confirm_keyboard(),
        )
        return

    # Pagination within team list
    if data.startswith("ts_pg:"):
        page = int(data[6:])
        ts = context.user_data.get("ts", {})
        teams = ts.get("teams", [])
        ts["page"] = page
        context.user_data["ts"] = ts
        country     = ts.get("country", "")
        league_name = ts.get("league_name", "League")
        flag        = COUNTRY_FLAGS.get(country, "🌍")
        await query.edit_message_text(
            f"⚽ <b>Pick your team</b>  (step 3 of 3)\n\n"
            f"{flag} {league_name} — {len(teams)} teams:",
            parse_mode="HTML",
            reply_markup=_teams_keyboard(teams, page),
        )
        return

    # Confirmation: yes → save + test post
    if data == "ts_ok":
        ts  = context.user_data.get("ts", {})
        idx = ts.get("selected_idx")
        teams = ts.get("teams", [])
        if idx is None or idx >= len(teams):
            await query.edit_message_text("⚠️ Session expired. Use /setteam again.")
            return
        team    = teams[idx]
        country = team.get("country") or ts.get("country", "Unknown")
        flag    = COUNTRY_FLAGS.get(country, "🌍")
        league  = ts.get("league_name", "Unknown")
        await _confirm_and_post(
            query=query, context=context, user_id=user.id,
            team_name=team["name"], country=country,
            flag=flag, sport="Soccer", league=league,
        )
        return

    # Back to team list (from confirm screen)
    if data == "ts_bt":
        ts   = context.user_data.get("ts", {})
        teams = ts.get("teams", [])
        page  = ts.get("page", 0)
        country     = ts.get("country", "")
        league_name = ts.get("league_name", "League")
        flag        = COUNTRY_FLAGS.get(country, "🌍")
        if not teams:
            await query.edit_message_text(
                "⚽ <b>Pick your team</b>  (step 1 of 3)\n\nChoose a country:",
                parse_mode="HTML",
                reply_markup=_country_keyboard(),
            )
            return
        await query.edit_message_text(
            f"⚽ <b>Pick your team</b>  (step 3 of 3)\n\n"
            f"{flag} {league_name} — {len(teams)} teams:",
            parse_mode="HTML",
            reply_markup=_teams_keyboard(teams, page),
        )
        return

    # Back to league list
    if data == "ts_bl":
        ts = context.user_data.get("ts", {})
        leagues = ts.get("leagues", [])
        country = ts.get("country", "")
        flag    = COUNTRY_FLAGS.get(country, "🌍")
        if not leagues:
            await query.edit_message_text(
                "⚽ <b>Pick your team</b>  (step 1 of 3)\n\nChoose a country:",
                parse_mode="HTML",
                reply_markup=_country_keyboard(),
            )
            return
        await query.edit_message_text(
            f"⚽ <b>Pick your team</b>  (step 2 of 3)\n\n"
            f"{flag} <b>{country}</b> — choose a division:",
            parse_mode="HTML",
            reply_markup=_leagues_keyboard(leagues, country),
        )
        return

    # Back to country list
    if data == "ts_bc":
        context.user_data.pop("ts", None)
        await query.edit_message_text(
            "⚽ <b>Pick your team</b>  (step 1 of 3)\n\nChoose a country:",
            parse_mode="HTML",
            reply_markup=_country_keyboard(),
        )
        return

    # ── Quick-menu buttons ────────────────────────────────────────
    if data == "menu:lang":
        await query.edit_message_text(
            "🌐 <b>Choose your language:</b>",
            parse_mode="HTML",
            reply_markup=_lang_keyboard(),
        )
        return

    if data == "menu:team":
        await query.edit_message_text(
            "⚽ <b>Pick your team</b>  (step 1 of 3)\n\nChoose a country:",
            parse_mode="HTML",
            reply_markup=_country_keyboard(),
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
                "Set a team first with /setteam", parse_mode="HTML"
            )
            return
        await query.edit_message_text(f"🔍 Fetching latest news for {row['team_name']}…")
        try:
            sent = await _process_group(
                team_name=row["team_name"],
                target_lang=row["language"] or "en",
                user_ids=[user.id],
                bot=context.bot,
            )
            if sent == 0:
                await context.bot.send_message(
                    chat_id=user.id,
                    text="📭 No new articles right now. Check back in 15 minutes!",
                )
        except Exception as exc:
            logger.error("menu:latest failed for user %d: %s", user.id, exc)
        return

    if data == "menu:stop":
        await set_user_active(user.id, False)
        await query.edit_message_text(
            "⏸ Notifications paused.",
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
                InlineKeyboardButton("⏸ Pause", callback_data="menu:stop")
            ]]),
        )
        return

import logging
from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.database import (
    MAX_TEAMS,
    add_user_team,
    get_user,
    get_user_teams,
    remove_user_team,
    set_user_active,
    set_user_language,
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
    buttons = [
        InlineKeyboardButton(f"{name} ({code})", callback_data=f"lang:{code}")
        for code, name in sorted(SUPPORTED_LANGUAGES.items(), key=lambda x: x[1])
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    return InlineKeyboardMarkup(rows)


def _country_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(f"{flag} {name}", callback_data=f"ts_c:{name}")
        for name, flag in FOOTBALL_COUNTRIES
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("✏️ Type team name directly", callback_data="ts_direct")])
    return InlineKeyboardMarkup(rows)


def _leagues_keyboard(leagues: List[dict], country: str) -> InlineKeyboardMarkup:
    # Use array index as callback data — avoids any ID confusion
    rows = [
        [InlineKeyboardButton(f"🏆 {l['name']}", callback_data=f"ts_l:{i}")]
        for i, l in enumerate(leagues)
    ]
    rows.append([InlineKeyboardButton("🔙 Change country", callback_data="ts_bc")])
    return InlineKeyboardMarkup(rows)


def _teams_keyboard(teams: List[dict], page: int) -> InlineKeyboardMarkup:
    start  = page * TEAMS_PER_PAGE
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
        InlineKeyboardButton("✅ Confirm",  callback_data="ts_ok"),
        InlineKeyboardButton("🔙 Back",     callback_data="ts_bt"),
    ]])


def _team_confirm_keyboard() -> InlineKeyboardMarkup:
    """Used only by the direct /setteam Name shortcut."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, that's my team", callback_data="team:yes"),
        InlineKeyboardButton("❌ No, search again",    callback_data="team:no"),
    ]])


def _add_more_keyboard(current_count: int) -> InlineKeyboardMarkup:
    rows = []
    if current_count < MAX_TEAMS:
        rows.append([InlineKeyboardButton(
            f"➕ Add another team ({current_count}/{MAX_TEAMS})",
            callback_data="ts_add_more",
        )])
    rows.append([InlineKeyboardButton("✅ Done — show my teams", callback_data="ts_show_teams")])
    return InlineKeyboardMarkup(rows)


def _my_teams_keyboard(teams: List[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"❌ Remove {t}", callback_data=f"rm_team:{i}")]
        for i, t in enumerate(teams)
    ]
    if len(teams) < MAX_TEAMS:
        rows.append([InlineKeyboardButton("➕ Add a team", callback_data="menu:team")])
    rows.append([InlineKeyboardButton("📰 Get news now", callback_data="menu:latest")])
    return InlineKeyboardMarkup(rows)


def _start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Choose language", callback_data="menu:lang")],
        [InlineKeyboardButton("⚽ Pick team",       callback_data="menu:team"),
         InlineKeyboardButton("⚙️ My status",      callback_data="menu:status")],
    ])


def _status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Change language", callback_data="menu:lang"),
         InlineKeyboardButton("📰 Get news now",    callback_data="menu:latest")],
        [InlineKeyboardButton("⚽ Manage teams",    callback_data="ts_show_teams")],
        [InlineKeyboardButton("⏸ Pause",  callback_data="menu:stop"),
         InlineKeyboardButton("▶️ Resume", callback_data="menu:resume")],
    ])


# ══════════════════════════════════════════════════════════════════
#  Shared helpers
# ══════════════════════════════════════════════════════════════════

def _teams_text(teams: List[str]) -> str:
    if not teams:
        return "No teams yet."
    lines = [f"⚽ <b>Your teams ({len(teams)}/{MAX_TEAMS}):</b>\n"]
    for i, t in enumerate(teams, 1):
        lines.append(f"{i}. {t}")
    return "\n".join(lines)


async def _confirm_and_post(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    team_name: str,
    country: str,
    flag: str,
    league: str,
) -> None:
    """Save team, send test post, then offer to add more or finish."""
    await add_user_team(user_id, team_name)
    context.user_data.pop("ts", None)
    context.user_data.pop("pending_team", None)

    all_teams = await get_user_teams(user_id)

    await query.edit_message_text(
        f"✅ <b>{team_name}</b> added!\n"
        f"{flag} {country}  🏆 {league}\n\n"
        f"Teams saved: {len(all_teams)}/{MAX_TEAMS}\n\n"
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
                    "📭 No news found yet.\n"
                    "I'll check automatically every 15 minutes.\n"
                    "Use /latest to check again anytime."
                ),
            )
    except Exception as exc:
        logger.error("Test post failed for user %d: %s", user_id, exc)

    # Re-fetch so count is accurate after the save
    all_teams = await get_user_teams(user_id)
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"<b>What's next?</b>\n\n"
            + _teams_text(all_teams)
        ),
        parse_mode="HTML",
        reply_markup=_add_more_keyboard(len(all_teams)),
    )


# ══════════════════════════════════════════════════════════════════
#  Command handlers
# ══════════════════════════════════════════════════════════════════

_HELP = (
    "⚽ <b>Sports News Bot</b>\n\n"
    "Searches news for your teams in multiple languages and sends it "
    "translated — every 15 minutes.\n\n"
    "<b>Commands</b>\n"
    "/setlang — Pick your language\n"
    "/setteam — Pick a team step-by-step\n"
    "/teams   — Manage your teams (add / remove)\n"
    "/status  — Your current settings\n"
    "/latest  — Fetch news right now\n"
    "/stop    — Pause notifications\n"
    "/resume  — Resume notifications\n"
    "/help    — This message"
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await upsert_user(u.id, u.username or "", u.first_name or "")
    await update.message.reply_html(
        f"👋 Hello, <b>{u.first_name or 'there'}</b>!\n\n"
        "1️⃣ Tap <b>Choose language</b>\n"
        "2️⃣ Tap <b>Pick team</b> and follow the steps\n"
        "3️⃣ You can add up to 3 teams total\n\n"
        "Tap <b>/</b> at any time to see all commands.",
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
            f"❌ <code>{code}</code> is not supported. Pick from the list:",
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

    teams = await get_user_teams(u.id)
    if len(teams) >= MAX_TEAMS:
        await update.message.reply_html(
            f"⚠️ You already have {MAX_TEAMS} teams.\n\n"
            + _teams_text(teams),
            reply_markup=_my_teams_keyboard(teams),
        )
        return

    if not context.args:
        await update.message.reply_html(
            "⚽ <b>Pick your team</b>  (step 1 of 3)\n\n"
            "Choose the <b>country</b> of the league:",
            reply_markup=_country_keyboard(),
        )
        return

    # Direct shortcut: /setteam Arsenal
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


async def teams_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show and manage the user's tracked teams."""
    u = update.effective_user
    await upsert_user(u.id, u.username or "", u.first_name or "")
    teams = await get_user_teams(u.id)
    if not teams:
        await update.message.reply_html(
            "You have no teams yet. Use /setteam to add one.",
        )
        return
    await update.message.reply_html(
        _teams_text(teams),
        reply_markup=_my_teams_keyboard(teams),
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row = await get_user(update.effective_user.id)
    if not row:
        await update.message.reply_text("Send /start to set up the bot.")
        return
    lang   = row["language"] or "en"
    teams  = await get_user_teams(update.effective_user.id)
    teams_str = ", ".join(f"<b>{t}</b>" for t in teams) if teams else "None"
    status = "✅ Active" if row["active"] else "⏸ Paused"
    await update.message.reply_html(
        f"⚙️ <b>Your settings</b>\n\n"
        f"🌐 Language: <b>{lang_display(lang)}</b> (<code>{lang}</code>)\n"
        f"⚽ Teams ({len(teams)}/{MAX_TEAMS}): {teams_str}\n"
        f"📬 Status: {status}",
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
    teams = await get_user_teams(update.effective_user.id)
    if not teams:
        await update.message.reply_html("No teams set yet. Use /setteam to add one.")
        return
    lang = row["language"] or "en"
    await update.message.reply_text(
        f"🔍 Fetching news for {len(teams)} team(s): {', '.join(teams)}…"
    )
    total = 0
    for team in teams:
        try:
            total += await _process_group(
                team_name=team, target_lang=lang,
                user_ids=[row["user_id"]], bot=context.bot,
            )
        except Exception as exc:
            logger.error("latest_command team=%s user=%d: %s", team, row["user_id"], exc)
    if total == 0:
        await update.message.reply_text(
            "📭 No new articles found right now. I'll check again in 15 minutes!"
        )


# ══════════════════════════════════════════════════════════════════
#  Inline button callback
# ══════════════════════════════════════════════════════════════════

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data  = query.data or ""
    user  = query.from_user

    # ── Language ──────────────────────────────────────────────────
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

    # ── Direct /setteam Name confirm flow ─────────────────────────
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
            league=pending.get("sport", "Sports"),
        )
        return

    if data == "team:no":
        context.user_data.pop("pending_team", None)
        await query.edit_message_text(
            "❌ Cancelled. Use /setteam to try again.", parse_mode="HTML"
        )
        return

    # ── ts_direct: skip to text entry ────────────────────────────
    if data == "ts_direct":
        await query.edit_message_text(
            "✏️ Type: /setteam <code>Team Name</code>\n\n"
            "Example: /setteam <code>Arsenal</code>",
            parse_mode="HTML",
        )
        return

    # ── Step 1 → 2: country chosen ────────────────────────────────
    if data.startswith("ts_c:"):
        country = data[5:]
        teams = await get_user_teams(user.id)
        if len(teams) >= MAX_TEAMS:
            await query.edit_message_text(
                f"⚠️ You already have {MAX_TEAMS} teams.\n\n" + _teams_text(teams),
                parse_mode="HTML",
                reply_markup=_my_teams_keyboard(teams),
            )
            return
        leagues = get_football_leagues(country)   # instant — no API call
        if not leagues:
            await query.edit_message_text(
                f"❌ No leagues configured for {country}. Try another country:",
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

    # ── Step 2 → 3: league chosen (index-based) ───────────────────
    if data.startswith("ts_l:"):
        league_idx = int(data[5:])
        ts = context.user_data.get("ts", {})
        leagues = ts.get("leagues", [])
        if league_idx >= len(leagues):
            await query.edit_message_text("⚠️ Session expired. Use /setteam again.")
            return
        league = leagues[league_idx]
        await query.edit_message_text(f"⏳ Loading teams for {league['name']}…")
        teams = await get_league_teams(league)
        if not teams:
            country = ts.get("country", "")
            await query.edit_message_text(
                f"❌ Could not load teams for <b>{league['name']}</b>.\n\n"
                "The sports database may be temporarily unavailable.\n"
                "You can type the team name directly: /setteam <code>Team Name</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to divisions", callback_data="ts_bl"),
                ]]),
            )
            return
        ts.update({"league_idx": league_idx, "league_name": league["name"],
                   "teams": teams, "page": 0})
        context.user_data["ts"] = ts
        country = ts.get("country", "")
        flag    = COUNTRY_FLAGS.get(country, "🌍")
        await query.edit_message_text(
            f"⚽ <b>Pick your team</b>  (step 3 of 3)\n\n"
            f"{flag} {league['name']} — {len(teams)} teams:",
            parse_mode="HTML",
            reply_markup=_teams_keyboard(teams, 0),
        )
        return

    # ── Step 3: team chosen ───────────────────────────────────────
    if data.startswith("ts_t:"):
        idx   = int(data[5:])
        ts    = context.user_data.get("ts", {})
        teams = ts.get("teams", [])
        if idx >= len(teams):
            await query.edit_message_text("⚠️ Team not found. Please start over with /setteam.")
            return
        ts["selected_idx"] = idx
        context.user_data["ts"] = ts
        team    = teams[idx]
        country = team.get("country") or ts.get("country", "Unknown")
        flag    = COUNTRY_FLAGS.get(country, "🌍")
        league  = ts.get("league_name", "Unknown")
        await query.edit_message_text(
            f"⚽ <b>{team['name']}</b>\n"
            f"{flag} {country}\n"
            f"🏆 {league}\n\n"
            "Confirm this team?",
            parse_mode="HTML",
            reply_markup=_ts_confirm_keyboard(),
        )
        return

    # ── Pagination ────────────────────────────────────────────────
    if data.startswith("ts_pg:"):
        page  = int(data[6:])
        ts    = context.user_data.get("ts", {})
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

    # ── Confirm (ts flow) ─────────────────────────────────────────
    if data == "ts_ok":
        ts  = context.user_data.get("ts", {})
        idx = ts.get("selected_idx")
        teams_list = ts.get("teams", [])
        if idx is None or idx >= len(teams_list):
            await query.edit_message_text("⚠️ Session expired. Use /setteam again.")
            return
        team    = teams_list[idx]
        country = team.get("country") or ts.get("country", "Unknown")
        flag    = COUNTRY_FLAGS.get(country, "🌍")
        league  = ts.get("league_name", "Unknown")
        await _confirm_and_post(
            query=query, context=context, user_id=user.id,
            team_name=team["name"], country=country,
            flag=flag, league=league,
        )
        return

    # ── Back navigation ───────────────────────────────────────────
    if data == "ts_bt":  # back to team list
        ts   = context.user_data.get("ts", {})
        page = ts.get("page", 0)
        teams_list  = ts.get("teams", [])
        country     = ts.get("country", "")
        league_name = ts.get("league_name", "League")
        flag        = COUNTRY_FLAGS.get(country, "🌍")
        if not teams_list:
            await query.edit_message_text(
                "⚽ <b>Pick your team</b>  (step 1 of 3)\n\nChoose a country:",
                parse_mode="HTML", reply_markup=_country_keyboard(),
            )
            return
        await query.edit_message_text(
            f"⚽ <b>Pick your team</b>  (step 3 of 3)\n\n"
            f"{flag} {league_name} — {len(teams_list)} teams:",
            parse_mode="HTML",
            reply_markup=_teams_keyboard(teams_list, page),
        )
        return

    if data == "ts_bl":  # back to league list
        ts      = context.user_data.get("ts", {})
        leagues = ts.get("leagues", [])
        country = ts.get("country", "")
        flag    = COUNTRY_FLAGS.get(country, "🌍")
        if not leagues:
            await query.edit_message_text(
                "⚽ <b>Pick your team</b>  (step 1 of 3)\n\nChoose a country:",
                parse_mode="HTML", reply_markup=_country_keyboard(),
            )
            return
        await query.edit_message_text(
            f"⚽ <b>Pick your team</b>  (step 2 of 3)\n\n"
            f"{flag} <b>{country}</b> — choose a division:",
            parse_mode="HTML",
            reply_markup=_leagues_keyboard(leagues, country),
        )
        return

    if data == "ts_bc":  # back to country list
        context.user_data.pop("ts", None)
        await query.edit_message_text(
            "⚽ <b>Pick your team</b>  (step 1 of 3)\n\nChoose a country:",
            parse_mode="HTML", reply_markup=_country_keyboard(),
        )
        return

    # ── Post-confirm: add another or show my teams ────────────────
    if data == "ts_add_more":
        teams = await get_user_teams(user.id)
        if len(teams) >= MAX_TEAMS:
            await query.edit_message_text(
                f"⚠️ Maximum {MAX_TEAMS} teams reached.\n\n" + _teams_text(teams),
                parse_mode="HTML",
                reply_markup=_my_teams_keyboard(teams),
            )
            return
        await query.edit_message_text(
            "⚽ <b>Pick your next team</b>  (step 1 of 3)\n\nChoose a country:",
            parse_mode="HTML", reply_markup=_country_keyboard(),
        )
        return

    if data == "ts_show_teams":
        teams = await get_user_teams(user.id)
        await query.edit_message_text(
            _teams_text(teams),
            parse_mode="HTML",
            reply_markup=_my_teams_keyboard(teams),
        )
        return

    # ── Remove team ───────────────────────────────────────────────
    if data.startswith("rm_team:"):
        idx   = int(data[8:])
        teams = await get_user_teams(user.id)
        if idx >= len(teams):
            await query.edit_message_text("⚠️ Team not found. Try /teams again.")
            return
        removed = teams[idx]
        await remove_user_team(user.id, removed)
        updated = await get_user_teams(user.id)
        if updated:
            await query.edit_message_text(
                f"🗑 <b>{removed}</b> removed.\n\n" + _teams_text(updated),
                parse_mode="HTML",
                reply_markup=_my_teams_keyboard(updated),
            )
        else:
            await query.edit_message_text(
                f"🗑 <b>{removed}</b> removed.\n\nNo teams left. Use /setteam to add one.",
                parse_mode="HTML",
            )
        return

    # ── Quick-menu ────────────────────────────────────────────────
    if data == "menu:lang":
        await query.edit_message_text(
            "🌐 <b>Choose your language:</b>",
            parse_mode="HTML", reply_markup=_lang_keyboard(),
        )
        return

    if data == "menu:team":
        teams = await get_user_teams(user.id)
        if len(teams) >= MAX_TEAMS:
            await query.edit_message_text(
                f"⚠️ You already have {MAX_TEAMS} teams.\n\n" + _teams_text(teams),
                parse_mode="HTML", reply_markup=_my_teams_keyboard(teams),
            )
            return
        await query.edit_message_text(
            "⚽ <b>Pick your team</b>  (step 1 of 3)\n\nChoose a country:",
            parse_mode="HTML", reply_markup=_country_keyboard(),
        )
        return

    if data == "menu:status":
        row  = await get_user(user.id)
        if not row:
            await query.edit_message_text("Use /start to set up the bot.")
            return
        lang  = row["language"] or "en"
        teams = await get_user_teams(user.id)
        teams_str = ", ".join(f"<b>{t}</b>" for t in teams) if teams else "None"
        status = "✅ Active" if row["active"] else "⏸ Paused"
        await query.edit_message_text(
            f"⚙️ <b>Your settings</b>\n\n"
            f"🌐 Language: <b>{lang_display(lang)}</b> (<code>{lang}</code>)\n"
            f"⚽ Teams ({len(teams)}/{MAX_TEAMS}): {teams_str}\n"
            f"📬 Status: {status}",
            parse_mode="HTML",
            reply_markup=_status_keyboard(),
        )
        return

    if data == "menu:latest":
        row   = await get_user(user.id)
        teams = await get_user_teams(user.id)
        if not row or not teams:
            await query.edit_message_text(
                "Set a team first with /setteam", parse_mode="HTML"
            )
            return
        await query.edit_message_text(
            f"🔍 Fetching news for {', '.join(teams)}…"
        )
        lang  = row["language"] or "en"
        total = 0
        for team in teams:
            try:
                total += await _process_group(
                    team_name=team, target_lang=lang,
                    user_ids=[user.id], bot=context.bot,
                )
            except Exception as exc:
                logger.error("menu:latest team=%s user=%d: %s", team, user.id, exc)
        if total == 0:
            await context.bot.send_message(
                chat_id=user.id,
                text="📭 No new articles right now. Check back in 15 minutes!",
            )
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

import asyncio
import logging
from datetime import datetime

from telegram.ext import ContextTypes

from bot.database import (
    delete_old_news,
    get_all_active_user_teams,
    get_unsent_news,
    mark_news_sent,
    news_url_exists,
    save_news_item,
)
from bot.news_fetcher import fetch_team_news
from bot.translator import translate_text
from config.settings import settings

logger = logging.getLogger(__name__)


def _format_post(news_row, team_name: str) -> str:
    title   = news_row["translated_title"]   or news_row["original_title"]
    content = news_row["translated_content"] or ""
    source  = news_row["source_name"]        or "News"
    url     = news_row["source_url"]         or ""

    lines = [f"⚽ <b>{team_name}</b>", "", f"📰 <b>{title}</b>"]
    if content:
        lines += ["", content]
    lines += ["", f"📡 {source}"]
    if url:
        lines.append(f'🔗 <a href="{url}">Read more</a>')

    raw_ts = news_row["published_at"]
    if raw_ts:
        try:
            dt = datetime.fromisoformat(str(raw_ts))
            lines.append(f"🕐 {dt.strftime('%d.%m.%Y %H:%M')} UTC")
        except ValueError:
            pass

    return "\n".join(lines)


async def _process_group(
    team_name: str,
    target_lang: str,
    user_ids: list,
    bot,
) -> int:
    """Fetch, translate, cache, and deliver news for one (team, lang) group.

    Returns the total number of messages sent across all users.
    """
    logger.info("Fetching news | team=%s | lang=%s | users=%d",
                team_name, target_lang, len(user_ids))

    news_items = await fetch_team_news(team_name)

    # Only translate items not already cached — avoids redundant API calls
    for item in news_items[:15]:
        if await news_url_exists(item["url"], target_lang):
            continue                       # already translated and stored

        orig_lang    = item["original_lang"]
        orig_title   = item["title"]
        orig_content = item["content"]

        if orig_lang == target_lang:
            trans_title, trans_content = orig_title, orig_content
        else:
            trans_title = await translate_text(orig_title, orig_lang, target_lang)
            trans_content = (
                await translate_text(orig_content, orig_lang, target_lang)
                if orig_content else ""
            )
            await asyncio.sleep(0.2)    # gentle rate-limit on translator

        await save_news_item(
            team_name=team_name,
            original_lang=orig_lang,
            original_title=orig_title,
            original_content=orig_content,
            translated_title=trans_title,
            translated_content=trans_content,
            target_lang=target_lang,
            source_url=item["url"],
            source_name=item["source_name"],
            published_at=item["published_at"],
        )

    total_sent = 0
    for user_id in user_ids:
        unsent = await get_unsent_news(
            user_id=user_id,
            team_name=team_name,
            target_lang=target_lang,
            limit=settings.MAX_NEWS_PER_CHECK,
        )
        for row in unsent:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=_format_post(row, team_name),
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                )
                await mark_news_sent(user_id, row["id"])
                await asyncio.sleep(0.5)
                total_sent += 1
            except Exception as exc:
                logger.error("Failed to send news to user %d: %s", user_id, exc)
    return total_sent


async def check_and_send_news(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: runs every NEWS_CHECK_INTERVAL seconds."""
    logger.info("=== Scheduled news check started ===")
    try:
        rows = await get_all_active_user_teams()
        if not rows:
            logger.info("No active users with teams configured — skipping")
            return

        # Group (team_name, language) → [user_ids] to avoid duplicate fetches
        groups = {}
        for r in rows:
            key = (r["team_name"].lower(), r["language"])
            groups.setdefault(key, []).append(r["user_id"])

        for (team, lang), uids in groups.items():
            await _process_group(team, lang, uids, context.bot)

    except Exception:
        logger.exception("Unhandled error in check_and_send_news")
    finally:
        logger.info("=== Scheduled news check complete ===")


async def cleanup_old_news(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily job: remove news older than 7 days."""
    logger.info("Running daily news cleanup")
    try:
        await delete_old_news(days=7)
    except Exception:
        logger.exception("Error in cleanup_old_news")

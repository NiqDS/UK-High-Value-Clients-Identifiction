import asyncio
import hashlib
import logging
from datetime import datetime

from telegram.ext import ContextTypes

from bot.database import (
    delete_old_news,
    get_all_active_user_teams,
    get_unsent_news,
    mark_news_sent,
    news_title_hash_exists,
    news_url_exists,
    save_news_item,
)
from bot.news_fetcher import fetch_article_image, fetch_team_news
from bot.translator import translate_text
from config.settings import settings

logger = logging.getLogger(__name__)


def _title_hash(team: str, title: str) -> str:
    raw = f"{team.lower()}|{title.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _format_text(news_row, team_name: str) -> str:
    """Plain-text post used when no image is available."""
    title  = news_row["translated_title"] or news_row["original_title"]
    source = news_row["source_name"]      or "News"
    url    = news_row["source_url"]       or ""

    lines = [f"⚽ <b>{team_name}</b>", f"📰 <b>{title}</b>", ""]
    lines.append(f"📡 {source}")
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


def _format_caption(news_row, team_name: str) -> str:
    """Caption for send_photo — identical structure, capped at Telegram's 1024-char limit."""
    return _format_text(news_row, team_name)[:1024]


async def _send_news_item(bot, chat_id: int, row, team_name: str) -> None:
    """Send one news item as a photo+caption or plain text."""
    image_url = (row["image_url"] or "") if "image_url" in row.keys() else ""
    if image_url:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=image_url,
                caption=_format_caption(row, team_name),
                parse_mode="HTML",
            )
            return
        except Exception as exc:
            logger.debug("send_photo failed (%s), falling back to text: %s", image_url, exc)
    # No image or photo send failed — send text without link preview so the
    # article title is not repeated by Telegram's own preview card.
    await bot.send_message(
        chat_id=chat_id,
        text=_format_text(row, team_name),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


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

    # Only process items not already in the cache.
    # Two checks: exact URL (fast) and normalised title hash (catches the same
    # story arriving via different Google News redirect URLs).
    for item in news_items[:15]:
        if await news_url_exists(item["url"], target_lang):
            continue

        t_hash = _title_hash(team_name, item["title"])
        if await news_title_hash_exists(t_hash, team_name, target_lang):
            continue

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

        # Fetch og:image from the real article (follows Google News redirect).
        # Done once per new article; result is stored in the cache.
        image_url = await fetch_article_image(item["url"])

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
            image_url=image_url,
            title_hash=t_hash,
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
                await _send_news_item(bot, user_id, row, team_name)
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

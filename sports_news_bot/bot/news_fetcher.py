import asyncio
import logging
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import aiohttp
import feedparser
from bs4 import BeautifulSoup

from config.settings import settings

logger = logging.getLogger(__name__)

_GOOGLE_NEWS_PARAMS: Dict[str, tuple] = {
    "en": ("en-US", "US", "en"),
    "ru": ("ru",    "RU", "ru"),
    "de": ("de",    "DE", "de"),
    "es": ("es-419","US", "es"),
    "fr": ("fr",    "FR", "fr"),
    "it": ("it",    "IT", "it"),
    "pt": ("pt-BR", "BR", "pt"),
    "nl": ("nl",    "NL", "nl"),
    "tr": ("tr",    "TR", "tr"),
    "ar": ("ar",    "AE", "ar"),
    "ja": ("ja",    "JP", "ja"),
    "zh": ("zh-CN", "CN", "zh"),
    "ko": ("ko",    "KR", "ko"),
    "pl": ("pl",    "PL", "pl"),
    "uk": ("uk",    "UA", "uk"),
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

_FETCH_TIMEOUT   = 25   # seconds per RSS request
_FETCH_RETRIES   = 3
_RETRY_BACKOFF   = (2, 5, 10)  # seconds between retries


def _google_news_url(query: str, lang_code: str) -> str:
    hl, gl, ceid_lang = _GOOGLE_NEWS_PARAMS.get(lang_code, _GOOGLE_NEWS_PARAMS["en"])
    q = quote_plus(f"{query} sport")
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={gl}:{ceid_lang}"


def _strip_html(text: str, max_len: int = 400) -> str:
    if not text:
        return ""
    return BeautifulSoup(text, "lxml").get_text(separator=" ", strip=True)[:max_len]


def _extract_image(entry: Any) -> str:
    """Extract the first usable image URL from a feed entry."""
    for attr in ("media_thumbnail", "media_content"):
        items = getattr(entry, attr, None)
        if items and isinstance(items, list):
            url = items[0].get("url", "")
            if url:
                return url
    for enc in getattr(entry, "enclosures", []):
        if enc.get("type", "").startswith("image/"):
            return enc.get("href", "") or enc.get("url", "")
    # fall back to first <img> in summary HTML
    summary = getattr(entry, "summary", "") or ""
    if "<img" in summary:
        img = BeautifulSoup(summary, "lxml").find("img")
        if img and img.get("src"):
            return img["src"]
    return ""


def _parse_date(entry: Any) -> datetime:
    try:
        raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
        if raw:
            return parsedate_to_datetime(raw).replace(tzinfo=None)
    except Exception:
        pass
    return datetime.utcnow()


async def _fetch_feed(url: str, session: aiohttp.ClientSession) -> List[Any]:
    """Fetch a single RSS feed with up to 3 retries and exponential backoff."""
    for attempt in range(_FETCH_RETRIES):
        try:
            async with session.get(
                url, headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=_FETCH_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    return feedparser.parse(await resp.text()).entries
                logger.debug("Feed %s returned HTTP %s", url, resp.status)
                return []
        except (asyncio.TimeoutError, aiohttp.ServerTimeoutError):
            wait = _RETRY_BACKOFF[attempt]
            if attempt < _FETCH_RETRIES - 1:
                logger.warning("Timeout on %s (attempt %d/%d) — retrying in %ds",
                               url, attempt + 1, _FETCH_RETRIES, wait)
                await asyncio.sleep(wait)
            else:
                logger.error("Feed %s timed out after %d attempts", url, _FETCH_RETRIES)
        except aiohttp.ClientError as exc:
            logger.warning("Network error on %s: %s", url, exc)
            break
        except Exception as exc:
            logger.warning("Unexpected error fetching %s: %s", url, exc)
            break
    return []


async def fetch_team_news(
    team_name: str,
    search_languages: Optional[List[str]] = None,
) -> List[Dict]:
    """Fetch news about *team_name* from Google News RSS in multiple languages.

    Returns a deduplicated list sorted by published_at descending (max 25 items).
    """
    if search_languages is None:
        search_languages = settings.NEWS_SEARCH_LANGUAGES

    seen_urls: set = set()
    all_news: List[Dict] = []

    async with aiohttp.ClientSession() as session:
        tasks = {
            lang: _fetch_feed(_google_news_url(team_name, lang), session)
            for lang in search_languages
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for lang, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.warning("Feed gather error (%s): %s", lang, result)
                continue

            for entry in result[:12]:
                url = getattr(entry, "link", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                title = _strip_html(getattr(entry, "title", ""), 200)
                content = _strip_html(
                    getattr(entry, "summary", "") or getattr(entry, "description", ""), 400
                )
                src = getattr(entry, "source", {})
                source_name = src.get("title", "News") if isinstance(src, dict) else "News"

                if len(title) < 5:
                    continue

                all_news.append({
                    "title":         title,
                    "content":       content,
                    "url":           url,
                    "source_name":   source_name,
                    "published_at":  _parse_date(entry),
                    "original_lang": lang,
                    "image_url":     _extract_image(entry),
                })

    all_news.sort(key=lambda x: x["published_at"], reverse=True)
    return all_news[:25]

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

# Maps ISO 639-1 language codes → Google News RSS params (hl, gl, ceid_lang)
_GOOGLE_NEWS_PARAMS: dict[str, tuple[str, str, str]] = {
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
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


def _google_news_url(query: str, lang_code: str) -> str:
    hl, gl, ceid_lang = _GOOGLE_NEWS_PARAMS.get(
        lang_code, _GOOGLE_NEWS_PARAMS["en"]
    )
    q = quote_plus(f"{query} sport")
    return (
        f"https://news.google.com/rss/search"
        f"?q={q}&hl={hl}&gl={gl}&ceid={gl}:{ceid_lang}"
    )


def _strip_html(text: str, max_len: int = 400) -> str:
    if not text:
        return ""
    return BeautifulSoup(text, "lxml").get_text(separator=" ", strip=True)[:max_len]


def _parse_date(entry: Any) -> datetime:
    try:
        raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
        if raw:
            return parsedate_to_datetime(raw).replace(tzinfo=None)
    except Exception:
        pass
    return datetime.utcnow()


async def _fetch_feed(
    url: str,
    session: aiohttp.ClientSession,
) -> List[Any]:
    try:
        async with session.get(
            url,
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                return feedparser.parse(await resp.text()).entries
    except asyncio.TimeoutError:
        logger.warning("Timeout fetching %s", url)
    except Exception as exc:
        logger.warning("Error fetching %s: %s", url, exc)
    return []


async def fetch_team_news(
    team_name: str,
    search_languages: Optional[List[str]] = None,
) -> List[Dict]:
    """Fetch news about *team_name* from Google News RSS in multiple languages.

    Returns a deduplicated list of dicts sorted by published_at descending.
    """
    if search_languages is None:
        search_languages = settings.NEWS_SEARCH_LANGUAGES

    seen_urls: set[str] = set()
    all_news: list[dict] = []

    async with aiohttp.ClientSession() as session:
        tasks = {
            lang: _fetch_feed(_google_news_url(team_name, lang), session)
            for lang in search_languages
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for lang, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.warning("Failed to fetch %s feed: %s", lang, result)
                continue

            for entry in result[:12]:
                url = getattr(entry, "link", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                title = _strip_html(getattr(entry, "title", ""), 200)
                content = _strip_html(
                    getattr(entry, "summary", "") or getattr(entry, "description", ""),
                    400,
                )
                src = getattr(entry, "source", {})
                source_name = (
                    src.get("title", "News") if isinstance(src, dict) else "News"
                )

                if len(title) < 5:
                    continue

                all_news.append({
                    "title":         title,
                    "content":       content,
                    "url":           url,
                    "source_name":   source_name,
                    "published_at":  _parse_date(entry),
                    "original_lang": lang,
                })

    all_news.sort(key=lambda x: x["published_at"], reverse=True)
    return all_news[:30]

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from deep_translator import GoogleTranslator
from deep_translator.exceptions import LanguageNotSupportedException, TranslationNotFound

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES: dict = {
    "en": "English",
    "ru": "Русский",
    "de": "Deutsch",
    "es": "Español",
    "fr": "Français",
    "it": "Italiano",
    "pt": "Português",
    "nl": "Nederlands",
    "tr": "Türkçe",
    "ar": "العربية",
    "ja": "日本語",
    "zh": "中文",
    "ko": "한국어",
    "pl": "Polski",
    "uk": "Українська",
}

_executor  = ThreadPoolExecutor(max_workers=4)
_RETRIES   = 3
_BACKOFF   = (1, 3, 6)      # seconds between retries
_ASYNC_TIMEOUT = 45.0       # seconds before asyncio gives up on the thread


def _translate_sync(text: str, source: str, target: str) -> str:
    """Blocking translation with up to 3 retries."""
    for attempt in range(_RETRIES):
        try:
            result = GoogleTranslator(source=source, target=target).translate(text)
            return result or text
        except (TranslationNotFound, LanguageNotSupportedException) as exc:
            logger.warning("Translation unsupported (%s→%s): %s", source, target, exc)
            return text          # no point retrying unsupported pairs
        except Exception as exc:
            wait = _BACKOFF[attempt]
            if attempt < _RETRIES - 1:
                logger.debug("Translation retry %d/%d (%s→%s) in %ds: %s",
                             attempt + 1, _RETRIES, source, target, wait, exc)
                time.sleep(wait)
            else:
                logger.error("Translation failed after %d attempts (%s→%s): %s",
                             _RETRIES, source, target, exc)
    return text


async def translate_text(text: str, source: str = "auto", target: str = "en") -> str:
    if not text:
        return text
    if source == target and source != "auto":
        return text
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_executor, _translate_sync, text, source, target),
            timeout=_ASYNC_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error("translate_text timed out after %.0fs (%s→%s)", _ASYNC_TIMEOUT, source, target)
        return text
    except Exception as exc:
        logger.error("translate_text unexpected error (%s→%s): %s", source, target, exc)
        return text


def is_supported(lang_code: str) -> bool:
    return lang_code.lower() in SUPPORTED_LANGUAGES


def lang_display(lang_code: str) -> str:
    return SUPPORTED_LANGUAGES.get(lang_code.lower(), lang_code.upper())

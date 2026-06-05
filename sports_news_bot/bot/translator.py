import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from deep_translator import GoogleTranslator
from deep_translator.exceptions import LanguageNotSupportedException, TranslationNotFound

logger = logging.getLogger(__name__)

# Language code → display name
SUPPORTED_LANGUAGES: dict[str, str] = {
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

_executor = ThreadPoolExecutor(max_workers=4)


def _translate_sync(text: str, source: str, target: str) -> str:
    try:
        return GoogleTranslator(source=source, target=target).translate(text) or text
    except (TranslationNotFound, LanguageNotSupportedException) as exc:
        logger.warning("Translation unavailable (%s→%s): %s", source, target, exc)
        return text
    except Exception as exc:
        logger.error("Translation error (%s→%s): %s", source, target, exc)
        return text


async def translate_text(text: str, source: str = "auto", target: str = "en") -> str:
    if not text:
        return text
    if source == target and source != "auto":
        return text
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _translate_sync, text, source, target)


def is_supported(lang_code: str) -> bool:
    return lang_code.lower() in SUPPORTED_LANGUAGES


def lang_display(lang_code: str) -> str:
    return SUPPORTED_LANGUAGES.get(lang_code.lower(), lang_code.upper())

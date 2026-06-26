"""Environment variables and application constants."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --- Telegram ---------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# --- Intervals --------------------------------------------------------------
DEFAULT_CHECK_INTERVAL_MINUTES: int = _get_int("DEFAULT_CHECK_INTERVAL_MINUTES", 60)
MIN_CHECK_INTERVAL_MINUTES: int = _get_int("MIN_CHECK_INTERVAL_MINUTES", 5)

# --- Limits -----------------------------------------------------------------
MAX_MONITORED_ITEMS_PER_USER: int = _get_int("MAX_MONITORED_ITEMS_PER_USER", 10)

# --- Storage ----------------------------------------------------------------
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "price_monitor.db").strip()

# --- Scraping ---------------------------------------------------------------
# Number of consecutive failed checks before warning the user.
MAX_CONSECUTIVE_FAILURES: int = _get_int("MAX_CONSECUTIVE_FAILURES", 5)

# Per-request timeout (seconds).
REQUEST_TIMEOUT_SECONDS: int = _get_int("REQUEST_TIMEOUT_SECONDS", 20)

# A small pool of user agents to rotate through to reduce bot detection.
USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

# --- Playwright fallback (for JavaScript-rendered pages) --------------------
# When the static httpx + BeautifulSoup pass finds no price, optionally fall
# back to rendering the page in a headless browser via Playwright.
USE_PLAYWRIGHT_FALLBACK: bool = _get_bool("USE_PLAYWRIGHT_FALLBACK", True)

# Optional explicit path to a Chromium executable. Leave empty to let
# Playwright locate its own managed browser. Some managed environments ship a
# pre-installed Chromium (e.g. /opt/pw-browsers/chromium) — set this to that
# path to avoid downloading browsers.
PLAYWRIGHT_EXECUTABLE_PATH: str = os.getenv("PLAYWRIGHT_EXECUTABLE_PATH", "").strip()

# How long (seconds) to allow a single rendered page load.
PLAYWRIGHT_TIMEOUT_SECONDS: int = _get_int("PLAYWRIGHT_TIMEOUT_SECONDS", 30)


def validate() -> None:
    """Raise a helpful error if required config is missing."""
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and "
            "fill in your bot token from @BotFather."
        )
    if MIN_CHECK_INTERVAL_MINUTES < 1:
        raise RuntimeError("MIN_CHECK_INTERVAL_MINUTES must be >= 1.")

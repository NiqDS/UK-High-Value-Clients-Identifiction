"""Playwright fallback for JavaScript-rendered pages.

Used only when the static (httpx + BeautifulSoup) pass fails to find a price.
Playwright is an optional dependency: if it isn't installed, ``is_available()``
returns False and the caller simply skips the fallback.
"""
from __future__ import annotations

import logging
import os
import random
from typing import Optional

from config import (
    PLAYWRIGHT_EXECUTABLE_PATH,
    PLAYWRIGHT_TIMEOUT_SECONDS,
    USER_AGENTS,
)

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright  # noqa: F401

    _PLAYWRIGHT_IMPORTED = True
except ImportError:  # pragma: no cover - optional dependency
    _PLAYWRIGHT_IMPORTED = False


def is_available() -> bool:
    """True if the playwright package is importable."""
    return _PLAYWRIGHT_IMPORTED


def _resolve_executable_path() -> Optional[str]:
    """Pick a Chromium executable, preferring an explicit/pre-installed one."""
    if PLAYWRIGHT_EXECUTABLE_PATH:
        return PLAYWRIGHT_EXECUTABLE_PATH
    # Common location for a pre-installed Chromium in managed environments.
    default = "/opt/pw-browsers/chromium"
    if os.path.exists(default):
        return default
    # Fall back to Playwright's own managed browser.
    return None


async def fetch_rendered_html(url: str) -> Optional[str]:
    """Render ``url`` in headless Chromium and return its HTML.

    Returns None if Playwright is unavailable or the render fails.
    """
    if not _PLAYWRIGHT_IMPORTED:
        return None

    from playwright.async_api import async_playwright

    executable_path = _resolve_executable_path()
    timeout_ms = PLAYWRIGHT_TIMEOUT_SECONDS * 1000

    try:
        async with async_playwright() as p:
            launch_kwargs: dict = {"headless": True}
            if executable_path:
                launch_kwargs["executable_path"] = executable_path

            browser = await p.chromium.launch(**launch_kwargs)
            try:
                context = await browser.new_context(
                    user_agent=random.choice(USER_AGENTS),
                    locale="en-GB",
                )
                page = await context.new_page()
                await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                # Give late client-side rendering a chance to settle.
                try:
                    await page.wait_for_load_state("networkidle", timeout=timeout_ms)
                except Exception:  # networkidle can time out on busy pages
                    logger.debug("networkidle wait timed out for %s", url)
                return await page.content()
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 - fallback must never raise
        logger.warning("Playwright render failed for %s: %s", url, exc)
        return None

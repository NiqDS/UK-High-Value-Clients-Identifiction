"""Price extraction logic.

Fetches a page with httpx (rotating user agents) and tries a series of
strategies to find an item name and price:

1. JSON-LD structured data (schema.org/Product offers).
2. Common meta tags (Open Graph / product price).
3. Elements whose class/id hint at a price ("price", "cost", "amount").
4. A regex sweep for currency-prefixed numbers anywhere in the visible text.
"""
from __future__ import annotations

import json
import logging
import random
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from config import REQUEST_TIMEOUT_SECONDS, USER_AGENTS
from models import ScrapeResult

logger = logging.getLogger(__name__)

# Currency symbols we recognise, longest-first so multi-char ones win.
CURRENCY_SYMBOLS = ["$", "€", "£", "¥"]
_CURRENCY_CLASS = "".join(re.escape(c) for c in CURRENCY_SYMBOLS)

# A price like "£1,299.00", "$1299", "€ 19,99" (handles , and . separators).
_PRICE_RE = re.compile(
    rf"([{_CURRENCY_CLASS}])\s*([0-9][0-9.,\s]*[0-9]|[0-9])"
)

# Hints used to find price-bearing elements by class/id.
_PRICE_HINTS = ("price", "cost", "amount")


class ScrapeError(Exception):
    """Raised when a page could not be fetched or parsed."""


def _clean_price(raw: str) -> Optional[float]:
    """Turn a messy price string into a float, or None if impossible.

    Handles both ``1,299.00`` (US) and ``1.299,00`` (EU) groupings.
    """
    s = raw.strip()
    # Drop everything except digits, dot and comma.
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        return None

    if "," in s and "." in s:
        # Whichever separator appears last is the decimal separator.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # Comma only: decimal if it looks like cents (exactly 2 trailing digits).
        if re.search(r",\d{2}$", s) and s.count(",") == 1:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    # else: dot only or plain digits — leave as is.

    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _detect_currency(text: str) -> str:
    for sym in CURRENCY_SYMBOLS:
        if sym in text:
            return sym
    return ""


async def fetch_html(url: str) -> str:
    """Fetch a page, raising ScrapeError on any failure."""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers=headers,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPStatusError as exc:
        raise ScrapeError(
            f"HTTP {exc.response.status_code} fetching {url}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ScrapeError(f"Network error fetching {url}: {exc}") from exc


# --- Extraction strategies --------------------------------------------------


def _from_json_ld(soup: BeautifulSoup) -> Optional[ScrapeResult]:
    for tag in soup.find_all("script", type="application/ld+json"):
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string)
        except (json.JSONDecodeError, TypeError):
            continue

        for node in _iter_json_nodes(data):
            if not isinstance(node, dict):
                continue
            type_ = node.get("@type", "")
            types = type_ if isinstance(type_, list) else [type_]
            if not any("product" in str(t).lower() for t in types):
                continue

            name = node.get("name")
            offers = node.get("offers")
            offer = None
            if isinstance(offers, list) and offers:
                offer = offers[0]
            elif isinstance(offers, dict):
                offer = offers
            if not isinstance(offer, dict):
                continue

            price_raw = offer.get("price") or offer.get("lowPrice")
            if price_raw is None:
                continue
            price = _clean_price(str(price_raw))
            if price is None:
                continue
            currency = _currency_from_code(offer.get("priceCurrency", ""))
            return ScrapeResult(
                item_name=str(name) if name else "Unknown item",
                price=price,
                currency_symbol=currency,
            )
    return None


def _iter_json_nodes(data):
    """Yield every dict/list node in a nested JSON structure."""
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from _iter_json_nodes(value)
    elif isinstance(data, list):
        for item in data:
            yield from _iter_json_nodes(item)


def _currency_from_code(code: str) -> str:
    mapping = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥"}
    return mapping.get(str(code).upper(), "")


def _from_meta(soup: BeautifulSoup) -> Optional[ScrapeResult]:
    price_meta = soup.find("meta", attrs={"property": "product:price:amount"}) or soup.find(
        "meta", attrs={"property": "og:price:amount"}
    )
    if not price_meta or not price_meta.get("content"):
        return None
    price = _clean_price(price_meta["content"])
    if price is None:
        return None

    currency_meta = soup.find(
        "meta", attrs={"property": "product:price:currency"}
    ) or soup.find("meta", attrs={"property": "og:price:currency"})
    currency = _currency_from_code(currency_meta["content"]) if currency_meta and currency_meta.get("content") else ""

    name = _extract_name(soup)
    return ScrapeResult(item_name=name, price=price, currency_symbol=currency)


def _from_price_elements(soup: BeautifulSoup) -> Optional[ScrapeResult]:
    candidates = []
    for hint in _PRICE_HINTS:
        pattern = re.compile(hint, re.IGNORECASE)
        candidates.extend(soup.find_all(attrs={"class": pattern}))
        candidates.extend(soup.find_all(attrs={"id": pattern}))
        candidates.extend(soup.find_all(attrs={"itemprop": pattern}))

    seen = set()
    for el in candidates:
        if id(el) in seen:
            continue
        seen.add(id(el))
        text = el.get_text(" ", strip=True)
        match = _PRICE_RE.search(text)
        if not match:
            # Some sites put the value in a content/value attribute.
            for attr in ("content", "value", "data-price"):
                if el.get(attr):
                    price = _clean_price(str(el[attr]))
                    if price is not None:
                        return ScrapeResult(
                            item_name=_extract_name(soup),
                            price=price,
                            currency_symbol=_detect_currency(text),
                        )
            continue
        price = _clean_price(match.group(2))
        if price is None:
            continue
        return ScrapeResult(
            item_name=_extract_name(soup),
            price=price,
            currency_symbol=match.group(1),
        )
    return None


def _from_text_sweep(soup: BeautifulSoup) -> Optional[ScrapeResult]:
    text = soup.get_text(" ", strip=True)
    match = _PRICE_RE.search(text)
    if not match:
        return None
    price = _clean_price(match.group(2))
    if price is None:
        return None
    return ScrapeResult(
        item_name=_extract_name(soup),
        price=price,
        currency_symbol=match.group(1),
    )


def _extract_name(soup: BeautifulSoup) -> str:
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return og["content"].strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    return "Unknown item"


def extract_price(html: str) -> Optional[ScrapeResult]:
    """Run all strategies in order; return the first success."""
    soup = BeautifulSoup(html, "html.parser")
    for strategy in (_from_json_ld, _from_meta, _from_price_elements, _from_text_sweep):
        try:
            result = strategy(soup)
        except Exception:  # a single bad strategy shouldn't abort the rest
            logger.debug("Price strategy %s failed", strategy.__name__, exc_info=True)
            result = None
        if result is not None:
            return result
    return None


async def scrape(url: str) -> Optional[ScrapeResult]:
    """Fetch ``url`` and extract a price. Returns None if no price found.

    Raises ScrapeError if the page itself could not be fetched.
    """
    html = await fetch_html(url)
    return extract_price(html)

"""Startup self-check: warn loudly if the API key looks like it can withdraw.

The bot must use a TRADE + READ key with **withdrawals disabled**, and never
calls withdrawal endpoints. Key-permission introspection is not part of ccxt's
unified API and varies by venue, so this is best-effort: when the venue exposes
permissions we inspect them; otherwise we emit a prominent reminder. It returns
False only when withdrawal capability is positively detected.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_REMINDER = (
    "SECURITY: use a TRADE+READ API key with WITHDRAWALS DISABLED. "
    "This bot never withdraws; keep withdrawals a manual, 2FA action in the exchange UI."
)


_TRUTHY = {"true", "1", "enabled", "enable", "allow", "allowed", "yes", "on"}


def _is_truthy(value: Any) -> bool:
    if value is True or value == 1:
        return True
    return str(value).strip().lower() in _TRUTHY


def _looks_like_withdraw_permission(perms: Any) -> bool:
    """Structurally detect an *enabled* withdrawal scope in a permissions payload.

    Looks for a key mentioning withdraw/transfer whose value is truthy, recursing
    into nested dicts/lists. Returns False for shapes it cannot parse, rather than
    false-blocking (a manual-verify reminder is logged separately)."""
    if isinstance(perms, dict):
        for k, v in perms.items():
            key = str(k).lower()
            if ("withdraw" in key or "transfer" in key) and _is_truthy(v):
                return True
            if isinstance(v, (dict, list)) and _looks_like_withdraw_permission(v):
                return True
        return False
    if isinstance(perms, list):
        return any(_looks_like_withdraw_permission(x) for x in perms)
    return False


async def trade_only_key_self_check(adapter) -> bool:
    """Return False if withdrawal capability is positively detected, else True.

    Always logs the trade-only reminder.
    """
    logger.warning(_REMINDER)
    client = getattr(adapter, "_client", None)
    if client is None:
        return True

    # Some venues expose a private permissions endpoint via ccxt.
    fetch_perms = getattr(client, "fetch_permissions", None) or getattr(
        client, "fetchPermissions", None
    )
    if callable(fetch_perms):
        try:
            perms = await fetch_perms()
            if _looks_like_withdraw_permission(perms):
                logger.error(
                    "API KEY APPEARS TO HAVE WITHDRAWAL PERMISSION — refuse to run live. "
                    "Recreate the key without withdrawal scope."
                )
                return False
            logger.info("Self-check: key permissions look trade-only.")
        except Exception:
            logger.info("Self-check: could not read key permissions (venue may not expose them).")
    else:
        logger.info(
            "Self-check: venue does not expose key permissions via ccxt — "
            "verify manually that withdrawals are disabled on this key."
        )
    return True

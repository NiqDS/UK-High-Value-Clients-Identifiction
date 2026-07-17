"""Structured logging with secret redaction.

Anything registered via :func:`register_secret` is scrubbed from every log
record's message and args, so API keys / tokens can never leak to file or stdout.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

# Secret literal values to redact wherever they appear in log output.
_SECRETS: set[str] = set()
_REDACTED = "***REDACTED***"


def register_secret(value: str | None) -> None:
    """Register a literal secret value to be scrubbed from all log output."""
    if value:
        _SECRETS.add(value)


def _scrub(text: str) -> str:
    for secret in _SECRETS:
        if secret and secret in text:
            text = text.replace(secret, _REDACTED)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _scrub(str(record.msg))
            if record.args:
                # Scrub only STRING args. Secrets are strings; coercing every arg
                # to str would turn ints/floats into strings and break any message
                # using %d/%f (e.g. httpx's 'HTTP Request: ... %d ...' status code).
                if isinstance(record.args, dict):
                    record.args = {
                        k: (_scrub(v) if isinstance(v, str) else v)
                        for k, v in record.args.items()
                    }
                else:
                    record.args = tuple(
                        _scrub(a) if isinstance(a, str) else a for a in record.args
                    )
        except Exception:  # logging must never raise
            pass
        return True


class PlainRedactingFormatter(logging.Formatter):
    """Plain-text formatter that scrubs the FINAL formatted line — including
    exception tracebacks. The args-level filter cannot reach exc_info text, and
    third-party tracebacks (e.g. telegram/httpx network errors) embed the full
    request URL with the bot token in the stack trace."""

    def format(self, record: logging.LogRecord) -> str:
        return _scrub(super().format(record))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": _scrub(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = _scrub(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO", json_output: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactionFilter())
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            PlainRedactingFormatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Quiet chatty dependencies: httpx/httpcore log every HTTP request at INFO
    # (one line per Telegram poll ~1/s) and those lines also echo the bot token
    # in the URL. Keep only their warnings/errors.
    for noisy in ("httpx", "httpcore", "telegram", "telegram.ext", "hpack", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

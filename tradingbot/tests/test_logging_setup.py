"""Redaction filter must scrub secrets WITHOUT breaking numeric log formatting.

Regression: the filter used to coerce every arg to str, turning ints into
strings so any message using %d/%f (e.g. httpx's 'HTTP Request ... %d ...'
status line) crashed the logging formatter under a flood of tracebacks.
"""

from __future__ import annotations

import logging

from tradingbot.logging_setup import RedactionFilter, register_secret, setup_logging


def _record(msg, *args):
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)


def test_numeric_args_survive_for_percent_d() -> None:
    rec = _record('HTTP Request: %s %s "%s %d %s"', "POST", "url", "HTTP/1.1", 200, "OK")
    RedactionFilter().filter(rec)
    # the int stays an int, so %d formatting works (this used to raise TypeError)
    assert rec.getMessage() == 'HTTP Request: POST url "HTTP/1.1 200 OK"'


def test_float_args_survive_for_percent_f() -> None:
    rec = _record("price %.2f", 100.5)
    RedactionFilter().filter(rec)
    assert rec.getMessage() == "price 100.50"


def test_string_secret_is_redacted() -> None:
    register_secret("SUPERSECRET")
    rec = _record("connecting with key %s and code %d", "SUPERSECRET", 42)
    RedactionFilter().filter(rec)
    out = rec.getMessage()
    assert "SUPERSECRET" not in out
    assert "42" in out  # numeric arg preserved alongside redaction


def test_setup_quiets_httpx_logger() -> None:
    setup_logging("INFO")
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("telegram.ext").level == logging.WARNING

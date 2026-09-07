"""Telegram approval workflow + runtime controls.

The Telegram transport (`telegram_bot`) is imported lazily by the app so the
package does not require python-telegram-bot for the rest of the bot or tests.
"""

from .controls import JsonOverrideStore, SettingsController, is_authorized
from .manager import ApprovalManager, ApprovalRequest, Notifier
from .messages import parse_callback, render_approval_message, render_settings
from .status import StatusReporter

__all__ = [
    "ApprovalManager",
    "ApprovalRequest",
    "Notifier",
    "SettingsController",
    "JsonOverrideStore",
    "is_authorized",
    "StatusReporter",
    "parse_callback",
    "render_approval_message",
    "render_settings",
]

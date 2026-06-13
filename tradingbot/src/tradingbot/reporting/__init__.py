"""Performance reporting (weekly trades log vs market benchmark)."""

from .email_sender import EmailSender, build_email_sender
from .weekly import WeeklyReporter, WeeklyStats

__all__ = ["WeeklyReporter", "WeeklyStats", "EmailSender", "build_email_sender"]

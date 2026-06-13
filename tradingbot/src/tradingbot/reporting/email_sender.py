"""Send the weekly report by email over SMTP (e.g. Gmail with an App Password).

Synchronous (``smtplib``); the runner calls it off the event loop via a thread.
Credentials come from the environment (never logged).
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from ..config import ReportingConfig, Secrets

logger = logging.getLogger(__name__)


class EmailSender:
    def __init__(
        self, host: str, port: int, use_tls: bool, sender: str,
        recipients: list[str], username: str = "", password: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.sender = sender
        self.recipients = recipients
        self.username = username
        self.password = password

    def send(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=30) as server:
            if self.use_tls:
                server.starttls(context=ssl.create_default_context())
            if self.username:
                server.login(self.username, self.password)
            server.send_message(msg)
        logger.info("Weekly report emailed to %s", ", ".join(self.recipients))


def build_email_sender(cfg: ReportingConfig, secrets: Secrets) -> EmailSender | None:
    """Construct an EmailSender from config+secrets, or None if not configured."""
    if not cfg.email_enabled:
        return None
    if not cfg.email_from or not cfg.email_to:
        logger.warning("reporting.email_enabled but email_from/email_to missing — skipping email")
        return None
    return EmailSender(
        host=cfg.smtp_host, port=cfg.smtp_port, use_tls=cfg.smtp_use_tls,
        sender=cfg.email_from, recipients=cfg.email_to,
        username=secrets.smtp_username.get_secret_value(),
        password=secrets.smtp_password.get_secret_value(),
    )

"""Validation for user-supplied URLs fetched by the bot."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


def validate_public_url(url: str) -> str:
    """Reject URLs that can address local services or non-public IP ranges."""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid URL") from exc

    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 80, 443)
    ):
        raise ValueError("only public HTTP(S) URLs on ports 80 and 443 are allowed")

    try:
        addresses = {ipaddress.ip_address(hostname)}
    except ValueError:
        try:
            records = socket.getaddrinfo(
                hostname.rstrip("."), port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ValueError("URL host could not be resolved") from exc
        addresses = {ipaddress.ip_address(record[4][0]) for record in records}

    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("URL host must resolve only to public IP addresses")
    return url

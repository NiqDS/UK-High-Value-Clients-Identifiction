from __future__ import annotations

import asyncio
import socket
import unittest
from unittest.mock import patch

import httpx
from scraper import ScrapeError, fetch_html
from url_security import validate_public_url


class URLSecurityTests(unittest.TestCase):
    def test_rejects_non_http_schemes(self) -> None:
        with self.assertRaises(ValueError):
            validate_public_url("file:///etc/passwd")

    def test_rejects_loopback_and_nonstandard_ports(self) -> None:
        for url in ("http://127.0.0.1/", "http://[::1]/", "http://example.com:8080/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_public_url(url)

    def test_rejects_private_dns_result(self) -> None:
        private_result = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.3", 443))
        ]
        with (
            patch("url_security.socket.getaddrinfo", return_value=private_result),
            self.assertRaises(ValueError),
        ):
            validate_public_url("https://example.com/")

    def test_accepts_public_dns_result(self) -> None:
        public_result = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]
        with patch("url_security.socket.getaddrinfo", return_value=public_result):
            self.assertEqual(validate_public_url("https://example.com/"), "https://example.com/")

    def test_static_fetch_rejects_private_redirect_before_requesting_it(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.requested: list[str] = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url: str):
                self.requested.append(url)
                return httpx.Response(
                    302,
                    headers={"location": "http://127.0.0.1/admin"},
                    request=httpx.Request("GET", url),
                )

        client = FakeClient()

        def validate(url: str) -> str:
            if url == "http://127.0.0.1/admin":
                raise ValueError("private destination")
            return url

        async def run() -> None:
            with (
                patch("scraper.httpx.AsyncClient", return_value=client),
                patch("scraper.validate_public_url", side_effect=validate),
                self.assertRaises(ScrapeError),
            ):
                await fetch_html("https://example.com/")

        asyncio.run(run())
        self.assertEqual(client.requested, ["https://example.com/"])


if __name__ == "__main__":
    unittest.main()

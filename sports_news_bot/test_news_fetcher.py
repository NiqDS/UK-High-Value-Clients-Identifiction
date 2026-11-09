from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from bot.news_fetcher import _parse_date


class PublishedDateTests(unittest.TestCase):
    def test_normalizes_offset_to_utc(self) -> None:
        published = _parse_date(
            SimpleNamespace(published="Mon, 02 Nov 2026 10:00:00 +0200", updated=None)
        )
        self.assertEqual(published, datetime(2026, 11, 2, 8, 0, 0))

    def test_fallback_is_naive_utc_shape(self) -> None:
        published = _parse_date(SimpleNamespace(published=None, updated=None))
        self.assertIsNone(published.tzinfo)


if __name__ == "__main__":
    unittest.main()

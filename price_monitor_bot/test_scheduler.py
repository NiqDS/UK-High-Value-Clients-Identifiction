from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from models import MonitoredItem, ScrapeResult
from scheduler import MonitorScheduler


class SchedulerAlertTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_alert_keeps_old_price_for_retry(self) -> None:
        item = MonitoredItem(
            id=1,
            chat_id=7,
            url="https://example.com/product",
            item_name="Product",
            last_price=10.0,
            currency_symbol="$",
            check_interval_minutes=60,
        )
        db = SimpleNamespace(
            get_item=AsyncMock(return_value=item),
            update_price=AsyncMock(),
            touch_checked=AsyncMock(),
        )
        app = SimpleNamespace(
            bot=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("offline")))
        )
        scheduler = MonitorScheduler(db, app)
        scrape_result = ScrapeResult(item_name="Product", price=12.0, currency_symbol="$")

        with patch("scheduler.scrape", new=AsyncMock(return_value=scrape_result)):
            await scheduler._check_item(item.id)

        db.update_price.assert_not_awaited()
        self.assertEqual(item.last_price, 10.0)


if __name__ == "__main__":
    unittest.main()

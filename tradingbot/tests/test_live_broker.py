"""No legacy acknowledgement-based live path may place orders."""
from unittest.mock import AsyncMock
import pytest
from tradingbot.execution.broker import LiveBroker


def test_live_adapter_is_not_certified():
    adapter=AsyncMock()
    with pytest.raises(RuntimeError,match='certified'):
        LiveBroker(adapter)
    adapter.create_order.assert_not_called()

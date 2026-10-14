"""Verify that a LINE message ID reaches the trusted SessionSource.

Run from the Hermes Agent repository with its runtime virtualenv. This uses no
network or production credentials and fails if the adapter drops the ID before
gateway session-context binding.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

from tests.gateway._plugin_adapter_loader import load_plugin_adapter


def main() -> int:
    line = load_plugin_adapter("line")
    from gateway.config import PlatformConfig

    os.environ["LINE_GROUP_KEYWORD"] = "@kumi"
    adapter = line.LineAdapter(PlatformConfig(enabled=True, extra={
        "channel_access_token": "test-token",
        "channel_secret": "test-secret",
    }))
    adapter._client = MagicMock()
    adapter.handle_message = AsyncMock()
    asyncio.run(adapter._handle_message_event({
        "type": "message",
        "replyToken": "test-reply-token",
        "source": {"type": "group", "groupId": "C-test", "userId": "U-test"},
        "message": {"type": "text", "id": "line-message-123", "text": "@kumi list my tasks"},
    }))
    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.message_id == "line-message-123"
    assert event.source.message_id == "line-message-123"
    print("LINE message context propagation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

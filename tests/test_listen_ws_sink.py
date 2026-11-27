"""Unit tests for modules.listen.sinks.websocket."""

import json
import unittest
from unittest import mock

from modules.listen.events import MatchEvent
from modules.listen.sinks.websocket import WebSocketBroadcaster, ws_sink


def _event():
    return MatchEvent(
        event_id="e1", timestamp="t", watchlist_id="a",
        source_type="telegram_web", target="@x", post_date="",
        post_views="", post_text="x", match_reasons=["bank:vcb"],
    )


class TestWebSocketBroadcaster(unittest.IsolatedAsyncioTestCase):

    async def test_broadcaster_sends_to_all_live_clients(self):
        b = WebSocketBroadcaster()
        c1 = mock.AsyncMock()
        c2 = mock.AsyncMock()
        b.register(c1)
        b.register(c2)
        await b.broadcast(_event())
        c1.send_str.assert_awaited_once()
        c2.send_str.assert_awaited_once()
        payload = c1.send_str.call_args.args[0]
        self.assertEqual(json.loads(payload)["event_id"], "e1")

    async def test_broadcaster_drops_client_whose_send_raised(self):
        b = WebSocketBroadcaster()
        dead = mock.AsyncMock()
        dead.send_str.side_effect = RuntimeError
        b.register(dead)
        await b.broadcast(_event())
        self.assertNotIn(dead, b._clients)

    async def test_ws_sink_delegates_to_broadcaster(self):
        b = WebSocketBroadcaster()
        c = mock.AsyncMock()
        b.register(c)
        sink = ws_sink(b)
        await sink(_event())
        c.send_str.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

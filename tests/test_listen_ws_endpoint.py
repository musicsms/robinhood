"""End-to-end test for the /ws/listen route wired into the viewer app.

Separate from tests/test_viewer_server.py because that file imports pytest
at the top and will not load under stdlib unittest; see the Task 6 ruling
in .superpowers/sdd/2026-11-27-listen-mode/progress.md."""

import json
import shutil
import tempfile
from pathlib import Path

from aiohttp.test_utils import AioHTTPTestCase

from modules.listen.events import MatchEvent
from modules.viewer.server import (
    LISTEN_BROADCASTER_KEY,
    create_viewer_app,
)


class TestListenWSEndpoint(AioHTTPTestCase):
    async def get_application(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="robin_test_ws_listen_"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))
        return create_viewer_app(investigations_dir=self.tmp_dir)

    async def test_ws_listen_route_echoes_ping(self):
        async with self.client.ws_connect("/ws/listen") as ws:
            await ws.send_str("ping")
            msg = await ws.receive(timeout=2.0)
            self.assertEqual(msg.type.name, "TEXT")
            self.assertEqual(msg.data, "pong")

    async def test_ws_listen_route_receives_broadcast_from_app(self):
        broadcaster = self.app[LISTEN_BROADCASTER_KEY]
        event = MatchEvent(
            event_id="eid", timestamp="t", watchlist_id="w",
            source_type="telegram_web", target="@x",
            post_date="", post_views="", post_text="dump",
            match_reasons=["bank:vcb"],
        )
        async with self.client.ws_connect("/ws/listen") as ws:
            await broadcaster.broadcast(event)
            msg = await ws.receive(timeout=2.0)
            self.assertEqual(msg.type.name, "TEXT")
            payload = json.loads(msg.data)
            self.assertEqual(payload["event_id"], "eid")

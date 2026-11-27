"""Unit tests for modules.listen.dispatcher + investigation_json sink."""

import json
import tempfile
import unittest
from pathlib import Path

from modules.listen.dispatcher import Dispatcher
from modules.listen.events import MatchEvent
from modules.listen.sinks.investigation_json import investigation_json_sink


def _event():
    return MatchEvent(
        event_id="abc",
        timestamp="2026-11-27T10:00:00+00:00",
        watchlist_id="a",
        source_type="telegram_web",
        target="@x",
        post_date="d",
        post_views="",
        post_text="Vietcombank leak",
        match_reasons=["bank:vietcombank"],
    )


class TestDispatcher(unittest.IsolatedAsyncioTestCase):

    async def test_dispatcher_fans_out_and_swallows_sink_errors(self):
        d = Dispatcher()
        seen: list[tuple[str, str]] = []

        async def good(ev):
            seen.append(("good", ev.event_id))

        async def bad(ev):
            raise RuntimeError("boom")

        d.register_sink(good)
        d.register_sink(bad)
        d.register_sink(good)
        await d.dispatch(_event())
        self.assertEqual(seen, [("good", "abc"), ("good", "abc")])


class TestInvestigationJSONSink(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    async def test_investigation_json_sink_writes_file(self):
        sink = investigation_json_sink(self.tmp_path)
        await sink(_event())
        files = sorted(self.tmp_path.glob("listen_match_*.json"))
        self.assertEqual(len(files), 1)
        body = json.loads(files[0].read_text())
        self.assertEqual(body["watchlist_id"], "a")
        self.assertEqual(body["match_reasons"], ["bank:vietcombank"])

    async def test_investigation_json_sink_two_calls_same_second_do_not_collide(self):
        sink = investigation_json_sink(self.tmp_path)
        await sink(_event())
        await sink(_event())
        self.assertEqual(
            len(list(self.tmp_path.glob("listen_match_*.json"))), 2
        )


if __name__ == "__main__":
    unittest.main()

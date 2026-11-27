"""Unit tests for modules.listen.workers.telegram_poll."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from modules.listen.events import MatchEvent
from modules.listen.watchlist import WatchlistEntry, open_db, upsert
from modules.listen.workers import telegram_poll as mod
from modules.listen.workers.telegram_poll import poll_once, signature


def _entry(id_="a"):
    return WatchlistEntry(
        id=id_, source_type="telegram_web", target="@x",
        interval_sec=60, enabled=True, seed_from=None,
        tags=[], last_polled=None, last_error=None,
    )


def _fake_scrape_result(posts, status="ok"):
    return {
        "status": status, "url": "https://t.me/s/x", "channel_name": "x",
        "title": "X", "posts_count": len(posts), "text": "fenced",
        "posts": posts,
    }


class _TmpConn:
    """Reusable tmp DB per test (TemporaryDirectory + open_db)."""
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = open_db(Path(self._tmp.name) / "w.db")
        return self.conn
    def __exit__(self, *a):
        self.conn.close()
        self._tmp.cleanup()


class TestPollWorker(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self._orig_scraper = mod.scrape_telegram_channel

    def tearDown(self):
        mod.scrape_telegram_channel = self._orig_scraper

    async def test_signature_is_deterministic_hash_of_date_and_text_head(self):
        post = {"date": "2026-11-27T10:00:00Z", "text": "x" * 500}
        sig1 = signature(post)
        sig2 = signature({"date": post["date"], "text": post["text"]})
        self.assertEqual(sig1, sig2)
        self.assertEqual(len(sig1), 64)  # sha256 hex

    async def test_polling_worker_emits_matches_for_vn_hits(self):
        with _TmpConn() as conn:
            upsert(conn, _entry())
            seen: list[MatchEvent] = []

            async def sink(ev: MatchEvent):
                seen.append(ev)

            posts = [
                {"date": "2026-11-27T10:00:00Z", "views": "1",
                 "text": "Vietcombank full dump"},
                {"date": "2026-11-27T11:00:00Z", "views": "2",
                 "text": "unrelated noise"},
            ]
            mod.scrape_telegram_channel = lambda *a, **k: _fake_scrape_result(posts)
            out = await poll_once(conn, _entry(), on_match=sink)
            self.assertEqual(out, {"posts": 2, "new": 2, "matches": 1, "error": None})
            self.assertEqual(len(seen), 1)
            self.assertIn("bank:vietcombank", seen[0].match_reasons)

    async def test_polling_worker_dedups_second_pass(self):
        with _TmpConn() as conn:
            upsert(conn, _entry())
            calls: list[MatchEvent] = []

            async def sink(ev):
                calls.append(ev)

            posts = [{"date": "d", "views": "", "text": "Vietcombank leak"}]
            mod.scrape_telegram_channel = lambda *a, **k: _fake_scrape_result(posts)
            await poll_once(conn, _entry(), on_match=sink)
            await poll_once(conn, _entry(), on_match=sink)
            self.assertEqual(len(calls), 1)

    async def test_polling_worker_records_last_error_and_continues(self):
        with _TmpConn() as conn:
            upsert(conn, _entry())

            async def sink(ev):
                pass

            mod.scrape_telegram_channel = lambda *a, **k: _fake_scrape_result(
                [], status="http_404"
            )
            out = await poll_once(conn, _entry(), on_match=sink)
            self.assertEqual(out["matches"], 0)
            self.assertEqual(out["error"], "http_404")
            row = conn.execute(
                "SELECT last_error FROM watchlist WHERE id=?", ("a",)
            ).fetchone()
            self.assertEqual(row[0], "http_404")

    async def test_polling_worker_reports_429_as_error_signal(self):
        with _TmpConn() as conn:
            upsert(conn, _entry())

            async def sink(ev):
                pass

            mod.scrape_telegram_channel = lambda *a, **k: _fake_scrape_result(
                [], status="http_429"
            )
            out = await poll_once(conn, _entry(), on_match=sink)
            self.assertEqual(out["error"], "http_429")


if __name__ == "__main__":
    unittest.main()

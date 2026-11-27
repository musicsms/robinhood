"""Unit tests for modules.listen.watchlist."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.listen.watchlist import (
    WatchlistEntry,
    list_enabled,
    load_yaml_seed,
    mark_seen,
    open_db,
    prune_seen_older_than,
    record_poll,
    upsert,
)


class TestWatchlistDB(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_open_db_creates_schema_idempotently(self):
        db = self.tmp_path / "w.db"
        c1 = open_db(db); c1.close()
        c2 = open_db(db); c2.close()  # must not error
        self.assertEqual(db.stat().st_mode & 0o777, 0o600)

    def test_upsert_then_list_enabled_roundtrips_all_fields(self):
        conn = open_db(self.tmp_path / "w.db")
        e = WatchlistEntry(
            id="a", source_type="telegram_web", target="@x",
            interval_sec=60, enabled=True, seed_from="s", tags=["t"],
            last_polled=None, last_error=None,
        )
        upsert(conn, e)
        rows = list_enabled(conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, "a")
        self.assertEqual(rows[0].tags, ["t"])

    def test_mark_seen_returns_false_on_duplicate(self):
        conn = open_db(self.tmp_path / "w.db")
        now = datetime.now(timezone.utc)
        self.assertIs(mark_seen(conn, "a", "sig1", now), True)
        self.assertIs(mark_seen(conn, "a", "sig1", now), False)

    def test_prune_seen_removes_older_than_cutoff(self):
        conn = open_db(self.tmp_path / "w.db")
        old = datetime.now(timezone.utc) - timedelta(days=30)
        new = datetime.now(timezone.utc)
        mark_seen(conn, "a", "old", old)
        mark_seen(conn, "a", "new", new)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        self.assertEqual(prune_seen_older_than(conn, cutoff), 1)

    def test_load_yaml_seed_parses_committed_stub(self):
        p = self.tmp_path / "w.yaml"
        p.write_text(
            "- id: a\n  source_type: telegram_web\n  target: '@x'\n"
            "  interval_sec: 60\n  enabled: true\n  tags: [t]\n",
            encoding="utf-8",
        )
        entries = load_yaml_seed(p)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].id, "a")

    def test_load_yaml_seed_returns_empty_list_for_empty_file(self):
        p = self.tmp_path / "w.yaml"
        p.write_text("[]\n", encoding="utf-8")
        self.assertEqual(load_yaml_seed(p), [])

    def test_record_poll_updates_last_polled_and_last_error(self):
        conn = open_db(self.tmp_path / "w.db")
        upsert(conn, WatchlistEntry(
            id="a", source_type="telegram_web", target="@x",
            interval_sec=60, enabled=True, seed_from=None, tags=[],
            last_polled=None, last_error=None,
        ))
        when = datetime.now(timezone.utc)
        record_poll(conn, "a", when, error="http_429")
        row = list_enabled(conn)[0]
        self.assertIsNotNone(row.last_polled)
        self.assertEqual(row.last_error, "http_429")

    def test_upsert_preserves_last_polled_when_entry_passes_none(self):
        conn = open_db(self.tmp_path / "w.db")
        e = WatchlistEntry(
            id="a", source_type="telegram_web", target="@x",
            interval_sec=60, enabled=True, seed_from=None, tags=[],
            last_polled=None, last_error=None,
        )
        upsert(conn, e)
        record_poll(conn, "a", datetime.now(timezone.utc), error=None)
        snap_before = list_enabled(conn)[0]
        self.assertIsNotNone(snap_before.last_polled)
        upsert(conn, e)  # same entry, None fields
        snap_after = list_enabled(conn)[0]
        self.assertEqual(snap_after.last_polled, snap_before.last_polled)

    def test_load_yaml_seed_raises_on_missing_required_field(self):
        p = self.tmp_path / "w.yaml"
        p.write_text("- id: a\n  source_type: telegram_web\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_yaml_seed(p)


if __name__ == "__main__":
    unittest.main()

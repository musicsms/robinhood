"""Persistent watchlist storage for listen mode.

SQLite file holds every target the polling workers should monitor, plus the
`seen_messages` dedup table. YAML stub at `config/watchlist.yaml` is the
check-in seed; deepdarkCTI mirrors are loaded separately in
`modules.listen.seed`.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import yaml

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    id            TEXT PRIMARY KEY,
    source_type   TEXT NOT NULL,
    target        TEXT NOT NULL,
    interval_sec  INTEGER NOT NULL DEFAULT 60,
    enabled       INTEGER NOT NULL DEFAULT 1,
    seed_from     TEXT,
    tags_json     TEXT NOT NULL DEFAULT '[]',
    last_polled   TEXT,
    last_error    TEXT
);

CREATE TABLE IF NOT EXISTS seen_messages (
    watchlist_id      TEXT NOT NULL,
    message_signature TEXT NOT NULL,
    seen_at           TEXT NOT NULL,
    PRIMARY KEY (watchlist_id, message_signature)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_enabled ON watchlist(enabled);
CREATE INDEX IF NOT EXISTS idx_seen_time ON seen_messages(seen_at);
"""

_REQUIRED_YAML_FIELDS = ("id", "source_type", "target")


@dataclass
class WatchlistEntry:
    id: str
    source_type: str
    target: str
    interval_sec: int = 60
    enabled: bool = True
    seed_from: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    last_polled: Optional[str] = None
    last_error: Optional[str] = None


def open_db(path: Path) -> sqlite3.Connection:
    """Open (or create) the watchlist DB with WAL + 0600 perms."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    if created:
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            logger.debug("Could not chmod %s: %s", path, exc)
    return conn


def _row_to_entry(row: sqlite3.Row) -> WatchlistEntry:
    return WatchlistEntry(
        id=row["id"],
        source_type=row["source_type"],
        target=row["target"],
        interval_sec=int(row["interval_sec"]),
        enabled=bool(row["enabled"]),
        seed_from=row["seed_from"],
        tags=json.loads(row["tags_json"] or "[]"),
        last_polled=row["last_polled"],
        last_error=row["last_error"],
    )


def upsert(conn: sqlite3.Connection, entry: WatchlistEntry) -> None:
    """Insert or update by id. Preserves `last_polled`/`last_error` when the
    caller passes `None` for them — a seed reload must not wipe runtime state."""
    existing = conn.execute(
        "SELECT last_polled, last_error FROM watchlist WHERE id = ?",
        (entry.id,),
    ).fetchone()

    last_polled = entry.last_polled
    last_error = entry.last_error
    if existing is not None:
        if last_polled is None:
            last_polled = existing["last_polled"]
        if last_error is None:
            last_error = existing["last_error"]

    conn.execute(
        """
        INSERT OR REPLACE INTO watchlist
          (id, source_type, target, interval_sec, enabled,
           seed_from, tags_json, last_polled, last_error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.id,
            entry.source_type,
            entry.target,
            entry.interval_sec,
            1 if entry.enabled else 0,
            entry.seed_from,
            json.dumps(list(entry.tags)),
            last_polled,
            last_error,
        ),
    )


def list_enabled(conn: sqlite3.Connection) -> List[WatchlistEntry]:
    rows = conn.execute(
        "SELECT * FROM watchlist WHERE enabled = 1 ORDER BY id"
    ).fetchall()
    return [_row_to_entry(r) for r in rows]


def record_poll(
    conn: sqlite3.Connection,
    watchlist_id: str,
    when: datetime,
    error: Optional[str],
) -> None:
    """Stamp the entry with its last poll time and the last error (or NULL)."""
    conn.execute(
        "UPDATE watchlist SET last_polled = ?, last_error = ? WHERE id = ?",
        (when.isoformat(), error, watchlist_id),
    )


def mark_seen(
    conn: sqlite3.Connection,
    watchlist_id: str,
    signature: str,
    when: datetime,
) -> bool:
    """Record a post signature. Returns True on new row, False on duplicate."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO seen_messages "
        "(watchlist_id, message_signature, seen_at) VALUES (?, ?, ?)",
        (watchlist_id, signature, when.isoformat()),
    )
    return cur.rowcount == 1


def prune_seen_older_than(conn: sqlite3.Connection, cutoff: datetime) -> int:
    cur = conn.execute(
        "DELETE FROM seen_messages WHERE seen_at < ?",
        (cutoff.isoformat(),),
    )
    return cur.rowcount


def load_yaml_seed(path: Path) -> List[WatchlistEntry]:
    """Read the YAML seed file. Empty document → []. Missing required fields → ValueError."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Expected a YAML list at top level of {path}, got {type(raw).__name__}")

    entries: List[WatchlistEntry] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"Entry #{idx} in {path} is not a mapping")
        for key in _REQUIRED_YAML_FIELDS:
            if key not in item:
                raise ValueError(f"Entry #{idx} in {path} missing required field '{key}'")
        entries.append(
            WatchlistEntry(
                id=str(item["id"]),
                source_type=str(item["source_type"]),
                target=str(item["target"]),
                interval_sec=int(item.get("interval_sec", 60)),
                enabled=bool(item.get("enabled", True)),
                seed_from=item.get("seed_from"),
                tags=list(item.get("tags", [])),
                last_polled=None,
                last_error=None,
            )
        )
    return entries


def to_dict(entry: WatchlistEntry) -> dict:
    """Serialisation helper (used by dispatcher JSON sink)."""
    return asdict(entry)

"""Seed the watchlist from deepdarkCTI mirrors.

The `extract_telegram_channels` helper is pure: it reads a markdown file and
returns unique handles. `seed_from_deepdarkcti` upserts them into the SQLite
watchlist and returns the count of newly inserted rows (ignoring entries
already present by id)."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from pathlib import Path
from typing import List, Optional

from modules.listen.watchlist import WatchlistEntry, upsert
from modules.scrape.telegram import extract_telegram_channel_name

logger = logging.getLogger(__name__)

_TME_URL_RE = re.compile(r"https?://t\.me/[^\s)\]|]+", re.IGNORECASE)


def extract_telegram_channels(md_path: Path) -> List[str]:
    """Return unique channel handles found in a deepdarkCTI markdown file.

    Order is first-seen. Private invite links (`joinchat/...`) and other
    non-channel paths return None from `extract_telegram_channel_name` and
    are skipped."""
    text = Path(md_path).read_text(encoding="utf-8", errors="ignore")
    seen: set[str] = set()
    out: List[str] = []
    for m in _TME_URL_RE.finditer(text):
        handle = extract_telegram_channel_name(m.group(0))
        if handle and handle not in seen:
            seen.add(handle)
            out.append(handle)
    return out


def _stable_id(handle: str) -> str:
    return "ddcti-" + hashlib.sha1(handle.encode("utf-8")).hexdigest()[:12]


def seed_from_deepdarkcti(
    conn: sqlite3.Connection,
    md_path: Path,
    *,
    source_type: str = "telegram_web",
    default_interval_sec: int = 60,
    tags: Optional[List[str]] = None,
) -> int:
    """Upsert one watchlist entry per unique channel handle in the file.

    Returns the number of NEW rows inserted. An id that already exists is
    left alone (so re-seeding on every boot is idempotent)."""
    handles = extract_telegram_channels(md_path)
    tags = list(tags or [])
    inserted = 0
    for handle in handles:
        entry_id = _stable_id(handle)
        existing = conn.execute(
            "SELECT 1 FROM watchlist WHERE id = ?", (entry_id,)
        ).fetchone()
        if existing is not None:
            continue
        upsert(
            conn,
            WatchlistEntry(
                id=entry_id,
                source_type=source_type,
                target="@" + handle,
                interval_sec=default_interval_sec,
                enabled=True,
                seed_from=Path(md_path).name,
                tags=tags,
                last_polled=None,
                last_error=None,
            ),
        )
        inserted += 1
    logger.info(
        "seed_from_deepdarkcti(%s): %d new entries (of %d handles)",
        Path(md_path).name, inserted, len(handles),
    )
    return inserted

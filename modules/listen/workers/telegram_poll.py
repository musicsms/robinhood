"""Polling worker for public Telegram channels (`t.me/s/<handle>`).

One `poll_loop` task runs per enabled watchlist entry. Each iteration
scrapes the channel via the existing web-preview helper, dedups by
`(channel, signature)`, filters with `match_vn_finance`, and awaits an
async `on_match(event)` per hit. Errors never crash the loop; HTTP 429
doubles the sleep (bounded at 1 hour) and resets on the next success.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from modules.listen.events import MatchEvent
from modules.listen.filters import match_vn_finance
from modules.listen.watchlist import (
    WatchlistEntry,
    mark_seen,
    record_poll,
)
from modules.scrape.telegram import scrape_telegram_channel

logger = logging.getLogger(__name__)

OnMatch = Callable[[MatchEvent], Awaitable[None]]


def signature(post: Dict[str, Any]) -> str:
    """SHA-256 of `date|text[:256]`. Stable across polls of the same post."""
    date = str(post.get("date") or "")
    text = str(post.get("text") or "")[:256]
    return hashlib.sha256(f"{date}|{text}".encode("utf-8")).hexdigest()


async def poll_once(
    conn: sqlite3.Connection,
    entry: WatchlistEntry,
    *,
    on_match: OnMatch,
) -> Dict[str, Any]:
    """Scrape, dedup, filter, dispatch. Returns a small counters dict."""
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: scrape_telegram_channel(entry.target)
        )
    except Exception as exc:  # scraper itself raised (rare — it catches most)
        logger.warning("poll_once scraper raised for %s: %s", entry.id, exc)
        record_poll(conn, entry.id, datetime.now(timezone.utc), error=str(exc))
        return {"posts": 0, "new": 0, "matches": 0, "error": str(exc)}

    status = result.get("status")
    now = datetime.now(timezone.utc)

    if status != "ok":
        record_poll(conn, entry.id, now, error=status)
        return {"posts": 0, "new": 0, "matches": 0, "error": status}

    posts = result.get("posts") or []
    new_count = 0
    match_count = 0

    for post in posts:
        sig = signature(post)
        if not mark_seen(conn, entry.id, sig, now):
            continue
        new_count += 1
        text = str(post.get("text") or "")
        matched, reasons = match_vn_finance(text)
        if not matched:
            continue
        event = MatchEvent(
            watchlist_id=entry.id,
            source_type=entry.source_type,
            target=entry.target,
            post_date=str(post.get("date") or ""),
            post_views=str(post.get("views") or ""),
            post_text=text,
            match_reasons=reasons,
        )
        await on_match(event)
        match_count += 1

    record_poll(conn, entry.id, now, error=None)
    return {
        "posts": len(posts),
        "new": new_count,
        "matches": match_count,
        "error": None,
    }


async def poll_loop(
    conn: sqlite3.Connection,
    entry: WatchlistEntry,
    on_match: OnMatch,
    *,
    stop_event: asyncio.Event,
) -> None:
    """Run `poll_once` in a loop until `stop_event.set()`. 429 → exp backoff."""
    backoff_mult: float = 1.0
    # Cap the effective sleep at one hour.
    max_mult = max(1.0, 3600.0 / max(1, entry.interval_sec))

    while not stop_event.is_set():
        try:
            out = await poll_once(conn, entry, on_match=on_match)
        except Exception as exc:
            logger.warning("poll_loop iteration error for %s: %s", entry.id, exc)
            out = {"error": "exception"}

        if out.get("error") == "http_429":
            backoff_mult = min(backoff_mult * 2, max_mult)
            logger.info(
                "poll_loop %s: 429 back-off, mult=%.1fx", entry.id, backoff_mult
            )
        else:
            backoff_mult = 1.0

        sleep_for = entry.interval_sec * backoff_mult
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            pass  # normal wake-up for next poll

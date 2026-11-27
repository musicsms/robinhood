"""Telegram bot notifier sink.

Sends a short message per `MatchEvent` to the configured chat_id. If
`token` or `chat_id` is unset the sink no-ops (one warning the first time
it is asked to send, then silent). Non-200 responses and network errors
are logged as the HTTP status only; the response body and the bot token
are never written to the log.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

import requests

from modules.listen.events import MatchEvent

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
MESSAGE_EXCERPT = 1200


def format_message(event: MatchEvent) -> str:
    handle = event.target.lstrip("@")
    body = (
        f"[LISTEN MATCH] {event.watchlist_id} ({event.source_type})\n"
        f"{', '.join(event.match_reasons)}\n"
        f"{event.target}\n\n"
        f"{event.post_text[:MESSAGE_EXCERPT]}\n\n"
        f"-> https://t.me/s/{handle}"
    )
    return body[:TELEGRAM_MESSAGE_LIMIT]


def telegram_bot_sink(
    token: Optional[str],
    chat_id: Optional[str],
    *,
    http_post=requests.post,
    timeout: float = 10.0,
) -> Callable[[MatchEvent], Awaitable[None]]:
    warned = False

    async def _sink(event: MatchEvent) -> None:
        nonlocal warned
        if not token or not chat_id:
            if not warned:
                logger.warning(
                    "telegram_bot_sink: TELEGRAM_BOT_TOKEN or "
                    "TELEGRAM_BOT_CHAT_ID unset; notifications disabled"
                )
                warned = True
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": format_message(event),
            "disable_web_page_preview": True,
        }
        try:
            resp = await asyncio.to_thread(
                http_post, url, json=payload, timeout=timeout
            )
        except Exception as exc:
            logger.warning("bot sink: network error %s", type(exc).__name__)
            return

        status = getattr(resp, "status_code", None)
        if status != 200:
            logger.warning("bot sink: HTTP %s", status)

    return _sink

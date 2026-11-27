"""Listen-mode boot: wire everything together and spawn poll loops.

Called from `main.py` as an on_startup hook when `ROBIN_LISTEN_ENABLED=1`.
Opens the SQLite watchlist, seeds it from YAML and deepdarkCTI (both
idempotent), builds the Dispatcher with the three Sprint-1 sinks, and
launches one `poll_loop` per enabled entry. Registers an on_cleanup
handler that sets the stop event and awaits the tasks.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import List, Optional

from aiohttp import web

from config import RobinConfig
from modules.listen.dispatcher import Dispatcher
from modules.listen.seed import seed_from_deepdarkcti
from modules.listen.sinks.investigation_json import investigation_json_sink
from modules.listen.sinks.telegram_bot import telegram_bot_sink
from modules.listen.sinks.websocket import ws_sink
from modules.listen.watchlist import list_enabled, load_yaml_seed, open_db, upsert
from modules.listen.workers.telegram_poll import poll_loop

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/listen/watchlist.db"
DEFAULT_YAML_PATH = "config/watchlist.yaml"
DEFAULT_INFOSTEALER_PATH = "data/deepdarkCTI/telegram_infostealer.md"


def _env_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default))


async def start_listen(
    app: web.Application,
    cfg: RobinConfig,
) -> List[asyncio.Task]:
    """Open DB, seed, wire sinks, spawn one poll_loop per enabled entry."""
    db_path = _env_path("LISTEN_WATCHLIST_DB_PATH", DEFAULT_DB_PATH)
    yaml_path = _env_path("LISTEN_WATCHLIST_YAML_PATH", DEFAULT_YAML_PATH)
    infostealer_path = _env_path(
        "LISTEN_SEED_INFOSTEALER_PATH", DEFAULT_INFOSTEALER_PATH
    )

    conn = open_db(db_path)

    # YAML seed: user edits.
    try:
        for entry in load_yaml_seed(yaml_path):
            upsert(conn, entry)
    except FileNotFoundError:
        logger.warning("listen: watchlist YAML not found at %s; skipping", yaml_path)
    except ValueError as exc:
        logger.warning("listen: watchlist YAML invalid (%s); skipping", exc)

    # deepdarkCTI infostealer seed: upstream mirror.
    if infostealer_path.exists():
        try:
            inserted = seed_from_deepdarkcti(
                conn, infostealer_path, tags=["infostealer"]
            )
            if inserted:
                logger.info("listen: seeded %d new infostealer channels", inserted)
        except Exception as exc:
            logger.warning("listen: infostealer seed failed: %s", exc)
    else:
        logger.info("listen: infostealer mirror not present at %s; skipping",
                    infostealer_path)

    # Build the dispatcher.
    dispatcher = Dispatcher()
    inv_dir = Path(cfg.investigations_dir or "investigations")
    dispatcher.register_sink(investigation_json_sink(inv_dir))

    # WS sink only if the viewer app carries a broadcaster.
    from modules.viewer.server import LISTEN_BROADCASTER_KEY
    if LISTEN_BROADCASTER_KEY in app:
        dispatcher.register_sink(ws_sink(app[LISTEN_BROADCASTER_KEY]))

    dispatcher.register_sink(
        telegram_bot_sink(
            token=os.getenv("TELEGRAM_BOT_TOKEN"),
            chat_id=os.getenv("TELEGRAM_BOT_CHAT_ID"),
        )
    )

    # Spawn one poll_loop per enabled entry.
    stop_event = asyncio.Event()
    tasks: List[asyncio.Task] = []
    for entry in list_enabled(conn):
        if entry.source_type != "telegram_web":
            # Sprint 1 only supports telegram_web; other types land in Sprint 2.
            continue
        tasks.append(
            asyncio.create_task(
                poll_loop(conn, entry, dispatcher.dispatch, stop_event=stop_event),
                name=f"listen-poll-{entry.id}",
            )
        )

    logger.info("listen: started %d poll loops", len(tasks))

    async def _cleanup(_app: web.Application) -> None:
        stop_event.set()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        try:
            conn.close()
        except Exception:
            pass

    app.on_cleanup.append(_cleanup)
    return tasks

"""WebSocket broadcaster + dispatcher sink.

A single `WebSocketBroadcaster` is attached to the aiohttp app; the
`/ws/listen` handler registers each live client with it, and the dispatcher's
`ws_sink` fans out one message per `MatchEvent` to every registered client.
A client whose send raises is dropped so a stuck socket cannot block the
others.
"""

from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable, Set

from modules.listen.events import MatchEvent

logger = logging.getLogger(__name__)


class WebSocketBroadcaster:
    def __init__(self) -> None:
        self._clients: Set = set()

    def register(self, ws) -> None:
        self._clients.add(ws)

    def unregister(self, ws) -> None:
        self._clients.discard(ws)

    async def broadcast(self, event: MatchEvent) -> None:
        payload = json.dumps(event.to_dict(), ensure_ascii=False)
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_str(payload)
            except Exception as exc:
                logger.debug("WS client drop on broadcast: %s", exc)
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)


def ws_sink(
    broadcaster: WebSocketBroadcaster,
) -> Callable[[MatchEvent], Awaitable[None]]:
    async def _sink(event: MatchEvent) -> None:
        await broadcaster.broadcast(event)
    return _sink

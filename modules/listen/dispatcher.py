"""Fan-out a `MatchEvent` to all registered sinks.

Sinks are async callables. A sink that raises is logged and skipped so
one bad sink cannot stop the others from firing.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, List

from modules.listen.events import MatchEvent

logger = logging.getLogger(__name__)

Sink = Callable[[MatchEvent], Awaitable[None]]


class Dispatcher:
    def __init__(self) -> None:
        self._sinks: List[Sink] = []

    def register_sink(self, sink: Sink) -> None:
        self._sinks.append(sink)

    async def dispatch(self, event: MatchEvent) -> None:
        for sink in self._sinks:
            try:
                await sink(event)
            except Exception as exc:
                logger.warning(
                    "listen sink %s raised %s", getattr(sink, "__name__", sink), exc
                )

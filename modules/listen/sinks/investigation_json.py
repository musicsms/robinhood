"""Investigation-JSON sink: write one `listen_match_*.json` per event.

Mirrors `store._candidate_names` (same `YYYYMMDD_HHMMSS[_N]` convention) but
with its own filename prefix so these match records don't pollute the
investigation list. The file is claimed with an exclusive-create open so
two events in the same second never overwrite each other.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from modules.listen.events import MatchEvent

logger = logging.getLogger(__name__)

FILENAME_PREFIX = "listen_match_"


def _candidate_names():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    yield f"{FILENAME_PREFIX}{stamp}.json"
    n = 1
    while True:
        yield f"{FILENAME_PREFIX}{stamp}_{n}.json"
        n += 1


def _write_new_file(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name in _candidate_names():
        path = directory / name
        try:
            with path.open("x", encoding="utf-8") as fh:
                fh.write(body)
            return path
        except FileExistsError:
            continue


def investigation_json_sink(
    investigations_dir: Path,
) -> Callable[[MatchEvent], Awaitable[None]]:
    directory = Path(investigations_dir)

    async def _sink(event: MatchEvent) -> None:
        body = json.dumps(event.to_dict(), indent=2, ensure_ascii=False)
        try:
            _write_new_file(directory, body)
        except OSError as exc:
            logger.warning("investigation_json_sink: could not write to %s: %s",
                           directory, exc)

    return _sink

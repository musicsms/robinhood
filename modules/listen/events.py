"""Match event emitted by listen-mode workers and consumed by sinks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MatchEvent:
    """A post that matched the VN-finance filter.

    All string fields are already scrubbed by the scraper (per security
    invariant in spec §5). The dispatcher never fences the text — that is
    the enricher's job if and when it feeds the post to an LLM.
    """
    watchlist_id: str
    source_type: str
    target: str
    post_date: str
    post_views: str
    post_text: str
    match_reasons: List[str] = field(default_factory=list)
    event_id: str = field(default_factory=lambda: uuid4().hex)
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return asdict(self)

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

from modules.viewer.models import (
    EventType,
    RunMetadata,
    TraceBundle,
    TraceEvent,
    TraceTurn,
)
from pipeline import Investigation

logger = logging.getLogger(__name__)


class InvestigationCollector:
    """Collects real-time events, turns, and findings from an active investigation."""

    def __init__(
        self,
        target_query: str,
        search_scope: str = "darkweb",
        preset: str = "threat_intel",
        model: str = "",
        max_iterations: int = 1,
        run_id: Optional[str] = None,
    ):
        self.run_id = run_id or f"inv_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        self.target_query = target_query
        self.metadata = RunMetadata(
            id=self.run_id,
            target_query=target_query,
            search_scope=search_scope,
            preset=preset,
            model=model,
            max_iterations=max_iterations,
        )
        self.subscribers: Set[asyncio.Queue] = set()
        self.events: List[TraceEvent] = []
        self.turns: List[TraceTurn] = []
        self.findings: List[Dict[str, Any]] = []
        self.summary: str = ""
        self.pivots: List[str] = []
        self._current_turn_idx: int = 0
        self._current_stage: Optional[str] = None
        self._current_turn: Optional[TraceTurn] = None
        self._stage_start_time: float = datetime.now().timestamp()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def dispatch(self, event: TraceEvent) -> None:
        self.events.append(event)
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except Exception:
                pass

    def on_stage(self, stage: str, investigation: Investigation) -> None:
        now = datetime.now().timestamp()
        if self._current_stage and self._current_turn:
            # Complete prior stage turn
            dur = now - self._stage_start_time
            self._current_turn.duration_sec = round(dur, 2)
            self._current_turn.description = f"Completed {self._current_stage}"
            self._current_turn.outputs = {"refined": investigation.refined, "status": investigation.status}
            self.dispatch(TraceEvent(
                event_type=EventType.STAGE_DONE,
                stage=self._current_stage,
                data={"duration": round(dur, 2)},
            ))

        self._current_stage = stage
        self._stage_start_time = now
        self._current_turn_idx += 1

        self.metadata.refined_query = investigation.refined
        self.metadata.results_count = len(investigation.results)
        self.metadata.scraped_count = len(investigation.scraped or {})
        self.metadata.cti_matches_count = len(investigation.cti_records or [])
        self.summary = investigation.summary
        self.pivots = investigation.pivots

        if investigation.results and not self.findings:
            for src in investigation.results:
                self.findings.append({
                    "title": src.get("title", ""),
                    "link": src.get("link", ""),
                    "snippet": src.get("snippet", ""),
                    "source": src.get("source", self.metadata.search_scope),
                })

        turn = TraceTurn(
            turn_idx=self._current_turn_idx,
            stage=stage,
            description=f"Running {stage}",
            duration_sec=0.0,
            inputs={"query": investigation.query},
            outputs={"refined": investigation.refined, "status": investigation.status},
        )
        self.turns.append(turn)
        self._current_turn = turn

        self.dispatch(TraceEvent(
            event_type=EventType.STAGE_START,
            stage=stage,
            data={"turn_idx": self._current_turn_idx, "query": investigation.query},
        ))

    def on_token(self, token: str) -> None:
        self.dispatch(TraceEvent(
            event_type=EventType.TOKEN,
            stage=self._current_stage,
            author="llm",
            data={"token": token},
        ))

    def record_cti_match(self, match: Dict[str, Any]) -> None:
        self.dispatch(TraceEvent(
            event_type=EventType.CTI_MATCH,
            stage=self._current_stage,
            data={"match": match},
        ))

    def record_log(self, level: str, message: str) -> None:
        self.dispatch(TraceEvent(
            event_type=EventType.LOG,
            stage=self._current_stage,
            data={"level": level, "message": message},
        ))

    def record_finding(self, finding: Dict[str, Any]) -> None:
        self.findings.append(finding)
        self.dispatch(TraceEvent(
            event_type=EventType.FINDING,
            stage=self._current_stage,
            data={"finding": finding},
        ))

    def finish(self, status: str = "COMPLETED") -> TraceBundle:
        now = datetime.now().timestamp()
        if self._current_stage and self._current_turn:
            dur = now - self._stage_start_time
            self._current_turn.duration_sec = round(dur, 2)
            self._current_turn.description = f"Completed {self._current_stage}"
            self.dispatch(TraceEvent(
                event_type=EventType.STAGE_DONE,
                stage=self._current_stage,
                data={"duration": round(dur, 2)},
            ))
            self._current_stage = None
            self._current_turn = None

        self.metadata.status = status
        self.metadata.end_time = datetime.now(timezone.utc).isoformat()
        try:
            start_dt = datetime.fromisoformat(self.metadata.start_time)
            end_dt = datetime.fromisoformat(self.metadata.end_time)
            self.metadata.duration_sec = round((end_dt - start_dt).total_seconds(), 2)
        except Exception:
            pass

        bundle = self.get_bundle()
        self.dispatch(TraceEvent(
            event_type=EventType.REPORT,
            stage="finish",
            data={"summary": self.summary, "pivots": self.pivots, "status": self.metadata.status},
        ))
        return bundle

    def get_bundle(self) -> TraceBundle:
        return TraceBundle(
            metadata=self.metadata,
            turns=list(self.turns),
            events=list(self.events),
            findings=list(self.findings),
            summary=self.summary,
            pivots=list(self.pivots),
        )

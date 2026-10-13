from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(str, Enum):
    STAGE_START = "stage_start"
    STAGE_DONE = "stage_done"
    TOKEN = "token"
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    CTI_MATCH = "cti_match"
    FINDING = "finding"
    LOG = "log"
    REPORT = "report"
    PIVOTS = "pivots"
    ERROR = "error"


class TraceEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_type: EventType
    author: str = "robin"
    stage: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class TraceTurn(BaseModel):
    turn_idx: int
    stage: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    description: str
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    duration_sec: float = 0.0
    status: str = "ok"


class RunMetadata(BaseModel):
    id: str
    target_query: str
    refined_query: str = ""
    status: str = "IN_PROGRESS"
    search_scope: str = "darkweb"
    preset: str = "threat_intel"
    model: str = ""
    start_time: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    end_time: Optional[str] = None
    duration_sec: float = 0.0
    results_count: int = 0
    scraped_count: int = 0
    cti_matches_count: int = 0
    max_iterations: int = 1
    iterations_run: int = 1


class TraceBundle(BaseModel):
    metadata: RunMetadata
    turns: List[TraceTurn] = Field(default_factory=list)
    events: List[TraceEvent] = Field(default_factory=list)
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    pivots: List[str] = Field(default_factory=list)


def legacy_to_bundle(record: Dict[str, Any], file_id: str = "") -> TraceBundle:
    """Converts a legacy investigation record dictionary into a TraceBundle."""
    run_id = file_id or record.get("timestamp", "").replace(":", "").replace("-", "") or uuid4().hex[:12]
    meta = RunMetadata(
        id=run_id,
        target_query=record.get("query", ""),
        refined_query=record.get("refined_query", ""),
        status="COMPLETED" if record.get("status") in ("ok", "no_results") else "FAILED",
        search_scope=record.get("search_scope", "darkweb"),
        preset=record.get("preset_key") or record.get("preset") or "threat_intel",
        model=record.get("model", ""),
        start_time=record.get("timestamp", datetime.now(timezone.utc).isoformat()),
        end_time=record.get("finished_at"),
        results_count=record.get("results_count", len(record.get("sources", []))),
        scraped_count=record.get("scraped_count", 0),
        cti_matches_count=record.get("cti_matches_count", 0),
        max_iterations=record.get("max_iterations", 1),
        iterations_run=record.get("iterations_run", 1),
    )

    turns: List[TraceTurn] = []
    events: List[TraceEvent] = []

    # Synthesize turns from recorded stages
    turns.append(TraceTurn(
        turn_idx=1,
        stage="refine",
        description="Query Refinement",
        inputs={"query": meta.target_query},
        outputs={"refined_query": meta.refined_query or meta.target_query},
    ))
    turns.append(TraceTurn(
        turn_idx=2,
        stage="search",
        description="Dark Web & Clear Web Search",
        inputs={"scope": meta.search_scope, "query": meta.refined_query or meta.target_query},
        outputs={"results_count": meta.results_count, "sources": record.get("sources", [])},
    ))
    turns.append(TraceTurn(
        turn_idx=3,
        stage="summarize",
        description="Executive Threat Intel Synthesis",
        inputs={"sources_analyzed": len(record.get("sources", []))},
        outputs={"summary_length": len(record.get("summary", ""))},
    ))

    # Findings from sources
    findings = []
    for src in record.get("sources", []):
        findings.append({
            "title": src.get("title", ""),
            "link": src.get("link", ""),
            "snippet": src.get("snippet", ""),
            "source": src.get("source", "darkweb"),
        })

    return TraceBundle(
        metadata=meta,
        turns=turns,
        events=events,
        findings=findings,
        summary=record.get("summary", ""),
        pivots=record.get("pivots", []),
    )


def load_bundle(file_path: Path) -> TraceBundle:
    """Loads a trace bundle or converts a legacy investigation JSON file."""
    data = json.loads(file_path.read_text(encoding="utf-8"))
    if "metadata" in data and "turns" in data:
        return TraceBundle.model_validate(data)
    return legacy_to_bundle(data, file_id=file_path.stem)

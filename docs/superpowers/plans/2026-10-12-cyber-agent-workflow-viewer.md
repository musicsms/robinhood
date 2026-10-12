# Robin-CTI Default Interactive Web Console & Trace Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standardized, interactive Web Console & Trace Viewer for Robin-CTI as the default front-door interface, closely modeled after `musicsms/cyber-agent` (`src/cyber_agent/viewer/`).

**Architecture:** A lightweight `aiohttp` web and WebSocket server (`modules/viewer/server.py`) hosting REST APIs and real-time event streaming (`/ws/live`). An `InvestigationCollector` (`modules/viewer/collector.py`) wraps the investigation pipeline to capture stage transitions, LLM reasoning tokens, CTI matches, and scraper logs into standardized `TraceBundle` models (`modules/viewer/models.py`). A zero-build vanilla JS/CSS/HTML dual-pane frontend (`modules/viewer/static/`) provides run configuration, live execution tracking, historical trace replay with a step playback slider, and follow-up analyst chatting.

**Tech Stack:** Python 3.10+, `aiohttp 3.14+`, `pydantic 2.x`, Modern Vanilla JS (ES6+), CSS3, HTML5.

**Spec:** `docs/superpowers/specs/2026-10-12-cyber-agent-workflow-viewer-design.md`

## Global Constraints
- Python 3.10+ compatibility.
- Zero-build frontend requirement: No npm, Node.js, Webpack, or Vite build steps. Vanilla HTML/CSS/JS only.
- Invariant: Preserve all 360 existing unit tests and security guarantees (CSAM filtering, Unicode scrubbing, content fencing).
- Default port: `8080` (configurable via `--port` or `PORT` environment variable).
- Backward compatibility: Legacy `investigations/*.json` files must load and play seamlessly in the viewer.

---

### Task 1: Trace Data Models & Backward Compatibility Parser

**Files:**
- Create: `modules/viewer/__init__.py`
- Create: `modules/viewer/models.py`
- Test: `tests/test_viewer_models.py`

**Interfaces:**
- Consumes: Standard Python datetime, uuid, pydantic.
- Produces: `EventType`, `TraceTurn`, `TraceEvent`, `RunMetadata`, `TraceBundle`, `legacy_to_bundle(record: dict) -> TraceBundle`, `load_bundle(file_path: Path) -> TraceBundle`.

- [ ] **Step 1: Write the failing test for models and legacy parser**

```python
# tests/test_viewer_models.py
import json
from pathlib import Path
from modules.viewer.models import (
    EventType,
    TraceEvent,
    TraceTurn,
    RunMetadata,
    TraceBundle,
    legacy_to_bundle,
    load_bundle,
)

def test_trace_models_instantiation():
    event = TraceEvent(
        event_type=EventType.STAGE_START,
        author="robin",
        stage="refine",
        data={"query": "test query"}
    )
    assert event.event_type == EventType.STAGE_START
    assert event.author == "robin"
    assert event.stage == "refine"

    turn = TraceTurn(
        turn_idx=1,
        stage="search",
        description="Search clearweb and darkweb",
        inputs={"query": "test"},
        outputs={"results_count": 5},
    )
    assert turn.turn_idx == 1
    assert turn.status == "ok"

    meta = RunMetadata(
        id="run_123",
        target_query="test query",
        status="COMPLETED"
    )
    bundle = TraceBundle(
        metadata=meta,
        turns=[turn],
        events=[event],
        summary="Analysis summary",
        pivots=["pivot1"]
    )
    assert len(bundle.turns) == 1
    assert len(bundle.events) == 1
    assert bundle.summary == "Analysis summary"

def test_legacy_to_bundle_conversion(tmp_path: Path):
    legacy_record = {
        "timestamp": "2026-10-10T06:48:26.112046",
        "query": "lotte finance",
        "refined_query": "lotte finance leak",
        "model": "deepseek-flash",
        "preset": "Dark Web Threat Intel",
        "sources": [
            {"title": "Lotte Card Breach", "link": "https://example.com/leak", "source": "clearweb"}
        ],
        "summary": "## Threat Intel\nDetailed breach findings.",
        "status": "ok",
        "pivots": ["pivot_one", "pivot_two"],
        "search_scope": "clearweb",
        "max_iterations": 1,
        "iterations_run": 1,
    }
    bundle = legacy_to_bundle(legacy_record)
    assert bundle.metadata.target_query == "lotte finance"
    assert bundle.metadata.refined_query == "lotte finance leak"
    assert bundle.metadata.status == "COMPLETED"
    assert len(bundle.turns) > 0  # Reconstructed turns
    assert bundle.summary == legacy_record["summary"]
    assert bundle.pivots == ["pivot_one", "pivot_two"]

    test_file = tmp_path / "investigation_test.json"
    test_file.write_text(json.dumps(legacy_record), encoding="utf-8")
    loaded = load_bundle(test_file)
    assert loaded.metadata.target_query == "lotte finance"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_viewer_models.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.viewer'`

- [ ] **Step 3: Implement `modules/viewer/__init__.py` and `modules/viewer/models.py`**

```python
# modules/viewer/__init__.py
"""Robin-CTI Interactive Web Console and Trace Viewer module."""
```

```python
# modules/viewer/models.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_viewer_models.py -v`  
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add modules/viewer/__init__.py modules/viewer/models.py tests/test_viewer_models.py
git commit -m "feat(viewer): implement trace data models and legacy bundle adapter"
```

---

### Task 2: Realtime Investigation Collector & Hooks

**Files:**
- Create: `modules/viewer/collector.py`
- Test: `tests/test_viewer_collector.py`

**Interfaces:**
- Consumes: `modules.viewer.models` (`TraceBundle`, `TraceTurn`, `TraceEvent`, `EventType`, `RunMetadata`).
- Produces: `InvestigationCollector`:
  - `collector.on_stage(stage: str, investigation: Investigation)`
  - `collector.on_token(token: str)`
  - `collector.record_log(level: str, message: str)`
  - `collector.record_cti_match(match: dict)`
  - `collector.subscribe() -> asyncio.Queue`
  - `collector.unsubscribe(queue: asyncio.Queue)`
  - `collector.get_bundle() -> TraceBundle`

- [ ] **Step 1: Write the failing test for InvestigationCollector**

```python
# tests/test_viewer_collector.py
import asyncio
import pytest
from modules.viewer.collector import InvestigationCollector
from modules.viewer.models import EventType
from pipeline import Investigation

@pytest.mark.asyncio
async def test_collector_events_and_subscription():
    collector = InvestigationCollector(target_query="lotte finance", search_scope="clearweb")
    queue = collector.subscribe()

    # Stage start event
    inv = Investigation(query="lotte finance", refined="lotte finance leak")
    collector.on_stage("refine", inv)

    event1 = await queue.get()
    assert event1.event_type == EventType.STAGE_START
    assert event1.stage == "refine"

    # Token streaming event
    collector.on_token("Analyzing")
    event2 = await queue.get()
    assert event2.event_type == EventType.TOKEN
    assert event2.data["token"] == "Analyzing"

    # CTI Match event
    collector.record_cti_match({"title": "Lotte leak dump", "actor": "breach_team"})
    event3 = await queue.get()
    assert event3.event_type == EventType.CTI_MATCH
    assert event3.data["match"]["actor"] == "breach_team"

    collector.unsubscribe(queue)
    bundle = collector.get_bundle()
    assert bundle.metadata.target_query == "lotte finance"
    assert len(bundle.turns) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_viewer_collector.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.viewer.collector'`

- [ ] **Step 3: Implement `modules/viewer/collector.py`**

```python
# modules/viewer/collector.py
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
        if self._current_stage:
            # Complete prior stage turn
            dur = now - self._stage_start_time
            turn = TraceTurn(
                turn_idx=self._current_turn_idx,
                stage=self._current_stage,
                description=f"Completed {self._current_stage}",
                duration_sec=round(dur, 2),
                outputs={"refined": investigation.refined, "status": investigation.status},
            )
            self.turns.append(turn)
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

    def finish(self, status: str = "COMPLETED") -> TraceBundle:
        self.metadata.status = status
        self.metadata.end_time = datetime.now(timezone.utc).isoformat()
        bundle = self.get_bundle()
        self.dispatch(TraceEvent(
            event_type=EventType.REPORT,
            stage="finish",
            data={"summary": self.summary, "pivots": self.pivots},
        ))
        return bundle

    def get_bundle(self) -> TraceBundle:
        return TraceBundle(
            metadata=self.metadata,
            turns=self.turns,
            events=self.events,
            findings=self.findings,
            summary=self.summary,
            pivots=self.pivots,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_viewer_collector.py -v`  
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add modules/viewer/collector.py tests/test_viewer_collector.py
git commit -m "feat(viewer): implement investigation event collector and real-time hooks"
```

---

### Task 3: Async Web & WebSocket Server

**Files:**
- Create: `modules/viewer/server.py`
- Test: `tests/test_viewer_server.py`

**Interfaces:**
- Consumes: `modules.viewer.models`, `modules.viewer.collector`, `pipeline.run_investigation`, `config.get_config()`.
- Produces:
  - `create_viewer_app(investigations_dir: Path) -> web.Application`
  - `run_viewer_server(host: str, port: int, investigations_dir: Path)`
  - REST APIs: `GET /api/status`, `GET /api/runs`, `GET /api/runs/{run_id}`, `POST /api/investigate`, `POST /api/investigate/{run_id}/abort`, `POST /api/followup`
  - WebSocket: `/ws/live`

- [ ] **Step 1: Write the failing test for the aiohttp server**

```python
# tests/test_viewer_server.py
import json
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from modules.viewer.server import create_viewer_app

class TestViewerServer(AioHTTPTestCase):
    async def get_application(self):
        self.tmp_dir = Path("/tmp/robin_test_investigations")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        # Seed sample investigation
        sample = {
            "timestamp": "2026-10-10T06:48:26.112046",
            "query": "lotte test",
            "refined_query": "lotte test leak",
            "model": "deepseek-flash",
            "summary": "Sample summary",
            "status": "ok",
        }
        (self.tmp_dir / "investigation_test1.json").write_text(json.dumps(sample), encoding="utf-8")
        return create_viewer_app(investigations_dir=self.tmp_dir)

    @unittest_run_loop
    async def test_get_status(self):
        resp = await self.client.request("GET", "/api/status")
        assert resp.status == 200
        data = await resp.json()
        assert "status" in data
        assert "cti_indexed_count" in data

    @unittest_run_loop
    async def test_get_runs(self):
        resp = await self.client.request("GET", "/api/runs")
        assert resp.status == 200
        runs = await resp.json()
        assert len(runs) >= 1
        assert runs[0]["target_query"] == "lotte test"

    @unittest_run_loop
    async def test_get_run_detail(self):
        resp = await self.client.request("GET", "/api/runs/investigation_test1")
        assert resp.status == 200
        bundle = await resp.json()
        assert bundle["metadata"]["target_query"] == "lotte test"
        assert bundle["summary"] == "Sample summary"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_viewer_server.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.viewer.server'`

- [ ] **Step 3: Implement `modules/viewer/server.py`**

Implement complete `create_viewer_app` with:
- Static asset handler for `/` and `/static/*`.
- Run scanner for `investigations_dir` reading both bundles and legacy JSON files via `load_bundle`.
- `GET /api/status` returning tor status, CTI engine count (`modules.cti.indexer.INDEX_FILE` or lookup items), and models.
- `GET /api/runs` and `GET /api/runs/{run_id}`.
- `POST /api/investigate` spawning async task running `pipeline.run_investigation` connected to `InvestigationCollector`.
- `POST /api/investigate/{run_id}/abort` cancelling the task.
- `POST /api/followup` answering questions using report summary.
- `/ws/live` streaming WebSocket events from active collector.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_viewer_server.py -v`  
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add modules/viewer/server.py tests/test_viewer_server.py
git commit -m "feat(viewer): implement aiohttp web console REST APIs and WebSocket server"
```

---

### Task 4: Zero-Build Frontend Interface (HTML, CSS, JS)

**Files:**
- Create: `modules/viewer/static/index.html`
- Create: `modules/viewer/static/style.css`
- Create: `modules/viewer/static/viewer.js`
- Test: `tests/test_viewer_static.py`

**Interfaces:**
- Consumes: Browser DOM, WebSocket `/ws/live`, REST `/api/runs`, `/api/investigate`.
- Produces: Responsive Dual-Pane Cyber Dark interface matching Cyber-Agent aesthetic.

- [ ] **Step 1: Write static asset verification test**

```python
# tests/test_viewer_static.py
from pathlib import Path
from modules.viewer.server import STATIC_DIR

def test_static_assets_exist():
    assert (STATIC_DIR / "index.html").exists()
    assert (STATIC_DIR / "style.css").exists()
    assert (STATIC_DIR / "viewer.js").exists()

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "Robin-CTI" in html
    assert "ws/live" in (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_viewer_static.py -v`  
Expected: FAIL with missing static assets

- [ ] **Step 3: Implement `index.html`, `style.css`, and `viewer.js`**

- `index.html`:
  - Header: Logo, status badges (Tor, CTI Index, Active Model), past runs dropdown, `+ New Investigation` button.
  - Left Workspace: New Investigation form (query, scope pills, preset, deep iterations) and Stage Timeline cards (`refine`, `cti_lookup`, `search`, `filter`, `scrape`, `eval_gap`, `summarize`).
  - Right Workspace: Tab buttons + Panels:
    1. 💻 Terminal & Replay
    2. 🧠 LLM Reasoning
    3. 🛡️ CTI & IOC Matches
    4. 📄 Intelligence Report (Markdown rendered)
    5. 💬 Follow-up Analyst (Interactive Chat)
    6. 📦 Raw Trace JSON
  - Bottom Bar: Replay slider `[0 -> N]`, Play/Pause, Speed `1x/2x/5x`, Status marquee.
- `style.css`:
  - GitHub/Cyber Dark palette (`#0d1117`, `#161b22`, `#21262d`, `#30363d`).
  - Accents: Neon Green `#3fb950`, Cyan `#58a6ff`, Yellow `#d29922`, Red `#f85149`.
  - Mac terminal chrome (red/yellow/green top-left circles).
- `viewer.js`:
  - WebSocket auto-reconnect logic.
  - Live state machine transitioning stages on `stage_start` / `stage_done`.
  - Streaming token accumulator into LLM Reasoning box.
  - Historical trace scrubber: moving slider steps through recorded turns, updating active stage card and terminal/thought views.
  - Follow-up chat poster to `/api/followup`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_viewer_static.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add modules/viewer/static/index.html modules/viewer/static/style.css modules/viewer/static/viewer.js tests/test_viewer_static.py
git commit -m "feat(viewer): implement modern zero-build dual-pane cyber frontend"
```

---

### Task 5: Default Webservice Integration & CLI Polish

**Files:**
- Create: `main.py`
- Modify: `entrypoint.sh`
- Modify: `requirements.txt`
- Modify: `config.py` (if default viewer port setting needed)
- Test: `tests/test_main_entrypoint.py`

**Interfaces:**
- Consumes: `modules.viewer.server.run_viewer_server`.
- Produces: `python main.py --host 0.0.0.0 --port 8080` as the default entry point.

- [ ] **Step 1: Write integration test for `main.py` CLI parser**

```python
# tests/test_main_entrypoint.py
import pytest
from main import parse_args

def test_parse_args_defaults():
    args = parse_args([])
    assert args.host == "0.0.0.0"
    assert args.port == 8080

def test_parse_args_custom():
    args = parse_args(["--host", "127.0.0.1", "--port", "9090"])
    assert args.host == "127.0.0.1"
    assert args.port == 9090
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_main_entrypoint.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Implement `main.py`, update `entrypoint.sh` and `requirements.txt`**

- In `main.py`: CLI arguments parsing, Tor availability check, CTI index refresh if needed, launching `run_viewer_server`.
- In `entrypoint.sh`: update line 92 from `exec streamlit run ui.py...` to `exec python3 main.py --host 0.0.0.0 --port 8080 "$@"`.
- In `requirements.txt`: ensure `aiohttp` and `pydantic` are explicitly pinned.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_main_entrypoint.py -v`  
Expected: PASS

- [ ] **Step 5: Execute full regression test suite**

Run: `.venv/bin/pytest tests/ -v`  
Expected: ALL tests PASS (existing 360 + new tests).

- [ ] **Step 6: Commit**

```bash
git add main.py entrypoint.sh requirements.txt tests/test_main_entrypoint.py
git commit -m "feat: make interactive web console the default entrypoint for Robin-CTI"
```

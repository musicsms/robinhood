# Design Specification: Robin-CTI Default Interactive Web Console & Trace Viewer

**Date:** 2026-10-12  
**Status:** Draft  
**Author:** Antigravity & Musicsms  
**Reference:** `musicsms/cyber-agent` (`src/cyber_agent/viewer/`)

---

## 1. Executive Summary & Goals

### 1.1 Motivation
Robin-CTI currently relies on either CLI execution or a Streamlit-based UI (`ui.py`). Streamlit suffers from heavy page re-renders, lack of fine-grained WebSocket control for streaming thoughts/logs, and rigid component layouts. Furthermore, analyzing past threat intelligence runs requires manual file inspection rather than interactive step-by-step trace playback.

Drawing inspiration from `musicsms/cyber-agent` (`src/cyber_agent/viewer`), Robin-CTI will feature a dedicated, high-performance **Default Interactive Web Console & Trace Viewer**.

### 1.2 Core Objectives
1. **Primary / Default Front-Door:** The web console is the default user-facing interface for Robin-CTI when started via `main.py`, `docker run`, or `entrypoint.sh`.
2. **Interactive Run Launcher:** Users can configure and launch investigations directly from the UI (target, search scope: `darkweb` / `clearweb` / `all`, presets, iterative loop depth, model selection) and abort runs if needed.
3. **Real-time Observability via WebSocket (`/ws/live`):** Streams LLM reasoning tokens, Tor connection logs, search query executions, scraping status, CSAM/security sanitization notices, and CTI engine matches with sub-second latency.
4. **Historical Replay & Timeline Playback:** Scans `investigations/*.json` and provides a playback slider (`[0 -> N]` steps, play/pause, 1x/2x/5x speed) to step through past investigations turn-by-turn.
5. **Interactive Follow-up Analyst:** In-browser conversational follow-up on completed investigations, retaining full threat report context.
6. **Zero-Build Architecture:** Modern Vanilla JS, CSS3, and HTML5 without Node.js/npm dependencies, maintaining a lightweight Python-native deployment.
7. **Security & Backward Compatibility:** Retains 100% compatibility with all 360 existing test cases, MCP tools, and security invariants (fencing, CSAM filtering, Unicode scrubbing).

---

## 2. Architecture & Data Flow

### 2.1 System Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Web Browser Client                              │
│  - Top Bar: System Status, Run Selector, New Run Trigger               │
│  - Left Pane: Interactive Setup & Workflow Stage Pipeline Flow         │
│  - Right Pane: Terminal / LLM Thoughts / CTI Matches / Report / Chat   │
│  - Bottom Bar: Step Playback Controls (Slider, Play/Pause, Speeds)     │
└───────────────────────────────────▲────────────────────────────────────┘
                                    │ HTTP REST & WebSocket (/ws/live)
┌───────────────────────────────────┴────────────────────────────────────┐
│                    modules/viewer/server.py                            │
│  - aiohttp Web Server (Default Port: 8080)                             │
│  - REST Endpoints: /api/runs, /api/investigate, /api/followup, etc.    │
│  - WebSocket Hub: Real-time broadcast of live bus events               │
└──────────────▲──────────────────────────────────────────▲──────────────┘
               │ Event Subscriptions                      │ Reads / Writes
┌──────────────┴──────────────────────────┐  ┌────────────┴─────────────┐
│ modules/viewer/collector.py             │  │ Store & Historical Traces│
│ - TraceEvent & TraceTurn serialization  │  │ investigations/*.json    │
│ - Stage & Token streaming callbacks     │  └──────────────────────────┘
└──────────────▲──────────────────────────┘
               │ Invocations & Callbacks
┌──────────────┴──────────────────────────┐
│ pipeline.run_investigation              │
│ - Core Threat Intelligence Pipeline     │
└─────────────────────────────────────────┘
```

---

## 3. Data Models (`modules/viewer/models.py`)

Adapted and enhanced from `cyber_agent/viewer/models.py`:

### 3.1 Event Types (`EventType`)
```python
class EventType(str, Enum):
    STAGE_START = "stage_start"       # Entered pipeline stage (refine, search, etc.)
    STAGE_DONE = "stage_done"         # Completed stage with summary metrics
    TOKEN = "token"                   # Streaming LLM generation token
    THOUGHT = "thought"               # Chain-of-thought or reasoning excerpt
    TOOL_CALL = "tool_call"           # Search query, scraping attempt, or CTI lookup
    CTI_MATCH = "cti_match"           # High-fidelity match from DeepDark CTI engine
    FINDING = "finding"               # Extracted IOC, CVE, actor, leak metadata
    LOG = "log"                       # Engine/system log (Tor, network, status)
    REPORT = "report"                 # Final synthesized threat intel report
    PIVOTS = "pivots"                 # Recommended secondary hunt vectors
    ERROR = "error"                   # Exception or failure notice
```

### 3.2 Turn & Event Structures
```python
class TraceEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_type: EventType
    author: str = "robin"            # "robin", "llm", "scraper", "search", "cti"
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
    status: str = "IN_PROGRESS"      # "IN_PROGRESS", "COMPLETED", "FAILED", "ABORTED"
    search_scope: str = "darkweb"    # "darkweb", "clearweb", "all"
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
```

### 3.3 Backward Compatibility Adapter
A dedicated utility `load_investigation_as_bundle(json_path: Path) -> TraceBundle` parses legacy `investigation_*.json` files into synthetic `TraceBundle` structures with reconstructed `TraceTurn` steps, allowing seamless playback of all pre-existing investigations.

---

## 4. Real-time Event Collector (`modules/viewer/collector.py`)

The `InvestigationCollector` manages the event lifecycle for live investigations:
- **Async Event Queue / Broadcaster:** Uses `asyncio.Queue` or listener callbacks to pipe events directly to connected WebSocket clients.
- **Stage Hook (`on_stage`):** Intercepts stage transitions (`load_llm`, `refine`, `search`, `filter`, `scrape`, `eval_gap`, `summarize`, `pivots`, `save`). Creates corresponding `TraceTurn` objects and fires `STAGE_START` / `STAGE_DONE` events.
- **Token Hook (`on_token`):** Buffers and streams raw LLM tokens to the UI with debounced WebSocket frames to optimize network performance.
- **Tool & Log Hooks:** Records CTI lookups, clearweb/darkweb search requests, scraping HTTP status codes, and security sanitization events.
- **Persistence:** Upon run completion, saves the complete `TraceBundle` to `investigations/` alongside the standard record format.

---

## 5. Web Server & API Design (`modules/viewer/server.py`)

Implemented via `aiohttp.web` (fully async, standard in Python, minimal footprint):

### 5.1 REST Endpoints
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Serves the main UI (`static/index.html`) |
| `GET` | `/api/status` | System health: Tor status, CTI index size, available models |
| `GET` | `/api/runs` | List all historical and active investigations |
| `GET` | `/api/runs/{run_id}` | Retrieve full `TraceBundle` for a run |
| `POST` | `/api/investigate` | Launch a new investigation (JSON body: query, scope, preset, iterations, model) |
| `POST` | `/api/investigate/{run_id}/abort` | Abort a running investigation |
| `POST` | `/api/followup` | Ask a follow-up question on an investigation |
| `GET` | `/static/{path:.*}` | Serves static assets (`style.css`, `viewer.js`, icons) |

### 5.2 WebSocket Endpoint (`/ws/live`)
- Clients connect to `/ws/live`.
- Server pushes JSON frames:
  ```json
  {
    "event_type": "tool_call",
    "stage": "search",
    "author": "search",
    "timestamp": "2026-10-12T11:45:00.000Z",
    "data": {
      "engine": "duckduckgo",
      "query": "lotte finance leak",
      "results_found": 8
    }
  }
  ```
- Handles heartbeats (`ping` / `pong`).

---

## 6. Frontend UI / UX Specification (`modules/viewer/static/`)

The design faithfully replicates the Cyber-Agent aesthetic (`#0d1117` GitHub dark theme with terminal green `#3fb950`, cyber blue `#58a6ff`, warning yellow `#d29922`, and danger red `#f85149` accents).

### 6.1 Top Bar
- **Logo & Status:** Robin-CTI badge, live status indicators (Tor circuit status, CTI engine records count, Active model).
- **Run Selector:** Dropdown listing past investigations sorted by date, with search/filter.
- **Action Buttons:** `+ New Investigation`, `Export Report` (Markdown / JSON).

### 6.2 Left Workspace Pane (Pipeline & Launch Form)
- **New Investigation Card (when launching):**
  - Query input field with search button.
  - Scope radio pills: `Dark Web (Tor)`, `Clear Web`, `All-Domain (Tor + Clear + Telegram)`.
  - Preset dropdown: Dark Web Threat Intel, Brand Monitoring, Ransomware/Leak, Exploit/CVE.
  - Deep Loop toggle: Single pass vs Iterative Auto-Refinement (1-3 iterations).
  - Advanced config collapsible: Custom instructions, threads, max results.
- **Workflow Pipeline Cards (Active / Replaying Run):**
  - Vertical stage cards with status icons (Done `✓`, Running `⏳`, Pending `○`, Error `✗`):
    1. Query Refinement (`refine`)
    2. DeepDark CTI Engine (`cti_lookup`)
    3. Multi-Engine Search (`search`)
    4. Relevance Filtering (`filter`)
    5. Content Scraping & Fencing (`scrape`)
    6. Gap Evaluation & Auto-Pivots (`eval_gap`)
    7. Executive Summary Synthesis (`summarize`)
  - Clicking any stage highlights its inputs/outputs in the right pane.

### 6.3 Right Workspace Pane (Inspector Tabs)
- **Tab 1: 💻 Terminal & Replay:** Mac-style terminal window with colored logs, execution details, search URLs, scraping results, and tor status.
- **Tab 2: 🧠 LLM Reasoning:** Real-time token streaming box displaying model thoughts and synthesis drafting.
- **Tab 3: 🛡️ CTI & IOC Matches:** Formatted table of matched IOCs, CVEs, ransomware groups, and Telegram channel signals.
- **Tab 4: 📄 Intelligence Report:** Formatted Markdown threat intelligence report with syntax highlighting, key findings, and recommended next pivots.
- **Tab 5: 💬 Follow-up Analyst:** Interactive chat box to query the investigation report directly.
- **Tab 6: 📦 Raw Trace JSON:** Formatted JSON viewer with copy-to-clipboard for auditing.

### 6.4 Bottom Playback Control Bar
- Control buttons: `⏮ Start`, `◀ Step Back`, `▶ Play / ⏸ Pause`, `Step Forward ▶`, `End ⏭`.
- Slider: `[ 0 ────────●──────── N ]` showing current step index and stage label.
- Speed selector: `1x`, `2x`, `5x`, `Instant`.
- Live status indicator ticker.

---

## 7. CLI & Service Entrypoint Integration

1. **Default Startup (`main.py`):**
   - Executing `python main.py` runs the Web Console on `http://0.0.0.0:8080` by default.
   - CLI flags: `--port`, `--host`, `--no-browser`.
2. **Container Entrypoint (`entrypoint.sh`):**
   - Mode `ui` defaults to starting the new Web Console (`python main.py --host 0.0.0.0 --port 8080`) instead of Streamlit.
   - Mode `mcp` remains unchanged for stdio JSON-RPC transport.

---

## 8. Verification & Test Plan

1. **Model & Serialization Tests (`tests/test_viewer_models.py`):**
   - Verify `TraceEvent`, `TraceTurn`, `RunMetadata`, and `TraceBundle` serialization and validation.
   - Test legacy `investigation_*.json` loading and conversion to `TraceBundle`.
2. **Collector Tests (`tests/test_viewer_collector.py`):**
   - Test stage hook transitions, token stream buffering, and event queue broadcasting.
   - Test run bundle generation and persistence.
3. **Server REST & WebSocket Tests (`tests/test_viewer_server.py`):**
   - Test `GET /api/status`, `GET /api/runs`, `GET /api/runs/{id}` using `aiohttp.test_utils`.
   - Test investigation launch endpoint `POST /api/investigate`.
   - Test WebSocket `/ws/live` connection, ping/pong, and event broadcast.
4. **End-to-End Regression Test:**
   - Execute full test suite `pytest tests/` to confirm all 360 existing unit tests pass.

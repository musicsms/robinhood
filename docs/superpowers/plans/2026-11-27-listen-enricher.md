# Listen Enricher (Sprint 2 feature 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an LLM-scored enrichment stage between the dedup check and the dispatcher: each new post is scored 0-10 for Vietnam-finance relevance by a cascade of models (grok-4.6 → grok-4.5 → deepseek-r1:32b); posts below the threshold are archived to a `rejected/` folder and not sent to WebSocket or bot sinks; posts at or above the threshold reach the existing sinks with an enriched bot message.

**Architecture:** A new `modules/listen/enricher.py` carrying an `Enricher` class with: a `score(post, entry, reasons) -> Optional[EnrichmentResult]` method; a configurable model cascade; an asyncio `Semaphore` rate limit; and a side-car audit log (`enricher_audit.py`) that appends one JSON line per LLM call. `poll_once` calls the enricher after `mark_seen` and before `on_match`; the resulting `MatchEvent.enrichment` + `below_threshold` steer sink routing. `investigation_json_sink` grows a `rejected/` branch; `telegram_bot_sink`'s `format_message` prepends the relevance header and summary when enrichment is present. Nothing runs by default — `LISTEN_ENRICHER_ENABLED=0` keeps Sprint 1 behaviour unchanged.

**Tech Stack:** Python 3.14, stdlib `unittest`+`IsolatedAsyncioTestCase` (matches Sprint 1 convention), `langchain-openai` (already wired via `llm.get_llm`), existing `core.security.fence_untrusted` for prompt safety.

**Spec:** `docs/superpowers/specs/2026-11-27-listen-enricher-design.md`

## Global Constraints

- **Python 3.14 venv at `.venv/`.** All test commands: `.venv/bin/python -m unittest ...`.
- **No new third-party dependency.** `llm.get_llm` already exposes `grok-4.6`, `grok-4.5`, and Ollama (`deepseek-r1:32b`) via the same `ChatOpenAI` wrapper.
- **Default off.** `LISTEN_ENRICHER_ENABLED` defaults to `0`. With it unset, Sprint 1's tests and runtime behaviour are unchanged.
- **Post text is fenced before the LLM sees it.** The enricher calls `core.security.fence_untrusted` on the post text. Audit log stores sizes only, never prompt or response text.
- **Fail-open on LLM error.** If every model in the cascade raises/times out, the event dispatches **raw** (no enrichment, `below_threshold=False`); the operator must not lose a hit because the LLM is down.
- **Test for the whole suite passes.** `.venv/bin/python -m unittest discover -s tests -t .` must show no NEW failures vs Sprint 1's baseline (1 pipeline FAIL + 5 pytest-import ERRORs inherited from `main`).

## Review Focus

- **Malformed LLM JSON response.** Covered by Task 1's parse-error test (`test_enricher_rejects_non_json_response`).
- **Primary model timeout + fallback succeeds.** Covered by Task 2's cascade test (`test_cascade_falls_through_to_next_model_on_timeout`).
- **Every model in the cascade fails.** Covered by Task 2's all-fail test (`test_cascade_returns_none_when_all_models_fail`).
- **Sample rate zero yields zero non-hit calls but still enriches keyword hits.** Covered by Task 4's sampling test (`test_worker_always_enriches_filter_hits_regardless_of_sample_rate`).
- **Audit file rotation at 100 MB.** Covered by Task 3's rotation test (`test_audit_rotates_at_size_cap`).

---

### Task 1: Enricher core + EnrichmentResult + rate limit

**Files:**
- Create: `modules/listen/enricher.py`
- Create: `config/enricher_prompt.md` (the system prompt from spec §3, committed)
- Test: `tests/test_listen_enricher.py`

**Interfaces:**
- Consumes: `llm.get_llm`, `config.RobinConfig`, `core.security.fence_untrusted`.
- Produces:
  - `EnrichmentResult` (dataclass, spec §2.1, plus `to_dict()`).
  - `class Enricher` with `__init__(cfg: RobinConfig, *, model: str, timeout_sec: float = 30.0, max_concurrency: int = 2, prompt_path: Optional[Path] = None, audit=None)`.
  - `async def score_once(post: dict, entry: WatchlistEntry, reasons: list[str], *, model: Optional[str] = None) -> EnrichmentResult` — calls one named model, returns parsed result; raises `EnricherError` on timeout or parse failure.
  - `_build_prompt(post, entry, reasons) -> tuple[str, str]` — returns `(system, user)` strings; wraps post text with `fence_untrusted`.
  - `_parse_response(raw: str) -> EnrichmentResult` — strict JSON parse with a lenient fallback that strips Markdown code-fences (`json\n...\n`) around the JSON body.

- [ ] **Step 1: Write failing tests in `tests/test_listen_enricher.py`**

```python
# Signatures and intent only; implementer fills bodies to match these names.
# All tests use unittest.IsolatedAsyncioTestCase or unittest.TestCase.

def test_enricher_result_dataclass_roundtrips_to_dict()
def test_build_prompt_fences_post_text_and_includes_reasons_and_handle()
def test_parse_response_accepts_plain_json()
def test_parse_response_strips_markdown_code_fence()
def test_parse_response_raises_on_non_json()

class TestEnricher(unittest.IsolatedAsyncioTestCase):
    # Mock llm.get_llm to return an object whose `.ainvoke(messages)` is an
    # AsyncMock with a configurable return value / side_effect.
    async def test_score_once_returns_parsed_result_on_happy_path()
    async def test_score_once_times_out_and_raises_EnricherError()
    async def test_score_once_raises_EnricherError_on_malformed_json()
    async def test_rate_limit_semaphore_caps_concurrency_at_max()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_listen_enricher -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.listen.enricher'`.

- [ ] **Step 3: Implement `config/enricher_prompt.md` and `modules/listen/enricher.py`**

Prompt file: exact body from spec §3. Loaded at `Enricher.__init__` via `Path.read_text`; cached. Parsed into system/user halves by splitting on `## System` / `## Schema` / `## Scoring guidance` markers — the prompt file is a Markdown document, but the `## System` section's body (everything up to the next `##`) is the LLM system message; `## Schema` and `## Scoring guidance` are appended to the system message as-is.

`_build_prompt`: user message is a single line with four `key=value` pairs separated by spaces: `post_date=`, `channel=`, `reasons=` (comma-joined), `text=` (fenced via `core.security.fence_untrusted`).

`score_once`: constructs two-message list `[("system", sys_text), ("user", user_text)]`, calls `await asyncio.wait_for(model.ainvoke(messages), timeout=self.timeout_sec)`, extracts `.content`, passes through `_parse_response`.

Rate-limit: `Enricher._sem = asyncio.Semaphore(max_concurrency)`; `score_once` acquires it around the LLM call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_listen_enricher -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/listen/enricher.py config/enricher_prompt.md tests/test_listen_enricher.py
git commit -m "feat(listen): enricher core with grok-4.6 call, timeout, rate limit"
```

---

### Task 2: Model cascade (primary → fallbacks)

**Files:**
- Modify: `modules/listen/enricher.py` — add `score(...)` public method that walks the cascade.
- Extend: `tests/test_listen_enricher.py` with four cascade tests.

**Interfaces:**
- Consumes: Task 1 `score_once`, `EnricherError`.
- Produces:
  - `Enricher.__init__` grows `fallback_models: list[str] = []`.
  - `async def score(post, entry, reasons) -> Optional[EnrichmentResult]` — tries `self.model` then each of `self.fallback_models`; returns the first success, or `None` when every model fails. Each attempt is audited (see Task 3).
  - `class EnricherError(Exception)` — raised internally by `score_once`, caught by `score`.

- [ ] **Step 1: Extend failing tests in `tests/test_listen_enricher.py`**

```python
class TestCascade(unittest.IsolatedAsyncioTestCase):
    async def test_cascade_uses_primary_when_it_succeeds()
    async def test_cascade_falls_through_to_next_model_on_timeout()
    async def test_cascade_falls_through_on_parse_error()
    async def test_cascade_returns_none_when_all_models_fail()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_listen_enricher.TestCascade -v`
Expected: FAIL with `AttributeError: 'Enricher' object has no attribute 'score'` (or similar).

- [ ] **Step 3: Implement `Enricher.score` + fallback chain**

Iterate `[self.model, *self.fallback_models]`; for each, call `score_once(..., model=name)` inside a `try/except EnricherError`; return the first success. Log a `warning` per fallthrough naming the model and error class. Return `None` after all attempts.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_listen_enricher -v`
Expected: all tests (Task 1 + Task 2) PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/listen/enricher.py tests/test_listen_enricher.py
git commit -m "feat(listen): enricher model-cascade fallback (grok-4.6 → grok-4.5 → deepseek)"
```

---

### Task 3: Audit log (append-only JSONL with rotation)

**Files:**
- Create: `modules/listen/enricher_audit.py`
- Modify: `modules/listen/enricher.py` — call audit around each model attempt.
- Test: `tests/test_listen_enricher_audit.py`

**Interfaces:**
- Produces:
  - `class EnricherAudit` with `__init__(path: Path, *, rotate_bytes: int = 100 * 1024 * 1024)`.
  - `record(model: str, elapsed_ms: int, prompt_len: int, response_len: int, success: bool, error: Optional[str], watchlist_id: str, signature: str) -> None` — appends one JSON line, rotates if the file is over the cap.
  - Rotation scheme: rename `enricher_audit.jsonl` to `enricher_audit.YYYYMMDD.jsonl.N` where N is the first free integer; create a fresh file.

- [ ] **Step 1: Write failing tests**

```python
class TestAudit(unittest.TestCase):
    def test_audit_appends_one_json_line_per_record()
    def test_audit_never_writes_prompt_or_response_text()
    def test_audit_rotates_at_size_cap()
    def test_audit_tolerates_missing_parent_directory()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_listen_enricher_audit -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `enricher_audit.py` and wire into `Enricher.score`**

Audit writes JSON with only the fields in spec §2.3. Rotation is checked at each `record()` call; `os.stat(path).st_size >= rotate_bytes` triggers rename. `Enricher.score` calls `audit.record(...)` in a `finally` block of each attempt so both success and failure are captured.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_listen_enricher tests.test_listen_enricher_audit -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/listen/enricher_audit.py modules/listen/enricher.py tests/test_listen_enricher_audit.py
git commit -m "feat(listen): enricher audit log with 100MB rotation"
```

---

### Task 4: Dispatch path — MatchEvent extension, sink routing, bot format

**Files:**
- Modify: `modules/listen/events.py` — add `enrichment` + `below_threshold` fields.
- Modify: `modules/listen/sinks/investigation_json.py` — route rejected events to a `rejected/` subdirectory.
- Modify: `modules/listen/sinks/telegram_bot.py` — `format_message` prepends enrichment header, truncates excerpt to 800 chars.
- Modify: `modules/listen/dispatcher.py` — sinks that should skip below-threshold events decide for themselves (bot, WS); JSON sink handles both.
- Modify: `modules/listen/sinks/websocket.py` — skip below-threshold events.
- Test: extend `tests/test_listen_dispatcher.py`, `tests/test_listen_bot_sink.py`, `tests/test_listen_ws_sink.py`.

**Interfaces:**
- `MatchEvent` gains two optional fields with defaults (backward compat: Sprint-1 code paths and tests unaffected).
- `investigation_json_sink(investigations_dir)` directory layout:
  - `<dir>/listen_match_<stamp>.json` when `event.below_threshold=False`.
  - `<dir>/rejected/listen_reject_<stamp>.json` when `event.below_threshold=True`.
- `format_message(event)`: when `event.enrichment` present, first line is
  `Relevance <N>/10 - <banks | "no bank"> - <leak_type>`; second block
  is `summary_vi`; then the existing excerpt at 800 chars.

- [ ] **Step 1: Extend failing tests in the three sink test files**

```python
# tests/test_listen_dispatcher.py
async def test_investigation_json_sink_routes_below_threshold_to_rejected_subdir(...)
async def test_investigation_json_sink_keeps_above_threshold_in_parent_dir(...)

# tests/test_listen_bot_sink.py
async def test_bot_sink_skips_below_threshold_events()
def test_format_message_prepends_enrichment_header_when_present()
def test_format_message_falls_back_to_sprint1_format_when_no_enrichment()

# tests/test_listen_ws_sink.py
async def test_ws_sink_skips_below_threshold_events()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_listen_dispatcher tests.test_listen_bot_sink tests.test_listen_ws_sink -v`
Expected: 6 new tests fail; existing tests still pass.

- [ ] **Step 3: Implement the five source edits**

`events.py`: `enrichment: Optional[dict] = None`, `below_threshold: bool = False`.

`investigation_json_sink`: before computing the candidate name, pick the target directory as `investigations_dir / "rejected"` when `event.below_threshold`; use filename prefix `listen_reject_` instead of `listen_match_` in that branch.

`format_message`: if `event.enrichment` is a non-empty dict, prepend a header block; otherwise keep the current body. Adjust the excerpt truncation to 800 chars when the header block is present (keeping total under 4096).

`ws_sink` and `telegram_bot_sink`: early-return when `event.below_threshold is True`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_listen_dispatcher tests.test_listen_bot_sink tests.test_listen_ws_sink tests.test_listen_watchlist tests.test_listen_filters -v`
Expected: all PASS (new + old).

- [ ] **Step 5: Commit**

```bash
git add modules/listen/events.py modules/listen/sinks/investigation_json.py modules/listen/sinks/telegram_bot.py modules/listen/sinks/websocket.py tests/test_listen_dispatcher.py tests/test_listen_bot_sink.py tests/test_listen_ws_sink.py
git commit -m "feat(listen): MatchEvent enrichment + below_threshold sink routing"
```

---

### Task 5: Worker integration — poll_once calls enricher + threshold gate

**Files:**
- Modify: `modules/listen/workers/telegram_poll.py` — new `enricher` and `sample_rate` params on `poll_once`.
- Modify: `modules/listen/boot.py` — build Enricher + audit from env, pass into `poll_loop`.
- Modify: `.env.example` — add the eight enricher env vars from spec §4.1.
- Test: extend `tests/test_listen_poll_worker.py` and `tests/test_listen_boot.py`.

**Interfaces:**
- `poll_once` signature grows:
  ```
  async def poll_once(conn, entry, *, on_match, enricher=None, threshold=5,
                      sample_rate=1.0, rng=random.random) -> dict:
  ```
- When `enricher is None`: Sprint 1 behaviour exactly (current tests keep passing).
- When `enricher is not None`: for each new post, if `match_reasons` is non-empty OR `rng() < sample_rate`, call `await enricher.score(post, entry, reasons)`:
  - on `EnrichmentResult`: attach to event as `event.enrichment = result.to_dict()`, set `event.below_threshold = result.relevance < threshold`;
  - on `None` (all-fail): leave `event.enrichment = None`, `event.below_threshold = False` (fail-open).
- Keyword-filter hit still populates `match_reasons` but no longer gates dispatch; enrichment (or its absence under fail-open) does.

- [ ] **Step 1: Extend failing tests**

```python
# tests/test_listen_poll_worker.py — new class TestWorkerWithEnricher
async def test_worker_without_enricher_behaves_like_sprint1()
async def test_worker_always_enriches_filter_hits_regardless_of_sample_rate()
async def test_worker_skips_enrichment_when_sample_rate_zero_and_no_filter_hit()
async def test_worker_marks_below_threshold_when_relevance_under_cutoff()
async def test_worker_fails_open_when_enricher_returns_none()

# tests/test_listen_boot.py — one new test
async def test_start_listen_builds_enricher_when_env_enabled()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_listen_poll_worker tests.test_listen_boot -v`
Expected: new tests fail (enricher kwarg missing or boot does not construct one).

- [ ] **Step 3: Implement worker integration + boot wiring**

Worker: thread `enricher`, `threshold`, `sample_rate`, `rng` kwargs through `poll_once` and `poll_loop`. Build the `MatchEvent` from the post, then run the enrichment decision above, then await `on_match(event)` unconditionally (sinks decide what to skip via `below_threshold`).

Boot: read env vars from spec §4.1. If `LISTEN_ENRICHER_ENABLED=1`, build one `Enricher(cfg, model=...)` with `fallback_models` from the CSV env, wrap with an `EnricherAudit`, and pass it into every spawned `poll_loop` via the kwargs above. Also add `.env.example` entries.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_listen_poll_worker tests.test_listen_boot -v`
Expected: all PASS.

- [ ] **Step 5: Full suite + synthetic smoke**

Run: `.venv/bin/python -m unittest discover -s tests -t .`
Expected: 0 new failures vs Sprint 1 baseline (1 pipeline FAIL + 5 pytest-import ERRORs inherited).

Then write a scratchpad smoke script `listen_enricher_smoke.py` that:
1. patches `scrape_telegram_channel` to emit a VN-matching post,
2. patches `Enricher.score` to return a canned `EnrichmentResult(relevance=7, ...)`,
3. runs `start_listen` with `LISTEN_ENRICHER_ENABLED=1`,
4. confirms one `listen_match_<stamp>.json` appears (above threshold path),
5. patches a second run to return `relevance=3`,
6. confirms one `rejected/listen_reject_<stamp>.json` appears (below threshold path),
7. confirms no WS/bot send on the rejected run (assert `mock.send_str` not called).

- [ ] **Step 6: Commit**

```bash
git add modules/listen/workers/telegram_poll.py modules/listen/boot.py .env.example tests/test_listen_poll_worker.py tests/test_listen_boot.py
git commit -m "feat(listen): wire enricher into poll_once with threshold + sampling"
```

---

## Deferred (Sprint 2 feature 2+)

- `/listen/watchlist` CRUD + `/listen/feed` live stream — needs this feature's `enrichment` to display meaningfully.
- Telethon realtime worker — adds sources; same enricher applies unchanged.
- Tor .onion poller — adds sources; same enricher applies unchanged.
- Forum seed loader — adds sources; same enricher applies unchanged.

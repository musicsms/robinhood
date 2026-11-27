# Listen Mode (Sprint 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a background Listen Mode that polls a seeded watchlist of public Telegram channels, filters each new post for Vietnam-finance relevance, and on a match writes an investigation JSON, broadcasts a WebSocket event, and sends a Telegram-bot notification.

**Architecture:** A new `modules/listen/` package. SQLite watchlist DB at `data/listen/watchlist.db` seeded from `config/watchlist.yaml` and from `data/deepdarkCTI/telegram_infostealer.md`. A per-entry asyncio polling loop reuses the existing `modules.scrape.telegram.scrape_telegram_channel`, dedups by message signature, runs posts through a pure-function Vietnam-finance filter, and dispatches matches to three sinks: a JSON writer reusing `store`'s collision scheme, a WebSocket broadcaster on `/ws/listen` added to the viewer's aiohttp app, and a Telegram-bot HTTP POST. The loop starts from `main.py` when `ROBIN_LISTEN_ENABLED=1`; it shares the viewer's event loop so `/ws/listen` and the worker coexist cleanly.

**Tech Stack:** Python 3.14, `aiohttp` (already in requirements), stdlib `sqlite3`, `pyyaml` (already pulled in by `langchain_community`), stdlib `re`/`unicodedata`, `requests` for the bot sink (already in requirements).

**Spec:** `docs/superpowers/specs/2026-11-27-listen-mode-design.md`

## Global Constraints

- **Python 3.14 venv at `.venv/`.** All test commands run as `.venv/bin/pytest ...`.
- **Scraped post text stays untrusted.** The listen pipeline reads the scrubbed `posts[*]['text']` returned by `scrape_telegram_channel`, never raw HTML. The investigation-JSON sink persists the fenced form produced by that scraper.
- **Watchlist DB at `data/listen/watchlist.db`.** Opened with WAL mode, file mode `0600`. The `data/` directory is already `.gitignore`d.
- **Bot token and chat_id are read from env, never logged.** A failed `sendMessage` logs only the HTTP status and a short reason, not the body.
- **Default off.** `ROBIN_LISTEN_ENABLED` defaults to `0`. With it unset, no new coroutine starts and existing tests must still pass.
- **No new third-party dependency.** `pyyaml`, `requests`, `aiohttp`, `sqlite3` are already available.

## Review Focus

- **Duplicate posts across polls.** Covered by Task 4's dedup test (`test_polling_worker_dedups_second_pass`).
- **Channel disappears or returns HTTP 404.** Covered by Task 4's error-path test (`test_polling_worker_records_last_error_and_continues`).
- **Rate-limit (HTTP 429) from t.me.** Covered by Task 4's backoff test (`test_polling_worker_backs_off_on_429`).
- **Bot token missing or chat_id wrong.** Covered by Task 7's no-op test (`test_bot_sink_no_ops_when_env_unset`) and error-swallowing test (`test_bot_sink_logs_but_does_not_raise_on_api_error`).
- **Keyword substring false-positive (e.g., `acb` inside an onion hash).** Covered by Task 3's word-boundary test (`test_filter_rejects_substring_matches_inside_hashes`).

---

### Task 1: Watchlist storage layer

**Files:**
- Create: `modules/listen/__init__.py` (empty marker)
- Create: `modules/listen/watchlist.py`
- Create: `config/watchlist.yaml` (empty list `[]` as a committed stub)
- Test: `tests/test_listen_watchlist.py`

**Interfaces:**
- Produces:
  - `open_db(path: pathlib.Path) -> sqlite3.Connection` — WAL mode, file mode 0600, schema from spec §2.1 initialised idempotently.
  - `WatchlistEntry` (dataclass with fields: `id: str, source_type: str, target: str, interval_sec: int, enabled: bool, seed_from: Optional[str], tags: list[str], last_polled: Optional[str], last_error: Optional[str]`).
  - `upsert(conn, entry: WatchlistEntry) -> None` — `INSERT OR REPLACE` by id; preserves `last_polled` and `last_error` if the caller passed `None` for them.
  - `list_enabled(conn) -> list[WatchlistEntry]`.
  - `record_poll(conn, watchlist_id: str, when: datetime, error: Optional[str]) -> None`.
  - `mark_seen(conn, watchlist_id: str, signature: str, when: datetime) -> bool` — returns `True` if the row was inserted (new), `False` if it already existed (duplicate).
  - `prune_seen_older_than(conn, cutoff: datetime) -> int` — delete + return row count.
  - `load_yaml_seed(path: pathlib.Path) -> list[WatchlistEntry]` — reads the YAML file, validates each entry's required fields, returns in file order.

- [ ] **Step 1: Write failing tests in `tests/test_listen_watchlist.py`**

```python
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from modules.listen.watchlist import (
    WatchlistEntry, open_db, upsert, list_enabled,
    record_poll, mark_seen, prune_seen_older_than, load_yaml_seed,
)

def test_open_db_creates_schema_idempotently(tmp_path):
    db = tmp_path / "w.db"
    c1 = open_db(db); c1.close()
    c2 = open_db(db); c2.close()  # must not error
    assert db.stat().st_mode & 0o777 == 0o600

def test_upsert_then_list_enabled_roundtrips_all_fields(tmp_path):
    conn = open_db(tmp_path / "w.db")
    e = WatchlistEntry(id="a", source_type="telegram_web", target="@x",
                       interval_sec=60, enabled=True, seed_from="s", tags=["t"],
                       last_polled=None, last_error=None)
    upsert(conn, e)
    rows = list_enabled(conn)
    assert len(rows) == 1 and rows[0].id == "a" and rows[0].tags == ["t"]

def test_mark_seen_returns_false_on_duplicate(tmp_path):
    conn = open_db(tmp_path / "w.db")
    now = datetime.now(timezone.utc)
    assert mark_seen(conn, "a", "sig1", now) is True
    assert mark_seen(conn, "a", "sig1", now) is False

def test_prune_seen_removes_older_than_cutoff(tmp_path):
    conn = open_db(tmp_path / "w.db")
    old = datetime.now(timezone.utc) - timedelta(days=30)
    new = datetime.now(timezone.utc)
    mark_seen(conn, "a", "old", old)
    mark_seen(conn, "a", "new", new)
    assert prune_seen_older_than(conn, datetime.now(timezone.utc) - timedelta(days=7)) == 1

def test_load_yaml_seed_parses_committed_stub(tmp_path):
    p = tmp_path / "w.yaml"
    p.write_text("- id: a\n  source_type: telegram_web\n  target: '@x'\n  interval_sec: 60\n  enabled: true\n  tags: [t]\n", encoding="utf-8")
    entries = load_yaml_seed(p)
    assert len(entries) == 1 and entries[0].id == "a"

def test_load_yaml_seed_returns_empty_list_for_empty_file(tmp_path):
    p = tmp_path / "w.yaml"; p.write_text("[]\n", encoding="utf-8")
    assert load_yaml_seed(p) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_listen_watchlist.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.listen'`.

- [ ] **Step 3: Implement `modules/listen/__init__.py` (empty) and `modules/listen/watchlist.py`**

Use `sqlite3.connect(path, check_same_thread=False)`, enable WAL with `PRAGMA journal_mode=WAL`, `os.chmod(path, 0o600)` after first create. Encode `tags` as `json.dumps`; decode on read. `record_poll` updates `last_polled` and `last_error` by id. `load_yaml_seed` uses `yaml.safe_load`; treats `None` (empty document) as `[]`; raises `ValueError` if an entry is missing one of `id`/`source_type`/`target`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_listen_watchlist.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Confirm `data/` is gitignored and create the committed YAML stub**

Run: `grep -E '^data/$|^data$|^/data' .gitignore`
Expected: at least one match. If none, add `data/listen/` to `.gitignore` in this commit.

Write `config/watchlist.yaml` with contents `[]\n` and a one-line header comment.

- [ ] **Step 6: Commit**

```bash
git add modules/listen/__init__.py modules/listen/watchlist.py config/watchlist.yaml tests/test_listen_watchlist.py .gitignore
git commit -m "feat(listen): watchlist SQLite layer + YAML seed loader"
```

---

### Task 2: deepdarkCTI seed loader

**Files:**
- Create: `modules/listen/seed.py`
- Test: `tests/test_listen_seed.py`

**Interfaces:**
- Consumes: Task 1's `WatchlistEntry`, `upsert`, `open_db`.
- Produces:
  - `extract_telegram_channels(md_path: pathlib.Path) -> list[str]` — returns unique channel handles (not URLs) found in a deepdarkCTI markdown file, preserving first-seen order.
  - `seed_from_deepdarkcti(conn, md_path: pathlib.Path, *, source_type: str = "telegram_web", default_interval_sec: int = 60, tags: list[str] | None = None) -> int` — upserts one entry per unique handle with a stable `id` of `"ddcti-" + sha1(handle)[:12]`, returns the number of new rows (ignores already-present ids).

- [ ] **Step 1: Write failing tests in `tests/test_listen_seed.py`**

```python
from pathlib import Path
from modules.listen.watchlist import open_db, list_enabled
from modules.listen.seed import extract_telegram_channels, seed_from_deepdarkcti

def test_extract_telegram_channels_from_sample(tmp_path):
    md = tmp_path / "x.md"
    md.write_text("|[Chan A](https://t.me/chan_a)|ONLINE|\n"
                  "|[Chan B](https://t.me/s/chan_b)|ONLINE|\n"
                  "|[Chan A again](https://t.me/chan_a)|OFFLINE|\n"
                  "|[Private](https://t.me/joinchat/XYZ)|OFFLINE|\n")
    assert extract_telegram_channels(md) == ["chan_a", "chan_b"]

def test_seed_on_real_infostealer_file_inserts_98(tmp_path):
    conn = open_db(tmp_path / "w.db")
    path = Path("data/deepdarkCTI/telegram_infostealer.md")
    inserted = seed_from_deepdarkcti(conn, path, tags=["infostealer"])
    assert inserted == 98
    assert len(list_enabled(conn)) == 98
    # second call is a no-op
    assert seed_from_deepdarkcti(conn, path, tags=["infostealer"]) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_listen_seed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.listen.seed'`.

- [ ] **Step 3: Implement `modules/listen/seed.py`**

`extract_telegram_channels` reuses `modules.scrape.telegram.extract_telegram_channel_name` on each URL found by a `re.finditer(r'https?://t\.me/[^\s)\]]+', text)`, dedups preserving order. `seed_from_deepdarkcti` iterates the result, builds an id with `hashlib.sha1(handle.encode()).hexdigest()[:12]` prefixed `ddcti-`, calls `upsert` with `enabled=True`, `interval_sec=default_interval_sec`, `seed_from=md_path.name`, and tags as passed (default `[]`). Counts only inserts of new ids (query `SELECT 1 FROM watchlist WHERE id=?` before `upsert`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_listen_seed.py -v`
Expected: both tests PASS. The count `98` is pinned to today's file (shell grep that produced "99" also matched the shared `t.me/joinchat` prefix; the real-channel count is 98 after filtering invite links); if a run fails with a different number, the upstream file changed — update both the spec and the test assertion in one commit, don't loosen the test to `> 0`.

- [ ] **Step 5: Commit**

```bash
git add modules/listen/seed.py tests/test_listen_seed.py
git commit -m "feat(listen): seed watchlist from deepdarkCTI telegram_infostealer"
```

---

### Task 3: Vietnam-finance keyword filter

**Files:**
- Create: `modules/listen/filters.py`
- Test: `tests/test_listen_filters.py`

**Interfaces:**
- Produces:
  - `normalize(text: str) -> str` — NFKD-strip diacritics, casefold, collapse whitespace.
  - `match_vn_finance(text: str) -> tuple[bool, list[str]]` — `(matched, reasons)` where each reason is `"class:match_literal"`. Classes and regexes from spec §3. Uses `\b` word boundaries except for the `phone` class. The `geo` class's `.vn` branch matches only when preceded by a non-word character or start-of-string and followed by a non-word character or end-of-string (so `vuln.vn` does, but `evn.vnc` does not).

- [ ] **Step 1: Write failing tests in `tests/test_listen_filters.py`**

```python
import pytest
from modules.listen.filters import normalize, match_vn_finance

def test_normalize_strips_diacritics_and_casefolds():
    assert normalize("Ngân Hàng") == "ngan hang"

def test_match_hits_bank_class():
    matched, reasons = match_vn_finance("Full logs including Vietcombank creds")
    assert matched is True
    assert any(r.startswith("bank:vietcombank") for r in reasons)

def test_match_hits_geo_and_vn_text():
    matched, reasons = match_vn_finance("Rò rỉ tài khoản ngân hàng Việt Nam")
    assert matched is True
    classes = {r.split(":", 1)[0] for r in reasons}
    assert "vn_text" in classes and "geo" in classes

def test_match_hits_phone_e164_and_national():
    assert match_vn_finance("contact +84 912 345 678")[0] is True
    assert match_vn_finance("call 0912345678 now")[0] is True

def test_filter_rejects_substring_matches_inside_hashes():
    # 'acb' and 'vcb' appear inside onion hashes in deepdarkCTI; must NOT match.
    noise = "pear peargxn3oki34c4savcbcfqofjjwjnnyrlrbszfv6ujlx36mhrh57did.onion acb_in_hash"
    matched, reasons = match_vn_finance(noise)
    assert matched is False, f"expected no match, got reasons={reasons}"

def test_filter_rejects_empty_and_whitespace():
    assert match_vn_finance("")[0] is False
    assert match_vn_finance("   \n  ")[0] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_listen_filters.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `modules/listen/filters.py`**

`normalize` uses `unicodedata.normalize("NFKD", text)` then strips combining marks with `"".join(c for c in s if not unicodedata.combining(c))`, then `.casefold()`, then `re.sub(r"\s+", " ", s).strip()`. `match_vn_finance` compiles the five regexes once at module import. The function normalises the input, runs each regex with `re.finditer`, aggregates matches, and returns `(bool(reasons), reasons)`. For word-boundary safety, bank and payment and vn_text patterns use `r"\b(...)\b"`; `acb` inside a longer alphanumeric run (like `peargxn3oki34c4savcbcfqofjjwjnnyrlrbszfv6ujlx36mhrh57did`) has no `\b` boundary on either side and does not match.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_listen_filters.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/listen/filters.py tests/test_listen_filters.py
git commit -m "feat(listen): Vietnam-finance keyword filter with diacritic normalization"
```

---

### Task 4: Telegram polling worker

**Files:**
- Create: `modules/listen/workers/__init__.py` (empty)
- Create: `modules/listen/workers/telegram_poll.py`
- Create: `modules/listen/events.py` — holds the `MatchEvent` dataclass from spec §2.2; imported by Tasks 4, 5, 6, 7.
- Test: `tests/test_listen_poll_worker.py`

**Interfaces:**
- Consumes: Task 1 (`open_db`, `mark_seen`, `record_poll`, `WatchlistEntry`), Task 3 (`match_vn_finance`), existing `modules.scrape.telegram.scrape_telegram_channel`.
- Produces:
  - `MatchEvent` (dataclass, spec §2.2).
  - `signature(post: dict) -> str` — sha256 of `(post["date"] + "|" + post["text"][:256])`, hex-digested.
  - `async def poll_once(conn, entry: WatchlistEntry, *, scraper=scrape_telegram_channel, on_match: Callable[[MatchEvent], Awaitable[None]], clock=datetime.now) -> dict` — runs one scrape, dedups, filters, awaits `on_match` per hit, calls `record_poll`, returns `{"posts": N_scraped, "new": N_unseen, "matches": N_dispatched, "error": Optional[str]}`.
  - `async def poll_loop(conn, entry: WatchlistEntry, on_match, *, stop_event: asyncio.Event) -> None` — sleeps `entry.interval_sec` between `poll_once` calls; on exception `logger.warning` and continue; on HTTP 429 double the effective sleep up to 1 hour and reset on next success (tracked in a local variable, not the DB).

- [ ] **Step 1: Write failing tests in `tests/test_listen_poll_worker.py`**

```python
import asyncio
from datetime import datetime, timezone
from unittest import mock

import pytest

from modules.listen.watchlist import open_db, upsert, WatchlistEntry
from modules.listen.workers.telegram_poll import poll_once, signature
from modules.listen.events import MatchEvent

def _entry(id_="a"):
    return WatchlistEntry(id=id_, source_type="telegram_web", target="@x",
                          interval_sec=60, enabled=True, seed_from=None,
                          tags=[], last_polled=None, last_error=None)

def _fake_scrape_result(posts):
    return {"status": "ok", "url": "https://t.me/s/x", "channel_name": "x",
            "title": "X", "posts_count": len(posts),
            "text": "fenced", "posts": posts}

@pytest.mark.asyncio
async def test_polling_worker_emits_matches_for_vn_hits(tmp_path):
    conn = open_db(tmp_path / "w.db"); upsert(conn, _entry())
    seen = []
    async def sink(ev: MatchEvent): seen.append(ev)
    posts = [
        {"date": "2026-11-27T10:00:00Z", "views": "1", "text": "Vietcombank full dump"},
        {"date": "2026-11-27T11:00:00Z", "views": "2", "text": "unrelated noise"},
    ]
    with mock.patch("modules.listen.workers.telegram_poll.scrape_telegram_channel",
                    return_value=_fake_scrape_result(posts)) as scraper_mock:
        # scraper is a regular function; wrap to be callable from sync path
        from modules.listen.workers import telegram_poll as mod
        mod.scrape_telegram_channel = scraper_mock
        out = await poll_once(conn, _entry(), on_match=sink)
    assert out == {"posts": 2, "new": 2, "matches": 1, "error": None}
    assert len(seen) == 1 and "bank:vietcombank" in seen[0].match_reasons

@pytest.mark.asyncio
async def test_polling_worker_dedups_second_pass(tmp_path):
    conn = open_db(tmp_path / "w.db"); upsert(conn, _entry())
    sink_calls = []
    async def sink(ev): sink_calls.append(ev)
    posts = [{"date": "d", "views": "", "text": "Vietcombank leak"}]
    from modules.listen.workers import telegram_poll as mod
    mod.scrape_telegram_channel = lambda *a, **k: _fake_scrape_result(posts)
    await poll_once(conn, _entry(), on_match=sink)
    await poll_once(conn, _entry(), on_match=sink)
    assert len(sink_calls) == 1

@pytest.mark.asyncio
async def test_polling_worker_records_last_error_and_continues(tmp_path):
    conn = open_db(tmp_path / "w.db"); upsert(conn, _entry())
    from modules.listen.workers import telegram_poll as mod
    def bad_scraper(*a, **k):
        return {"status": "http_404", "url": "https://t.me/s/x", "posts_count": 0,
                "text": "", "posts": []}
    mod.scrape_telegram_channel = bad_scraper
    async def sink(ev): pass
    out = await poll_once(conn, _entry(), on_match=sink)
    assert out["matches"] == 0 and out["error"] == "http_404"
    row = conn.execute("SELECT last_error FROM watchlist WHERE id=?", ("a",)).fetchone()
    assert row[0] == "http_404"

@pytest.mark.asyncio
async def test_polling_worker_backs_off_on_429(tmp_path):
    # poll_loop local-variable backoff: we test poll_once reports the condition,
    # and poll_loop multiplies the sleep; we assert the signal, not the sleep itself.
    conn = open_db(tmp_path / "w.db"); upsert(conn, _entry())
    from modules.listen.workers import telegram_poll as mod
    mod.scrape_telegram_channel = lambda *a, **k: {
        "status": "http_429", "url": "u", "posts_count": 0, "text": "", "posts": []
    }
    async def sink(ev): pass
    out = await poll_once(conn, _entry(), on_match=sink)
    assert out["error"] == "http_429"
```

Add `pytest-asyncio` only if not already present. Check with `.venv/bin/pip show pytest-asyncio`. If absent, add to `requirements.txt` in this task's commit and install.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_listen_poll_worker.py -v`
Expected: FAIL (either `ModuleNotFoundError` or `pytest-asyncio` missing).

- [ ] **Step 3: Implement `modules/listen/events.py` and `modules/listen/workers/telegram_poll.py`**

`events.py` holds only the `MatchEvent` dataclass from spec §2.2 plus a `to_dict()` helper (for JSON sinks).

`telegram_poll.py` imports `scrape_telegram_channel` at module top, exposes it as a module-level name so tests can monkey-patch it. `poll_once` calls the scraper on `entry.target`, iterates the returned `posts` list (reverse already applied by the scraper so newest-first), computes `signature`, calls `mark_seen`; for each unseen post whose text matches `match_vn_finance`, builds a `MatchEvent` and awaits `on_match(event)`. If the scraper's `status` is anything other than `"ok"`, record it as the error and return early.

`poll_loop` uses a local `backoff_mult: float = 1.0`; after `poll_once` returns `error=="http_429"`, `backoff_mult = min(backoff_mult * 2, 3600 / entry.interval_sec)`; on success resets to 1.0. The sleep is `entry.interval_sec * backoff_mult`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_listen_poll_worker.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/listen/events.py modules/listen/workers/__init__.py modules/listen/workers/telegram_poll.py tests/test_listen_poll_worker.py requirements.txt
git commit -m "feat(listen): telegram polling worker with dedup and backoff"
```

---

### Task 5: Match dispatcher + investigation-JSON sink

**Files:**
- Create: `modules/listen/dispatcher.py`
- Create: `modules/listen/sinks/__init__.py` (empty)
- Create: `modules/listen/sinks/investigation_json.py`
- Test: `tests/test_listen_dispatcher.py`

**Interfaces:**
- Consumes: Task 4 (`MatchEvent`), existing `store.resolve_dir`, existing `store._write_new_file` pattern (but the listen dispatcher writes under its own filename prefix, not through `store.save_investigation`, because the record shape is a match, not an investigation).
- Produces:
  - `class Dispatcher` with `async def dispatch(event: MatchEvent) -> None` that awaits each registered sink in order and swallows per-sink exceptions (logs, continues).
  - `register_sink(sink: Callable[[MatchEvent], Awaitable[None]]) -> None`.
  - `investigation_json_sink(investigations_dir: Path) -> Callable[[MatchEvent], Awaitable[None]]` — factory that returns an async sink closed over the target directory. The file name is `listen_match_YYYYMMDD_HHMMSS[_N].json`; the body is `json.dumps(event.to_dict(), indent=2)`.

- [ ] **Step 1: Write failing test in `tests/test_listen_dispatcher.py`**

```python
import asyncio, json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from modules.listen.events import MatchEvent
from modules.listen.dispatcher import Dispatcher
from modules.listen.sinks.investigation_json import investigation_json_sink

def _event():
    return MatchEvent(event_id="abc", timestamp="2026-11-27T10:00:00+00:00",
                      watchlist_id="a", source_type="telegram_web",
                      target="@x", post_date="d", post_views="",
                      post_text="Vietcombank leak",
                      match_reasons=["bank:vietcombank"])

@pytest.mark.asyncio
async def test_dispatcher_fans_out_and_swallows_sink_errors():
    d = Dispatcher()
    seen = []
    async def good(ev): seen.append(("good", ev.event_id))
    async def bad(ev): raise RuntimeError("boom")
    d.register_sink(good); d.register_sink(bad); d.register_sink(good)
    await d.dispatch(_event())
    assert seen == [("good", "abc"), ("good", "abc")]

@pytest.mark.asyncio
async def test_investigation_json_sink_writes_file(tmp_path):
    sink = investigation_json_sink(tmp_path)
    await sink(_event())
    files = sorted(tmp_path.glob("listen_match_*.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text())
    assert body["watchlist_id"] == "a" and body["match_reasons"] == ["bank:vietcombank"]

@pytest.mark.asyncio
async def test_investigation_json_sink_two_calls_same_second_do_not_collide(tmp_path):
    sink = investigation_json_sink(tmp_path)
    await sink(_event()); await sink(_event())
    assert len(list(tmp_path.glob("listen_match_*.json"))) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_listen_dispatcher.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `dispatcher.py` and `sinks/investigation_json.py`**

`Dispatcher.dispatch` iterates sinks with `for sink in self._sinks: try: await sink(event) except Exception: logger.warning(...)`. `investigation_json_sink` mirrors `store._candidate_names` but with prefix `listen_match_` and opens with `"x"` to claim a free name without collision.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_listen_dispatcher.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/listen/dispatcher.py modules/listen/sinks/__init__.py modules/listen/sinks/investigation_json.py tests/test_listen_dispatcher.py
git commit -m "feat(listen): match dispatcher + investigation JSON sink"
```

---

### Task 6: WebSocket sink and `/ws/listen` endpoint

**Files:**
- Create: `modules/listen/sinks/websocket.py`
- Modify: `modules/viewer/server.py` — add `/ws/listen` handler and the client set in `create_viewer_app` (follow the existing WS pattern in the file).
- Test: `tests/test_listen_ws_sink.py`
- Test: extend `tests/test_viewer_server.py` with one case for `/ws/listen` subscribe-and-receive.

**Interfaces:**
- Consumes: Task 4 (`MatchEvent`), aiohttp `web.Application`.
- Produces:
  - `class WebSocketBroadcaster` with `register(ws: web.WebSocketResponse) -> None`, `unregister(ws) -> None`, and `async def broadcast(event: MatchEvent) -> None`.
  - `ws_sink(broadcaster: WebSocketBroadcaster) -> Callable[[MatchEvent], Awaitable[None]]` — factory returning an async sink.
  - In `modules/viewer/server.py`: `LISTEN_WS_CLIENTS_KEY = web.AppKey("listen_ws_clients", set)` and `LISTEN_BROADCASTER_KEY = web.AppKey("listen_broadcaster", WebSocketBroadcaster)`, plus `async def handle_listen_ws(request) -> web.WebSocketResponse` that registers the ws, pings on messages, and unregisters on close. Route `app.router.add_get("/ws/listen", handle_listen_ws)`.

- [ ] **Step 1: Write failing tests in `tests/test_listen_ws_sink.py`**

```python
import asyncio, json
import pytest
from unittest import mock

from modules.listen.events import MatchEvent
from modules.listen.sinks.websocket import WebSocketBroadcaster, ws_sink

def _event():
    return MatchEvent(event_id="e1", timestamp="t", watchlist_id="a",
                      source_type="telegram_web", target="@x", post_date="",
                      post_views="", post_text="x", match_reasons=["bank:vcb"])

@pytest.mark.asyncio
async def test_broadcaster_sends_to_all_live_clients():
    b = WebSocketBroadcaster()
    c1 = mock.AsyncMock(); c2 = mock.AsyncMock()
    b.register(c1); b.register(c2)
    await b.broadcast(_event())
    c1.send_str.assert_awaited_once()
    c2.send_str.assert_awaited_once()
    assert json.loads(c1.send_str.call_args.args[0])["event_id"] == "e1"

@pytest.mark.asyncio
async def test_broadcaster_drops_client_whose_send_raised():
    b = WebSocketBroadcaster()
    dead = mock.AsyncMock()
    dead.send_str.side_effect = RuntimeError
    b.register(dead)
    await b.broadcast(_event())
    assert dead not in b._clients
```

And in `tests/test_viewer_server.py` add one case:

```python
# (adapt to the file's existing AioHTTPTestCase structure)
async def test_ws_listen_route_accepts_subscribe(self):
    async with self.client.ws_connect("/ws/listen") as ws:
        await ws.send_str("ping")
        msg = await ws.receive(timeout=1.0)
        assert msg.type.name in ("TEXT", "CLOSE")  # server either echoes or holds open
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_listen_ws_sink.py tests/test_viewer_server.py -v -k "listen or ws_listen"`
Expected: `test_listen_ws_sink.py` all fail with `ModuleNotFoundError`; the viewer case fails with 404.

- [ ] **Step 3: Implement `sinks/websocket.py` and wire `/ws/listen` into `modules/viewer/server.py`**

`WebSocketBroadcaster._clients: set[web.WebSocketResponse]`. `broadcast` serialises the event once, iterates a `list(self._clients)` copy, and removes any that raise on `send_str`. `handle_listen_ws` mirrors the existing viewer WS handler but registers into the listen broadcaster. `create_viewer_app` builds and stores one broadcaster per app in `LISTEN_BROADCASTER_KEY`; the listen boot code (Task 7's integration bit) reaches it via `app[LISTEN_BROADCASTER_KEY]`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_listen_ws_sink.py tests/test_viewer_server.py -v`
Expected: PASS. Also confirm no regression on the existing viewer tests.

- [ ] **Step 5: Commit**

```bash
git add modules/listen/sinks/websocket.py modules/viewer/server.py tests/test_listen_ws_sink.py tests/test_viewer_server.py
git commit -m "feat(listen): WebSocket sink and /ws/listen endpoint"
```

---

### Task 7: Telegram bot notifier sink + boot wiring

**Files:**
- Create: `modules/listen/sinks/telegram_bot.py`
- Create: `modules/listen/boot.py`
- Modify: `main.py` — call `modules.listen.boot.start_listen` when `ROBIN_LISTEN_ENABLED=1`.
- Modify: `.env.example` — append the five env vars from spec §4.1.
- Test: `tests/test_listen_bot_sink.py`
- Test: `tests/test_listen_boot.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces:
  - `telegram_bot_sink(token: Optional[str], chat_id: Optional[str], *, http_post=requests.post, timeout: float = 10.0) -> Callable[[MatchEvent], Awaitable[None]]` — factory. If either arg is falsy the returned sink is a no-op that logs a single `warning` the first time only.
  - `format_message(event: MatchEvent) -> str` — renders a one-screen message under 4096 chars (Telegram bot limit); includes the target, the match reasons, a 1200-char excerpt of the post text, and no raw URLs beyond `https://t.me/s/<handle>`.
  - `start_listen(app: web.Application, cfg: RobinConfig) -> list[asyncio.Task]` — opens the DB, runs the YAML seed loader, runs the deepdarkCTI infostealer seed (once per boot), registers the three sinks on the dispatcher, and launches one `poll_loop` per enabled entry. Returns the task list for test introspection. Registers an `on_cleanup` handler on the app to cancel them.

- [ ] **Step 1: Write failing tests in `tests/test_listen_bot_sink.py` and `tests/test_listen_boot.py`**

```python
# tests/test_listen_bot_sink.py
import pytest
from unittest import mock
from modules.listen.events import MatchEvent
from modules.listen.sinks.telegram_bot import telegram_bot_sink, format_message

def _event():
    return MatchEvent(event_id="e1", timestamp="t", watchlist_id="a",
                      source_type="telegram_web", target="@x", post_date="d",
                      post_views="", post_text="Vietcombank leak",
                      match_reasons=["bank:vietcombank"])

@pytest.mark.asyncio
async def test_bot_sink_no_ops_when_env_unset(caplog):
    sink = telegram_bot_sink(token=None, chat_id=None)
    await sink(_event()); await sink(_event())
    # The sink must warn exactly once, not twice.
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1

@pytest.mark.asyncio
async def test_bot_sink_calls_http_post_with_formatted_body():
    resp = mock.MagicMock(status_code=200)
    poster = mock.MagicMock(return_value=resp)
    sink = telegram_bot_sink(token="T", chat_id="C", http_post=poster)
    await sink(_event())
    poster.assert_called_once()
    assert poster.call_args.args[0] == "https://api.telegram.org/botT/sendMessage"
    payload = poster.call_args.kwargs["json"]
    assert payload["chat_id"] == "C" and "Vietcombank leak" in payload["text"]

@pytest.mark.asyncio
async def test_bot_sink_logs_but_does_not_raise_on_api_error(caplog):
    resp = mock.MagicMock(status_code=400, text="bad request")
    poster = mock.MagicMock(return_value=resp)
    sink = telegram_bot_sink(token="T", chat_id="C", http_post=poster)
    await sink(_event())  # must not raise

def test_format_message_under_telegram_limit():
    e = _event(); e.post_text = "x" * 20000
    assert len(format_message(e)) <= 4096
```

```python
# tests/test_listen_boot.py
import os, asyncio
from pathlib import Path
import pytest
from aiohttp import web
from modules.listen.boot import start_listen

@pytest.mark.asyncio
async def test_start_listen_boots_with_empty_yaml_and_no_bot(tmp_path, monkeypatch):
    monkeypatch.setenv("LISTEN_WATCHLIST_DB_PATH", str(tmp_path / "w.db"))
    monkeypatch.setenv("LISTEN_WATCHLIST_YAML_PATH", str(tmp_path / "empty.yaml"))
    (tmp_path / "empty.yaml").write_text("[]\n")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_CHAT_ID", raising=False)
    app = web.Application()
    from config import RobinConfig
    tasks = await start_listen(app, RobinConfig.from_env())
    assert tasks == []  # no entries → no poll loops
    # cleanup registered
    assert app.on_cleanup
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_listen_bot_sink.py tests/test_listen_boot.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the three files**

`sinks/telegram_bot.py`: `telegram_bot_sink` returns an async closure that, if `token and chat_id`, calls `http_post` in a thread via `asyncio.to_thread` (since `requests` is sync); checks `resp.status_code`; logs `logger.warning("bot sink: HTTP %s", status)` on non-200 without the response body. Carries a `_warned` nonlocal to log the missing-env warning once.

`format_message`: `f"🔔 {event.watchlist_id} ({event.source_type})\n{', '.join(event.match_reasons)}\n{event.target}\n\n{event.post_text[:1200]}\n\n→ https://t.me/s/{event.target.lstrip('@')}"` truncated at 4096.

`boot.py`: reads env, opens DB, calls `load_yaml_seed` then upserts each, runs `seed_from_deepdarkcti` on `data/deepdarkCTI/telegram_infostealer.md` (idempotent, so safe at every boot), builds a `Dispatcher` with the three sinks (`investigation_json_sink(cfg.investigations_dir)`, `ws_sink(app[LISTEN_BROADCASTER_KEY])` guarded with `if LISTEN_BROADCASTER_KEY in app`, and `telegram_bot_sink` from env), then `list_enabled` → spawn one `asyncio.create_task(poll_loop(conn, entry, dispatcher.dispatch, stop_event=stop))` per entry. Register a cleanup that sets the stop event and awaits the tasks.

`main.py`: after building the app but before running it, check `os.getenv("ROBIN_LISTEN_ENABLED") == "1"` and if so `await start_listen(app, cfg)`. This requires `run_viewer_server` to expose a seam to run code against the app before `web.run_app` — if it does not (verify by reading `modules/viewer/server.py` first in this step), extend it with an `on_startup` callback parameter, kept backwards-compatible.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_listen_bot_sink.py tests/test_listen_boot.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Full smoke — the whole Sprint 1 pipeline end-to-end**

Create `scripts/listen_smoke.py` (not committed; `.gitignore`d or run from scratchpad):

```python
import asyncio, os
os.environ["ROBIN_LISTEN_ENABLED"] = "1"
from dotenv import load_dotenv; load_dotenv()
# Point at a tmp DB and a yaml that contains one known-active test channel.
# After 90 seconds, inspect data/investigations/ for listen_match_*.json
```

Run: `.venv/bin/python scripts/listen_smoke.py` for 90 seconds against a single known-active test channel the user picks; confirm one `listen_match_*.json` appears OR explain why no match (post dates out of window, keyword absent, etc.). Skip the bot assertion if `TELEGRAM_BOT_TOKEN` is unset — that's covered by the no-op test.

- [ ] **Step 6: Run the whole repo test suite**

Run: `.venv/bin/pytest -q`
Expected: no regressions on existing tests; new tests pass.

- [ ] **Step 7: Commit**

```bash
git add modules/listen/sinks/telegram_bot.py modules/listen/boot.py main.py .env.example tests/test_listen_bot_sink.py tests/test_listen_boot.py
git commit -m "feat(listen): Telegram bot sink and boot wiring"
```

---

## Sprint 2 (follow-up plans, out of scope here)

These remain to be specified and planned separately, after Sprint 1 ships:

- **Tor .onion poller** for `ransomware_gang.md` leak blogs (23 sites) — needs `pysocks` wiring already present, interval ≥ 15 min, per-host concurrency cap.
- **Forum seed loader** for `forum.md` with a top-N whitelist.
- **LLM enricher** using the configured `grok-4.6` custom model, called only on filter-hit posts; adds `relevance`, `banks_mentioned`, `leak_type` to the match event before dispatch.
- **Telethon realtime worker** for channels the user adds manually with `TELEGRAM_API_ID`/`HASH`/`PHONE` env.
- **Web Console UI** — a `/listen/watchlist` CRUD page and a `/listen/feed` live-event page that subscribes to `/ws/listen`.

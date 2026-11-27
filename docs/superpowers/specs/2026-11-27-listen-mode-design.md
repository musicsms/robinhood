# Design Specification: Listen Mode (Sprint 1)

**Date:** 2026-11-27
**Status:** Draft
**Owner:** bobbab

---

## 1. Executive Summary & Goals

### 1.1 Motivation

Robin today is request-driven: a user types a query, the pipeline runs one
investigation, and the run ends. For OSINT in the Vietnam finance sector,
threat-relevant posts appear at unpredictable times on dark-web-adjacent
Telegram channels (infostealer log markets, ransomware brokers, carding
groups). The user has to re-run the same investigation manually to notice
anything new, and by then the post has often rolled off the channel.

Sprint 1 adds a **Listen Mode**: a background loop that polls a configured
watchlist of Telegram channels seeded from `fastfire/deepdarkCTI`, matches
each new post against a Vietnamese-finance keyword filter, and on a hit saves
an investigation JSON, pushes a WebSocket event to the Web Console, and
sends a Telegram-bot notification.

Realtime Telethon capture, Tor .onion polling, forum seeding, LLM enrichment,
and the Web Console UI for watchlist management are **out of scope for
Sprint 1** and covered in follow-up plans.

### 1.2 Core Objectives (Sprint 1)

1. **Persistent watchlist** stored in SQLite at `data/listen/watchlist.db`,
   seeded from a check-in YAML at `config/watchlist.yaml` on first boot.
2. **Seed loader** that reads `data/deepdarkCTI/telegram_infostealer.md`,
   extracts every `https://t.me/<handle>` URL, and inserts a
   `source_type=telegram_web` entry per unique channel (99 channels today).
3. **Vietnam-finance keyword filter** — a pure function that takes a post's
   text and returns `(matched: bool, reasons: list[str])`, where `reasons`
   names which keyword class triggered (`bank`, `payment`, `geo`, `phone`,
   `vn_text`). Case-insensitive, diacritic-normalised, word-boundary safe.
4. **Telegram polling worker** — an async task that round-robins the
   enabled watchlist entries, calls the existing
   `modules.scrape.telegram.scrape_telegram_channel`, dedups each post by
   `(channel, message_signature)` against SQLite, and emits one
   `MatchEvent` per new post that passes the filter.
5. **Match dispatcher** that fans each `MatchEvent` out to three sinks:
   - **Investigation sink** — writes a `listen_match_<timestamp>_<id>.json`
     file into the configured investigations directory, reusing
     `store.save_investigation`'s filename collision scheme.
   - **WebSocket sink** — broadcasts the event JSON on a new
     `/ws/listen` endpoint added to the viewer's aiohttp app.
   - **Telegram bot sink** — POSTs to
     `https://api.telegram.org/bot<token>/sendMessage` with
     `chat_id=TELEGRAM_BOT_CHAT_ID` and a one-screen message body.
6. **Boot integration** — `main.py` starts the listen worker alongside the
   viewer HTTP server when `ROBIN_LISTEN_ENABLED=1` in `.env`; default off.

### 1.3 Non-Goals (Sprint 1)

- Telethon MTProto realtime capture (needs `TELEGRAM_API_ID`,
  `TELEGRAM_API_HASH`, `.session` persistence, login flow).
- Tor-routed polling of `.onion` ransomware leak blogs or forum sites.
- LLM enrichment of matched posts via `grok-4.6`.
- Web Console UI pages for watchlist CRUD or live-feed viewer.
- Vietnam-specific sources outside deepdarkCTI (RSS, Twitter/Nitter).
- Multi-tenant notification routing (one bot, one chat_id for Sprint 1).

---

## 2. Data Model

### 2.1 Watchlist entry

YAML schema (check-in file `config/watchlist.yaml`):

```yaml
- id: infostealer-redline-pub        # stable, string, unique
  source_type: telegram_web          # enum: telegram_web (Sprint 2 adds telethon, onion, rss)
  target: "@redline_logs_pub"        # channel handle or URL
  interval_sec: 60                   # polling period
  enabled: true
  seed_from: deepdarkCTI/telegram_infostealer.md
  tags: [infostealer, logs]          # free-form strings, SQLite stores JSON-encoded
```

SQLite schema (persistent file at `data/listen/watchlist.db`):

```sql
CREATE TABLE IF NOT EXISTS watchlist (
    id            TEXT PRIMARY KEY,
    source_type   TEXT NOT NULL,                 -- 'telegram_web' only in Sprint 1
    target        TEXT NOT NULL,
    interval_sec  INTEGER NOT NULL DEFAULT 60,
    enabled       INTEGER NOT NULL DEFAULT 1,
    seed_from     TEXT,
    tags_json     TEXT NOT NULL DEFAULT '[]',
    last_polled   TEXT,                          -- ISO-8601 UTC
    last_error    TEXT
);

CREATE TABLE IF NOT EXISTS seen_messages (
    watchlist_id      TEXT NOT NULL,
    message_signature TEXT NOT NULL,             -- sha256 of (date + first 256 chars of text)
    seen_at           TEXT NOT NULL,
    PRIMARY KEY (watchlist_id, message_signature)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_enabled ON watchlist(enabled);
CREATE INDEX IF NOT EXISTS idx_seen_time ON seen_messages(seen_at);
```

### 2.2 Match event

Python dataclass, serialised to JSON for all three sinks:

```python
@dataclass
class MatchEvent:
    event_id: str                  # uuid4().hex
    timestamp: str                 # ISO-8601 UTC
    watchlist_id: str
    source_type: str
    target: str
    post_date: str                 # from scraper, may be empty
    post_views: str                # from scraper, may be empty
    post_text: str                 # scrubbed, fenced-untrusted on save but raw here
    match_reasons: list[str]       # ['bank:vietcombank', 'geo:vietnam']
```

---

## 3. Vietnam-Finance Keyword Rules (Sprint 1)

Keyword classes and regexes, pre-NFKD-normalised (diacritics stripped),
case-folded, matched with `\b` boundaries:

| Class | Pattern |
|---|---|
| `bank` | `\b(vietcombank\|vcb\|bidv\|techcombank\|tcb\|vpbank\|agribank\|sacombank\|acb\|mbbank\|vietinbank\|ctg\|hdbank\|eximbank\|shb\|tpbank\|ocb\|seabank\|lpb\|msb\|nab\|pgb\|vib)\b` |
| `payment` | `\b(napas\|momo\|zalopay\|vnpay\|viettelmoney\|shopeepay\|moca\|payoo)\b` |
| `geo` | `\b(viet ?nam\|hanoi\|ho ?chi ?minh\|saigon\|da ?nang\|\.vn)\b` |
| `phone` | `\+?84[\s.-]?\d{9,10}\b` **or** `\b0[35789]\d{8}\b` |
| `vn_text` | `\b(ngan hang\|tai khoan\|the tin dung\|ro ri)\b` (post is NFKD-stripped first; `ngân hàng` → `ngan hang`) |

A post matches if **any** class produces at least one hit. The reason list
names every hit as `class:match`; the dispatcher routes on the match, not on
a numeric score.

---

## 4. Boot & Runtime Contract

### 4.1 Environment variables (added to `.env.example`)

| Name | Required for | Default |
|---|---|---|
| `ROBIN_LISTEN_ENABLED` | listen loop boots | `0` |
| `TELEGRAM_BOT_TOKEN` | bot sink | (unset; bot sink no-ops with one-line warning) |
| `TELEGRAM_BOT_CHAT_ID` | bot sink | (unset; same) |
| `LISTEN_WATCHLIST_DB_PATH` | override DB location | `data/listen/watchlist.db` |
| `LISTEN_WATCHLIST_YAML_PATH` | override seed YAML | `config/watchlist.yaml` |

### 4.2 Startup sequence inside `main.py`

1. Existing: Tor check, CTI index init, model refresh.
2. New: if `ROBIN_LISTEN_ENABLED=1`, call
   `modules.listen.boot.start_listen(app)` **after**
   `run_viewer_server` builds the aiohttp app but **before** `web.run_app`,
   so the worker shares the event loop and app state with the viewer.
3. `start_listen` opens the SQLite DB, runs the YAML seed loader (idempotent
   `INSERT OR IGNORE`), and schedules one `telegram_poll_loop()` coroutine
   per enabled watchlist entry.

### 4.3 Shutdown

aiohttp `on_cleanup` cancels the worker tasks and closes the DB. SIGINT
during a poll cycle must not corrupt the DB — WAL mode + a short write
transaction per cycle.

---

## 5. Security Invariants Preserved

- All scraped text from Telegram passes through
  `core.security.scrub_untrusted_text` inside
  `modules.scrape.telegram.scrape_telegram_channel` (per-post field
  `posts[*]['text']`). The listen pipeline uses that scrubbed field for
  both filtering and persistence, never the raw HTML. If a Sprint-2
  enricher later feeds a match to an LLM, it must wrap the text in
  `fence_untrusted` at that boundary — the Sprint-1 JSON sink does not,
  because the JSON is OSINT evidence, not a prompt.
- Bot token and chat_id are read from `.env` only, never logged.
- Watchlist DB is created with mode `0600` and lives under `data/`, which
  is already `.gitignore`d (verify in Task 1).

---

## 6. Open Questions Deferred to Sprint 2

- Enricher threshold tuning (LLM relevance score ≥ 5 before notify?).
- Whether Telethon's realtime capture should replace web polling for the
  same channel or run in parallel.
- Rate-limit strategy under Tor (onion polling will need proxied `aiohttp`
  with per-host concurrency ≤ 2).
- UI: should the live-feed page replace the viewer's run-list on the home
  route, or sit on `/listen`?

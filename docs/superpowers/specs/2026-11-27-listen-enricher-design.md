# Design Specification: Listen Enricher (Sprint 2 — feature 1 of 5)

**Date:** 2026-11-27
**Status:** Draft
**Owner:** bobbab
**Follows:** `docs/superpowers/specs/2026-11-27-listen-mode-design.md`

---

## 1. Executive Summary & Goals

### 1.1 Motivation

Sprint 1 ran a VN-finance keyword filter over every new Telegram post and
dispatched every match to three sinks. The 15-minute real soak confirmed the
pipeline is healthy but surfaced the limit the plan already anticipated: a
regex filter cannot tell `Vietcombank leak` from `Vietcombank promotional
post` or `Vietcombank user asks a support question`. Every hit will reach
the user's Telegram bot unranked.

This feature adds an **LLM enricher step** between the dedup check and the
dispatcher. For each new post it calls the configured custom model
(`grok-4.6`), asks it to score the post's relevance to Vietnam finance OSINT
on a 0-10 scale, extract banks mentioned, leak type, and a Vietnamese-language
one-paragraph summary. Posts below the configurable threshold are routed to a
new `investigations/rejected/` folder (audit trail), not dispatched; posts at
or above the threshold flow to the existing three sinks with an enriched bot
message that leads with the score, banks, leak type, and summary.

### 1.2 Core Objectives

1. **Enrich every new post**, not only keyword-filter hits — the keyword
   filter from Sprint 1 remains as a precondition for the existing
   `match_reasons` field, but no longer gates dispatch. The enricher itself
   is the gate.
2. **Scoped by sampling** so the operator can throttle token cost:
   `LISTEN_ENRICHER_SAMPLE_RATE` is a float in `[0.0, 1.0]` applied per
   non-keyword-hit post. Keyword-hit posts always enrich (`sample_rate=1`
   implicit). Default `1.0` honours the operator's "enrich everything" ask;
   anyone can turn it down later without code changes.
3. **Model-cascade fallback**: if the primary model times out or raises,
   fall through a configurable chain. Sprint-2 default:
   `grok-4.6 → grok-4.5 → deepseek-r1:32b` (Ollama). An all-models failure
   logs the chain's last error, dispatches the event raw (no enrichment),
   and does **not** apply the threshold — a failed LLM is not evidence
   against the hit.
4. **Threshold gate**: posts with `relevance < LISTEN_ENRICHER_THRESHOLD`
   (default `5`) are written to `investigations/rejected/listen_reject_<stamp>.json`
   and **not** sent to the WebSocket or Telegram-bot sinks. Posts at or
   above the threshold take the existing dispatcher path with the
   enrichment attached.
5. **Audit log**: every LLM call (success or fail) appends one JSON line to
   `data/listen/enricher_audit.jsonl` with model, elapsed_ms, prompt
   length, response length, success flag, error class. The audit log
   rotates at 100 MB. No prompt or response text in the audit, only sizes.
6. **Bot message upgrade**: when a dispatched event carries an enrichment,
   the bot message leads with
   `Relevance N/10 - <banks> - <leak_type>\n<summary_vi>` then the
   existing post excerpt (trimmed to 800 chars to keep room under the
   4096-char Telegram limit).
7. **Feature-flagged**: `LISTEN_ENRICHER_ENABLED=0` by default. With it
   unset, Sprint 1 behaviour is unchanged.

### 1.3 Non-Goals (Sprint 2 feature 1)

- Any UI for watchlist CRUD or live feed (that is feature 2).
- Telethon realtime worker (feature 3).
- Tor .onion poller (feature 4).
- Forum seed loader (feature 5).
- Per-channel enrichment tuning (same threshold for every entry in Sprint 2).
- Streaming LLM responses (`generate_summary` uses batched calls).
- Caching identical enrichments (dedup table already prevents re-scoring
  the same signature).

---

## 2. Data Model

### 2.1 EnrichmentResult (what the LLM returns, parsed into a dataclass)

```python
@dataclass
class EnrichmentResult:
    relevance: int                 # 0-10
    banks_mentioned: list[str]     # normalised bank names, e.g. ["Vietcombank"]
    leak_type: str                 # one of: creds | combo | card | personal | other | none
    victim_count_est: Optional[int]
    language: str                  # ISO-639-1 of the post ('vi', 'ru', 'en', ...)
    summary_vi: str                # 2-3 sentence Vietnamese summary
    confidence: float              # 0.0-1.0, model's self-reported confidence
    model: str                     # which cascade model produced this result
    elapsed_ms: int                # how long the call took
```

### 2.2 MatchEvent extension

```python
# modules/listen/events.py
@dataclass
class MatchEvent:
    # ...existing fields unchanged...
    enrichment: Optional[dict] = None       # EnrichmentResult.to_dict() or None
    below_threshold: bool = False           # set by poll_once after enrichment
```

Backward compat: the Sprint 1 JSON schema continues to work with
`enrichment: null` and `below_threshold: false` in every record.

### 2.3 Enricher audit line (one JSON per file line)

```json
{"ts": "2026-11-27T20:45:00Z", "model": "grok-4.6", "elapsed_ms": 1240,
 "prompt_len": 512, "response_len": 203, "success": true, "error": null,
 "watchlist_id": "ddcti-ab1234", "signature": "<sha256[:12]>"}
```

---

## 3. LLM Prompt Contract

Checked-in file `config/enricher_prompt.md` (user-editable) holds the
system prompt as a two-section Markdown file:

```markdown
## System

You are a threat-intelligence analyst specialising in Vietnam finance
and banking OSINT. You read posts scraped from public dark-web-adjacent
Telegram channels and score each one for relevance to a Vietnamese
cyber-security team monitoring for data leaks, credential stuffing, card
fraud, and account takeover against Vietnamese banks and payment
services. Reply with JSON ONLY, following the schema below.

## Schema

{"relevance": 0-10, "banks_mentioned": [...], "leak_type": "creds|combo|card|personal|other|none",
 "victim_count_est": null, "language": "...", "summary_vi": "...", "confidence": 0.0-1.0}

## Scoring guidance

- 9-10: explicit leak of a named Vietnamese bank's customer data or creds.
- 7-8: combo/card list filtered or tagged VN; references NAPAS/MoMo/ZaloPay.
- 5-6: mentions Vietnam or a VN bank incidentally but no actionable data.
- 1-4: generic infostealer log, no VN signal.
- 0: unrelated (ad, spam, sticker, unrelated country).
```

User message: a short envelope
`post_date=<date> channel=<handle> reasons=<csv> text=<text>`.

---

## 4. Boot & Runtime Contract

### 4.1 New env vars (added to `.env.example`)

| Name | Required for | Default |
|---|---|---|
| `LISTEN_ENRICHER_ENABLED` | enrichment runs | `0` |
| `LISTEN_ENRICHER_MODEL` | primary model | `grok-4.6` |
| `LISTEN_ENRICHER_FALLBACK_MODELS` | cascade (CSV) | `grok-4.5,deepseek-r1:32b` |
| `LISTEN_ENRICHER_THRESHOLD` | dispatch cutoff | `5` |
| `LISTEN_ENRICHER_TIMEOUT_SEC` | per-model call timeout | `30` |
| `LISTEN_ENRICHER_SAMPLE_RATE` | non-keyword-hit sampling | `1.0` |
| `LISTEN_ENRICHER_MAX_CONCURRENCY` | asyncio.Semaphore cap | `2` |
| `LISTEN_ENRICHER_AUDIT_PATH` | audit JSONL | `data/listen/enricher_audit.jsonl` |

### 4.2 Integration point

`poll_once` calls `enricher.score(post, entry, match_reasons)` after
`mark_seen` returns True. Two branches:

- `match_reasons` non-empty → always enrich.
- `match_reasons` empty → enrich iff `random() < sample_rate`.

On enrichment success it populates `event.enrichment` and
`event.below_threshold = result.relevance < threshold`. On all-models
failure `event.enrichment = None`, `event.below_threshold = False`
(fail-open: do not reject a real hit because the model is down).

### 4.3 Sink routing

- `investigation_json_sink`: if `event.below_threshold`, write under
  `<investigations_dir>/rejected/listen_reject_<stamp>.json`; otherwise
  under `<investigations_dir>/listen_match_<stamp>.json`.
- `ws_sink` and `telegram_bot_sink`: skip when `event.below_threshold`.
- `telegram_bot_sink` + `format_message`: if `event.enrichment` is set,
  prepend the relevance header and summary_vi.

---

## 5. Security & Cost Invariants

- Post text passed to the LLM is already scrubbed by the scraper; it is
  wrapped in `fence_untrusted(...)` at the enricher boundary so the
  model treats it as evidence, not instructions.
- Audit log stores **sizes only**, never prompt or response text —
  avoids PII and credential leakage into logs.
- The audit log rotates at `~100 MB` by renaming to
  `enricher_audit.YYYYMMDD.jsonl.N` and opening a fresh file.
- Sampling and feature-flag default off bound token cost: by default
  this change spends zero tokens.

---

## 6. Open Questions Deferred

- Whether to score posts in batches of 5 for efficiency (current spec:
  one call per post; simpler).
- Whether to add a second-pass "escalation" LLM for posts with
  `relevance=8-9` (e.g., ask a stronger model for a deeper summary).
- Whether to translate `summary_vi` into English on request for the UI
  — the UI is feature 2's problem, not this one.

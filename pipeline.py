"""The investigation: refine, search, filter, scrape, summarize, pivots, save."""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

import store
from config import (
    DEFAULT_CONTENT_CHARS,
    DEFAULT_MAX_RESULTS,
    DEFAULT_MAX_SCRAPE,
    DEFAULT_PRESET,
    DEFAULT_THREADS,
    RobinConfig,
    redact_secrets,
)
from llm import (
    filter_results,
    generate_summary,
    get_llm,
    refine_query,
    suggest_pivots,
)
from llm_utils import BufferedStreamingHandler, response_text
from prompts import PRESETS
from scrape import is_onion, scrape_multiple
from search import engines_unreachable, get_search_results_detailed

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_NO_RESULTS = "no_results"
# Told apart from no_results: nothing was found out, rather than nothing exists.
STATUS_ENGINES_UNREACHABLE = "engines_unreachable"
STATUS_NOTHING_RELEVANT = "nothing_relevant"
# The filter kept results and the scraper could not read a single one of them:
# every onion service down, slow or refusing. Stopping here is the same honesty
# as nothing_relevant.
STATUS_NOTHING_READABLE = "nothing_readable"

# Stage name -> the phrase a caller puts after "Failed to ..." in the UI's
# error box and the MCP server's failure reply.
STAGE_ACTIONS = {
    "load_llm": "load the selected LLM",
    "refine": "refine the query",
    "search": "search the dark web",
    "filter": "filter the search results",
    "scrape": "scrape the selected pages",
    "eval_gap": "evaluate intelligence gaps",
    "summarize": "generate the investigation summary",
    "pivots": "suggest investigation pivots",
    "save": "save the investigation",
}


# Model names that record who wrote a report rather than a model Robin can
# build: robin_save_investigation stores "host", and a run through MCP
# sampling stores "host sampling".
HOST_MODEL_MARKERS = frozenset({"host", "host sampling"})


def followup_model(stored: Optional[str], selected: str,
                   resolvable: Callable[[str], bool]) -> str:
    """The model a follow-up question about a saved report runs on.

    The report's own model when `resolvable` accepts it, so a follow-up sounds
    like the report; otherwise `selected`. A resolver that raises means "no".
    """
    name = (stored or "").strip()
    if not name or name.lower() in HOST_MODEL_MARKERS:
        return selected
    try:
        return name if resolvable(name) else selected
    except Exception:
        return selected


class PipelineError(RuntimeError):
    """A stage failed. Carries which one, and the error underneath."""

    def __init__(self, stage: str, original: Exception):
        self.stage = stage
        self.action = STAGE_ACTIONS.get(stage, stage)
        self.original = original
        super().__init__("Failed to {}: {}".format(self.action, original))


@dataclass
class Investigation:
    """Everything one investigation produced, and what it ran with."""

    query: str
    status: str = STATUS_OK
    refined: str = ""
    results: List[dict] = field(default_factory=list)
    # What the engines did, when the search reported it: an empty result
    # list means nothing found only if an engine actually answered.
    search_stats: dict = field(default_factory=dict)
    filtered: List[dict] = field(default_factory=list)
    scraped: dict = field(default_factory=dict)
    summary: str = ""
    pivots: List[str] = field(default_factory=list)
    model: str = ""
    preset: str = DEFAULT_PRESET
    preset_label: str = ""
    custom_instructions: str = ""
    # The depth this run actually used, not the defaults it might have used.
    max_results: int = DEFAULT_MAX_RESULTS
    max_scrape: int = DEFAULT_MAX_SCRAPE
    content_chars: int = DEFAULT_CONTENT_CHARS
    threads: int = DEFAULT_THREADS
    started_at: str = ""
    finished_at: str = ""
    # The file `store.save_investigation` wrote, or None when it could not.
    saved_as: Optional[str] = None
    search_scope: str = "darkweb"
    max_iterations: int = 1
    iterations_run: int = 1
    cti_records: List[dict] = field(default_factory=list)

    def to_record(self) -> dict:
        """The JSON body written to disk.

        The first seven keys keep their original names so older Robin versions
        can still open these files. Raw scraped text is not persisted.
        """
        return {
            "timestamp": self.started_at,
            "query": self.query,
            "refined_query": self.refined,
            "model": self.model,
            "preset": self.preset_label or self.preset,
            "sources": self.filtered,
            "summary": self.summary,
            "status": self.status,
            "preset_key": self.preset,
            "custom_instructions": self.custom_instructions,
            "results_count": len(self.results),
            "scraped_count": len(self.scraped or {}),
            "max_results": self.max_results,
            "max_scrape": self.max_scrape,
            "content_chars": self.content_chars,
            "threads": self.threads,
            "pivots": self.pivots,
            "finished_at": self.finished_at,
            "search_scope": self.search_scope,
            "max_iterations": self.max_iterations,
            "iterations_run": self.iterations_run,
            "cti_matches_count": len(self.cti_records or []),
        }


def _preset_label(preset: str) -> str:
    entry = PRESETS.get(preset)
    return entry[0] if entry else preset


def _now() -> str:
    return datetime.now().isoformat()


def run_investigation(cfg: Optional[RobinConfig],
                      query: str,
                      model: str,
                      preset: str = DEFAULT_PRESET,
                      custom_instructions: str = "",
                      max_results: int = DEFAULT_MAX_RESULTS,
                      max_scrape: int = DEFAULT_MAX_SCRAPE,
                      content_chars: int = DEFAULT_CONTENT_CHARS,
                      threads: int = DEFAULT_THREADS,
                      on_stage: Optional[Callable[[str, Investigation], None]] = None,
                      on_token: Optional[Callable[[str], None]] = None,
                      search_fn: Optional[Callable] = None,
                      scrape_fn: Optional[Callable] = None,
                      investigations_dir=None,
                      preset_label: Optional[str] = None,
                      llm_factory: Optional[Callable[[], object]] = None,
                      save: bool = True,
                      search_scope: str = "darkweb",
                      max_iterations: int = 1) -> Investigation:
    """Run one investigation start to finish and return it."""
    def build_llm():
        return llm_factory() if llm_factory is not None else get_llm(model, cfg)
    def stage(name):
        if on_stage:
            on_stage(name, inv)

    inv = Investigation(
        query=query,
        model=model,
        preset=preset,
        preset_label=preset_label or _preset_label(preset),
        custom_instructions=custom_instructions or "",
        max_results=max_results,
        max_scrape=max_scrape,
        content_chars=content_chars,
        threads=threads,
        started_at=_now(),
        search_scope=search_scope,
        max_iterations=max_iterations,
    )

    # Stage 1 - the model
    stage("load_llm")
    try:
        llm = build_llm()
    except Exception as exc:
        raise PipelineError("load_llm", exc) from exc

    # Stage 2 - refine the query
    stage("refine")
    try:
        inv.refined = refine_query(llm, query)
    except Exception as exc:
        raise PipelineError("refine", exc) from exc

    # Stage 3 - search. The `+` encoding lives in the search layer, so this
    # hands over the refined query verbatim.
    stage("search")
    try:
        if search_fn is not None:
            outcome = search_fn(inv.refined, threads)
            inv.results = list(outcome["results"])
            inv.search_stats = dict(outcome.get("stats") or {})
        else:
            merged_results = []
            merged_stats = {}

            # CTI directory lookup
            if search_scope in ("hybrid", "clearweb"):
                try:
                    from modules.cti.lookup import search_cti_records
                    inv.cti_records = search_cti_records(query, limit=5)
                    if not inv.cti_records and inv.refined and inv.refined != query:
                        inv.cti_records = search_cti_records(inv.refined, limit=5)
                    for r in inv.cti_records:
                        if r.get("url") and r.get("is_online"):
                            if search_scope == "clearweb" and is_onion(r["url"]):
                                continue
                            merged_results.append({
                                "title": f"[{r['category'].upper()}] {r['name']} ({r['status']})",
                                "link": r["url"],
                                "snippet": f"CTI Directory: {r.get('description') or ''} {r.get('extra_channels') or ''}".strip(),
                                "source": "cti_directory",
                            })
                except Exception as exc:
                    logger.warning("CTI lookup encountered error: %s", exc)

            # Dark Web search
            if search_scope in ("hybrid", "darkweb"):
                try:
                    outcome = get_search_results_detailed(inv.refined, max_workers=threads)
                    merged_results.extend(outcome.get("results") or [])
                    merged_stats.update(outcome.get("stats") or {})
                except Exception as exc:
                    if search_scope == "darkweb":
                        raise
                    logger.warning("Darkweb search error in %s mode: %s", search_scope, exc)

            # Clear Web search
            if search_scope in ("hybrid", "clearweb"):
                try:
                    from modules.search.clearweb import search_clearweb
                    cw_results = search_clearweb(inv.refined or query, max_results=max_results)
                    merged_results.extend(cw_results)
                except Exception as exc:
                    if search_scope == "clearweb":
                        raise
                    logger.warning("Clearweb search error in %s mode: %s", search_scope, exc)

            # Deduplicate by URL
            seen_urls = set()
            deduped = []
            for item in merged_results:
                raw_u = (item.get("link") or "").rstrip("/").lower()
                if raw_u and raw_u not in seen_urls:
                    seen_urls.add(raw_u)
                    deduped.append(item)

            inv.results = deduped
            inv.search_stats = merged_stats
    except Exception as exc:
        raise PipelineError("search", exc) from exc

    if not inv.results:
        return _finish(inv, STATUS_ENGINES_UNREACHABLE
                       if engines_unreachable(inv.search_stats) else STATUS_NO_RESULTS)

    # The user's cap applies before the model is asked to rank anything.
    if len(inv.results) > max_results:
        inv.results = inv.results[:max_results]

    # Stage 4 - filter
    stage("filter")
    try:
        inv.filtered = list(filter_results(llm, inv.refined, inv.results,
                                           limit=max_scrape) or [])
    except Exception as exc:
        raise PipelineError("filter", exc) from exc

    if not inv.filtered:
        # Nothing on topic. Robin stops here on purpose rather than summarizing
        # unrelated pages.
        return _finish(inv, STATUS_NOTHING_RELEVANT)

    if len(inv.filtered) > max_scrape:
        inv.filtered = inv.filtered[:max_scrape]

    # Stage 5 - scrape
    stage("scrape")
    try:
        if scrape_fn is not None:
            inv.scraped = scrape_fn(inv.filtered, threads, content_chars) or {}
        else:
            onion_items = [u for u in inv.filtered if is_onion(u.get("link") if isinstance(u, dict) else str(u))]
            clear_items = [u for u in inv.filtered if not is_onion(u.get("link") if isinstance(u, dict) else str(u))]

            scraped_pages = {}
            if onion_items:
                scraped_pages.update(
                    scrape_multiple(onion_items, max_workers=threads, max_return_chars=content_chars) or {}
                )
            if clear_items:
                from modules.scrape.clearweb import scrape_clearweb_multiple
                c_recs = scrape_clearweb_multiple(clear_items, max_workers=threads, max_return_chars=content_chars)
                for cr in c_recs:
                    if cr.get("status") == "ok" and cr.get("text"):
                        scraped_pages[cr["url"]] = cr["text"]
            inv.scraped = scraped_pages
    except Exception as exc:
        raise PipelineError("scrape", exc) from exc

    if not inv.scraped:
        # Not one kept page could be read. No summary, no pivots, no save.
        return _finish(inv, STATUS_NOTHING_READABLE)

    # Iterative Deepening Loop (if enabled)
    if max_iterations > 1 and inv.scraped and len(inv.scraped) > 0:
        stage("eval_gap")
        try:
            from llm import evaluate_research_gaps
            gap_pivots = evaluate_research_gaps(build_llm(), query, inv.scraped)
            if gap_pivots:
                logger.info("Deep loop identified intelligence gaps: %s", gap_pivots)
                for pivot_q in gap_pivots:
                    pivot_items = []
                    if search_scope in ("hybrid", "clearweb"):
                        try:
                            from modules.search.clearweb import search_clearweb
                            pivot_items.extend(search_clearweb(pivot_q, max_results=3))
                        except Exception as e:
                            logger.warning("Pivot clearweb search error: %s", e)
                    if search_scope in ("hybrid", "darkweb"):
                        try:
                            p_out = get_search_results_detailed(pivot_q, max_workers=threads)
                            pivot_items.extend(p_out.get("results") or [])
                        except Exception as e:
                            logger.warning("Pivot darkweb search error: %s", e)

                    new_items = [r for r in pivot_items if (r.get("link") or "") not in inv.scraped][:2]
                    if new_items:
                        onion_p = [r for r in new_items if is_onion(r.get("link") or "")]
                        clear_p = [r for r in new_items if not is_onion(r.get("link") or "")]
                        if onion_p:
                            inv.scraped.update(
                                scrape_multiple(onion_p, max_workers=threads, max_return_chars=content_chars) or {}
                            )
                        if clear_p:
                            from modules.scrape.clearweb import scrape_clearweb_multiple
                            c_p_res = scrape_clearweb_multiple(clear_p, max_workers=threads, max_return_chars=content_chars)
                            for cr in c_p_res:
                                if cr.get("status") == "ok" and cr.get("text"):
                                    inv.scraped[cr["url"]] = cr["text"]
                inv.iterations_run = 2
        except Exception as exc:
            logger.warning("Gap evaluation failed: %s", exc)

    # Stage 6 - summarize, streaming through the caller's callback
    stage("summarize")
    streamed = {"text": ""}

    def _emit(chunk: str) -> None:
        streamed["text"] += chunk
        if on_token:
            on_token(chunk)

    llm.callbacks = [BufferedStreamingHandler(ui_callback=_emit)]

    # Enrich custom instructions with CTI Directory matches if available
    summary_instructions = custom_instructions or ""
    if inv.cti_records:
        try:
            from modules.cti.lookup import format_cti_summary
            cti_block = format_cti_summary(inv.cti_records, max_chars=2000)
            summary_instructions = (
                f"{summary_instructions}\n\nCTI DIRECTORY CONTEXT:\n{cti_block}".strip()
            )
        except Exception as exc:
            logger.warning("Failed to format CTI summary: %s", exc)

    try:
        returned = generate_summary(llm, query, inv.scraped, preset=preset,
                                    custom_instructions=summary_instructions)
    except Exception as exc:
        raise PipelineError("summarize", exc) from exc

    # Reasoning models (OpenAI o1, DeepSeek R1 and similar) may stream no answer
    # tokens, leaving the streamed buffer empty; generate_summary's return value
    # still holds the answer, so it is the fallback.
    streamed_text = response_text(streamed["text"])
    inv.summary = streamed_text if streamed_text.strip() else response_text(returned)
    if not inv.summary.strip():
        raise PipelineError("summarize", RuntimeError("the model returned an empty report"))

    # Pivots are a convenience and never block a finished investigation. A
    # fresh client, so the structured JSON is not streamed to the caller.
    stage("pivots")
    try:
        inv.pivots = list(suggest_pivots(build_llm(), query, inv.scraped,
                                         preset=preset) or [])
    except Exception as exc:
        logger.warning("Pivot suggestions failed (%s).", redact_secrets(exc, cfg)[:200])
        inv.pivots = []

    # The MCP server passes save=False and saves the finished run itself,
    # through the same scrubbing, bounds and per-session allowance as its save
    # tool. The UI keeps the default and saves here.
    if not save:
        return _finish(inv, STATUS_OK)

    stage("save")
    _finish(inv, STATUS_OK)
    return store.save_investigation(inv, investigations_dir=investigations_dir, cfg=cfg)


def _finish(inv: Investigation, status: str) -> Investigation:
    inv.status = status
    inv.finished_at = _now()
    return inv

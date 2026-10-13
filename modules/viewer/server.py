import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from aiohttp import WSCloseCode, WSMsgType, web

import config
from config import RobinConfig, get_config
from modules.viewer.collector import InvestigationCollector
from modules.viewer.models import TraceBundle, load_bundle
import store

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

INVESTIGATIONS_DIR_KEY = web.AppKey("investigations_dir", Path)
WS_CLIENTS_KEY = web.AppKey("ws_clients", set)
ACTIVE_COLLECTORS_KEY = web.AppKey("active_collectors", dict)
ACTIVE_TASKS_KEY = web.AppKey("active_tasks", dict)
ROBIN_CONFIG_HOLDER_KEY = web.AppKey("robin_config_holder", dict)


def _robin_cfg(app: web.Application) -> RobinConfig:
    if ROBIN_CONFIG_HOLDER_KEY in app:
        return app[ROBIN_CONFIG_HOLDER_KEY].get("cfg", get_config())
    return app.get("robin_config", get_config())


def _inv_dir(app: web.Application) -> Path:
    res = app.get(INVESTIGATIONS_DIR_KEY)
    if res is not None:
        return res
    return app.get("investigations_dir", store.resolve_dir())


def _ws_clients(app: web.Application) -> Set[web.WebSocketResponse]:
    if WS_CLIENTS_KEY in app:
        return app[WS_CLIENTS_KEY]
    return app.setdefault(WS_CLIENTS_KEY, set())


def _active_collectors(app: web.Application) -> Dict[str, InvestigationCollector]:
    if ACTIVE_COLLECTORS_KEY in app:
        return app[ACTIVE_COLLECTORS_KEY]
    return app.setdefault(ACTIVE_COLLECTORS_KEY, {})


def _active_tasks(app: web.Application) -> Dict[str, asyncio.Task]:
    if ACTIVE_TASKS_KEY in app:
        return app[ACTIVE_TASKS_KEY]
    return app.setdefault(ACTIVE_TASKS_KEY, {})


async def handle_index(request: web.Request) -> web.StreamResponse:
    """Serve the root web console HTML page."""
    index_file = STATIC_DIR / "index.html"
    if index_file.is_file():
        return web.FileResponse(index_file)
    return web.Response(
        text="""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Robin-CTI Workflow Viewer</title>
</head>
<body style="background-color: #0d1117; color: #c9d1d9; font-family: monospace; padding: 2rem;">
    <h1>Robin-CTI Workflow Viewer</h1>
    <p>Static UI bundle loading...</p>
</body>
</html>""",
        content_type="text/html",
    )


async def handle_status(request: web.Request) -> web.Response:
    """Return system health: Tor status, CTI index records count, available models."""
    cfg = _robin_cfg(request.app)

    # Tor status
    try:
        from health import check_tor_proxy
        tor_info = await asyncio.to_thread(check_tor_proxy)
    except Exception as exc:
        tor_info = {"status": "down", "error": str(exc)}

    # CTI index count
    try:
        from modules.cti.indexer import get_db_connection
        conn = get_db_connection()
        cur = conn.execute("SELECT COUNT(*) FROM cti_records")
        cti_indexed_count = cur.fetchone()[0]
    except Exception:
        cti_indexed_count = 0

    # Models list (quick check from config & registry)
    app = request.app
    models: List[str] = []
    if cfg.custom_api_model:
        models.append(cfg.custom_api_model)
    try:
        from model_registry import get_entries
        entries = get_entries(cfg)
        models.extend([e["key"] for e in entries])
    except Exception:
        pass

    models = list(dict.fromkeys(models))

    return web.json_response({
        "status": "ok",
        "tor": tor_info,
        "cti_indexed_count": cti_indexed_count,
        "models": models,
        "active_runs": len(_active_collectors(app)),
    })


async def handle_get_runs(request: web.Request) -> web.Response:
    """List historical and active investigations, newest first."""
    app = request.app
    inv_dir: Path = _inv_dir(app)
    runs: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    seen_ids = set()
    seen_query_times = set()

    # Active in-progress runs first
    for run_id, collector in _active_collectors(app).items():
        meta = collector.metadata.model_dump()
        runs.append(meta)
        seen_ids.add(meta["id"])
        q_key = (meta.get("target_query", "").strip().lower(), (meta.get("start_time") or "")[:16])
        if q_key[0] and q_key[1]:
            seen_query_times.add(q_key)

    # Historical files from investigations_dir (bundles prioritized first)
    if inv_dir.exists():
        json_files = sorted(
            inv_dir.glob("*.json"),
            key=lambda p: (0 if p.name.startswith("bundle_") else 1, -p.stat().st_mtime),
        )
        for p in json_files:
            try:
                bundle = load_bundle(p)
                meta = bundle.metadata.model_dump()
                q_key = (meta.get("target_query", "").strip().lower(), (meta.get("start_time") or "")[:16])
                if meta["id"] not in seen_ids and (not q_key[0] or not q_key[1] or q_key not in seen_query_times):
                    runs.append(meta)
                    seen_ids.add(meta["id"])
                    if q_key[0] and q_key[1]:
                        seen_query_times.add(q_key)
            except Exception as exc:
                logger.debug("Failed to read investigation file %s: %s", p.name, exc)

    runs.sort(key=lambda r: r.get("start_time", ""), reverse=True)
    return web.json_response(runs)


async def handle_get_run_detail(request: web.Request) -> web.Response:
    """Retrieve full TraceBundle for a specific run ID."""
    app = request.app
    run_id = request.match_info["run_id"]
    inv_dir: Path = _inv_dir(app)

    # 1. Check in-memory active collectors
    collector = _active_collectors(app).get(run_id)
    if collector:
        return web.json_response(collector.get_bundle().model_dump())

    # 2. Check direct candidate file paths (sanitized against directory traversal)
    safe_id = Path(run_id).name
    candidates = [
        inv_dir / f"{safe_id}.json",
        inv_dir / safe_id,
        inv_dir / f"investigation_{safe_id}.json",
        inv_dir / f"bundle_{safe_id}.json",
    ]
    target_path = None
    for cand in candidates:
        if cand.is_file():
            target_path = cand
            break

    # 3. Check file stem match
    if not target_path and inv_dir.exists():
        for p in inv_dir.glob("*.json"):
            if p.stem in (run_id, safe_id):
                target_path = p
                break

    # 4. Check loaded bundle metadata id match
    if not target_path and inv_dir.exists():
        for p in inv_dir.glob("*.json"):
            try:
                bundle = load_bundle(p)
                if bundle.metadata.id in (run_id, safe_id):
                    return web.json_response(bundle.model_dump())
            except Exception:
                continue

    if not target_path:
        return web.json_response({"error": f"Run '{run_id}' not found"}, status=404)

    try:
        bundle = load_bundle(target_path)
        return web.json_response(bundle.model_dump())
    except Exception as exc:
        logger.exception("Error loading run bundle %s: %s", target_path, exc)
        return web.json_response({"error": f"Failed to load run: {exc}"}, status=500)


async def _forward_collector_events(app: web.Application, q: asyncio.Queue):
    """Pipes events from collector subscriber queue to all connected WebSockets."""
    while True:
        try:
            event = await q.get()
            payload = event.model_dump_json()
            dead: Set[web.WebSocketResponse] = set()
            for ws in list(_ws_clients(app)):
                try:
                    await ws.send_str(payload)
                except Exception:
                    dead.add(ws)
            for ws in dead:
                _ws_clients(app).discard(ws)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("Error forwarding event: %s", exc)


async def _run_investigation_worker(app: web.Application, collector: InvestigationCollector, params: dict):
    """Worker task executing pipeline.run_investigation with collector hooks."""
    loop = asyncio.get_running_loop()
    run_id = collector.run_id
    q = collector.subscribe()
    forwarder = asyncio.create_task(_forward_collector_events(app, q))

    def on_stage(stage, inv):
        loop.call_soon_threadsafe(collector.on_stage, stage, inv)

    def on_token(token):
        loop.call_soon_threadsafe(collector.on_token, token)

    cfg = _robin_cfg(app)
    inv_dir: Path = _inv_dir(app)

    try:
        from pipeline import run_investigation
        inv = await loop.run_in_executor(
            None,
            lambda: run_investigation(
                cfg=cfg,
                query=params["query"],
                model=params.get("model", "") or cfg.robin_model or "deepseek-flash",
                preset=params.get("preset", "threat_intel"),
                custom_instructions=params.get("custom_instructions", ""),
                max_results=int(params.get("max_results", 50)),
                max_scrape=int(params.get("max_scrape", 10)),
                content_chars=int(params.get("content_chars", 8000)),
                threads=int(params.get("threads", 4)),
                on_stage=on_stage,
                on_token=on_token,
                investigations_dir=inv_dir,
                search_scope=params.get("search_scope") or params.get("scope") or "darkweb",
                max_iterations=int(params.get("max_iterations") or params.get("iterations") or 1),
                save=True,
            )
        )
        status = "COMPLETED" if inv.status == "ok" else ("NO_RESULTS" if inv.status == "no_results" else "FAILED")
        collector.finish(status=status)
    except asyncio.CancelledError:
        collector.finish(status="ABORTED")
        raise
    except Exception as exc:
        logger.exception("Investigation %s encountered error: %s", run_id, exc)
        collector.record_log("error", str(exc))
        collector.finish(status="FAILED")
    finally:
        # Save bundle
        try:
            bundle = collector.get_bundle()
            bundle_file = inv_dir / f"bundle_{run_id}.json"
            bundle_file.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        except Exception as err:
            logger.warning("Could not persist bundle for %s: %s", run_id, err)

        # Drain event queue before cancelling forwarder
        drain_deadline = loop.time() + 2.0
        while not q.empty() and loop.time() < drain_deadline:
            await asyncio.sleep(0.02)

        collector.unsubscribe(q)
        forwarder.cancel()
        _active_tasks(app).pop(run_id, None)
        _active_collectors(app).pop(run_id, None)


async def handle_investigate(request: web.Request) -> web.Response:
    """Launch an investigation asynchronously in a background task."""
    app = request.app
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    query = str(data.get("query", "")).strip()
    if not query:
        return web.json_response({"error": "Query is required"}, status=400)

    search_scope = data.get("search_scope") or data.get("scope") or "darkweb"
    preset = data.get("preset", "threat_intel")
    max_iterations = int(data.get("max_iterations") or data.get("iterations") or 1)
    model = data.get("model", "")

    collector = InvestigationCollector(
        target_query=query,
        search_scope=search_scope,
        preset=preset,
        model=model,
        max_iterations=max_iterations,
    )
    run_id = collector.run_id
    _active_collectors(app)[run_id] = collector

    task = asyncio.create_task(_run_investigation_worker(app, collector, data))
    _active_tasks(app)[run_id] = task

    return web.json_response({
        "run_id": run_id,
        "status": "IN_PROGRESS",
        "metadata": collector.metadata.model_dump(),
    }, status=202)


async def handle_abort(request: web.Request) -> web.Response:
    """Abort an active investigation."""
    app = request.app
    run_id = request.match_info["run_id"]

    task = _active_tasks(app).get(run_id)
    if not task or task.done():
        return web.json_response({"error": f"Active run '{run_id}' not found"}, status=404)

    task.cancel()
    return web.json_response({"run_id": run_id, "status": "ABORTED"})


async def handle_followup(request: web.Request) -> web.Response:
    """Answer a follow-up question on an investigation report."""
    app = request.app
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    question = str(data.get("question", "")).strip()
    if not question:
        return web.json_response({"error": "Question is required"}, status=400)

    run_id = data.get("run_id")
    summary = data.get("summary", "")
    query = data.get("query", "")
    refined = data.get("refined", "")
    sources = data.get("sources", [])
    scraped = data.get("scraped", {})
    preset = data.get("preset", "threat_intel")
    custom_instructions = data.get("custom_instructions", "")
    model = data.get("model", "")
    history = data.get("history", [])

    if run_id:
        safe_id = Path(run_id).name
        bundle = None
        if run_id in _active_collectors(app):
            bundle = _active_collectors(app)[run_id].get_bundle()
        else:
            inv_dir: Path = _inv_dir(app)
            candidates = [
                inv_dir / f"{safe_id}.json",
                inv_dir / safe_id,
                inv_dir / f"investigation_{safe_id}.json",
                inv_dir / f"bundle_{safe_id}.json",
            ]
            for cand in candidates:
                if cand.is_file():
                    bundle = load_bundle(cand)
                    break
            if not bundle and inv_dir.exists():
                for p in inv_dir.glob("*.json"):
                    if p.stem in (run_id, safe_id):
                        bundle = load_bundle(p)
                        break

        if bundle:
            query = query or bundle.metadata.target_query
            refined = refined or bundle.metadata.refined_query
            summary = summary or bundle.summary
            sources = sources or bundle.findings
            preset = preset or bundle.metadata.preset
            model = model or bundle.metadata.model

    if not summary and not sources:
        return web.json_response({"error": "No investigation context available for followup"}, status=400)

    cfg = _robin_cfg(request.app)
    try:
        from llm import answer_followup, build_followup_context, get_llm
        from llm_utils import resolve_model_config
        from pipeline import followup_model
        from langchain_core.messages import AIMessage, HumanMessage

        selected_model = followup_model(
            model, model or cfg.robin_model or "deepseek-flash",
            lambda name: resolve_model_config(name, cfg) is not None,
        )

        context = build_followup_context(
            query=query,
            refined=refined,
            sources=sources,
            scraped=scraped,
            summary=summary,
        )

        llm = get_llm(selected_model, cfg)

        formatted_history = []
        for turn in history:
            role = turn.get("role")
            content = turn.get("content", "")
            if role == "user":
                formatted_history.append(HumanMessage(content=content))
            elif role in ("assistant", "robin"):
                formatted_history.append(AIMessage(content=content))

        answer = await asyncio.to_thread(
            answer_followup,
            llm=llm,
            question=question,
            context=context,
            history=formatted_history,
            preset=preset,
            custom_instructions=custom_instructions,
        )
        return web.json_response({
            "question": question,
            "answer": answer,
            "model": selected_model,
        })
    except Exception as exc:
        logger.exception("Followup generation failed: %s", exc)
        return web.json_response({"error": f"Failed to answer followup: {exc}"}, status=500)


async def handle_ws_live(request: web.Request) -> web.WebSocketResponse:
    """WebSocket streaming live collector events to connected browser clients."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    # Replay recent events from active collector before subscribing to live broadcasts
    try:
        for collector in _active_collectors(request.app).values():
            for ev in collector.events[-50:]:
                await ws.send_str(ev.model_dump_json())
    except Exception as err:
        logger.debug("Failed to replay recent events to WS: %s", err)

    _ws_clients(request.app).add(ws)

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                text = msg.data.strip()
                if text == "ping":
                    await ws.send_str("pong")
                else:
                    try:
                        obj = json.loads(text)
                        if isinstance(obj, dict) and obj.get("type") == "ping":
                            await ws.send_str(json.dumps({"type": "pong"}))
                    except Exception:
                        pass
            elif msg.type == WSMsgType.ERROR:
                logger.warning("WebSocket connection closed with exception %s", ws.exception())
    finally:
        _ws_clients(request.app).discard(ws)

    return ws


async def handle_get_settings_llm(request: web.Request) -> web.Response:
    """Return masked LLM configuration and available models."""
    cfg = _robin_cfg(request.app)
    from modules.viewer.settings import get_llm_settings
    settings = get_llm_settings(cfg)
    return web.json_response(settings)


async def handle_post_settings_llm(request: web.Request) -> web.Response:
    """Update LLM settings, test connection if requested, and persist."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    if not isinstance(data, dict):
        return web.json_response({"error": "Expected JSON object"}, status=400)

    from modules.viewer.settings import update_llm_settings
    success, message, result = update_llm_settings(data, request.app)
    if not success:
        return web.json_response({"success": False, "error": message}, status=400)

    return web.json_response({
        "success": True,
        "message": message,
        **result,
    })


def create_viewer_app(investigations_dir: Optional[Path] = None) -> web.Application:
    """Factory creating and configuring the aiohttp Web Application."""
    app = web.Application()

    inv_dir = Path(investigations_dir) if investigations_dir is not None else store.resolve_dir()
    inv_dir.mkdir(parents=True, exist_ok=True)
    app[INVESTIGATIONS_DIR_KEY] = inv_dir
    app[WS_CLIENTS_KEY] = set()
    app[ACTIVE_COLLECTORS_KEY] = {}
    app[ACTIVE_TASKS_KEY] = {}
    app[ROBIN_CONFIG_HOLDER_KEY] = {"cfg": get_config()}

    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    # Routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/settings/llm", handle_get_settings_llm)
    app.router.add_post("/api/settings/llm", handle_post_settings_llm)
    app.router.add_get("/api/runs", handle_get_runs)
    app.router.add_get("/api/runs/{run_id}", handle_get_run_detail)
    app.router.add_post("/api/investigate", handle_investigate)
    app.router.add_post("/api/investigate/{run_id}/abort", handle_abort)
    app.router.add_post("/api/followup", handle_followup)
    app.router.add_get("/ws/live", handle_ws_live)

    # Static assets
    app.router.add_static("/static/", path=STATIC_DIR, name="static")

    async def on_shutdown(app: web.Application):
        for ws in list(_ws_clients(app)):
            await ws.close(code=WSCloseCode.GOING_AWAY, message=b"Server shutdown")
        _ws_clients(app).clear()

        for task in list(_active_tasks(app).values()):
            if not task.done():
                task.cancel()

    app.on_shutdown.append(on_shutdown)
    return app


def run_viewer_server(host: str = "0.0.0.0", port: int = 8080, investigations_dir: Optional[Path] = None):
    """Run the aiohttp web server application."""
    app = create_viewer_app(investigations_dir=investigations_dir)
    web.run_app(app, host=host, port=port)

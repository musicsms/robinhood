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

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

@pytest.mark.asyncio
async def test_collector_stage_transitions():
    collector = InvestigationCollector(target_query="ransomware attack", search_scope="darkweb")
    queue = collector.subscribe()

    inv = Investigation(query="ransomware attack", refined="ransomware attack darkweb")
    collector.on_stage("refine", inv)
    ev_start1 = await queue.get()
    assert ev_start1.event_type == EventType.STAGE_START
    assert ev_start1.stage == "refine"

    # Move to search stage - triggers STAGE_DONE for refine, STAGE_START for search
    inv.results = [{"title": "Leaked Docs", "link": "http://onion123.onion"}]
    collector.on_stage("search", inv)

    ev_done1 = await queue.get()
    assert ev_done1.event_type == EventType.STAGE_DONE
    assert ev_done1.stage == "refine"
    assert "duration" in ev_done1.data

    ev_start2 = await queue.get()
    assert ev_start2.event_type == EventType.STAGE_START
    assert ev_start2.stage == "search"

    # Finish run completes current active stage and dispatches REPORT
    inv.summary = "Executive Summary: Ransomware leak discovered."
    inv.pivots = ["onion123.onion wallet tracking"]
    collector.summary = inv.summary
    collector.pivots = inv.pivots
    bundle = collector.finish(status="COMPLETED")

    ev_done2 = await queue.get()
    assert ev_done2.event_type == EventType.STAGE_DONE
    assert ev_done2.stage == "search"

    ev_finish = await queue.get()
    assert ev_finish.event_type == EventType.REPORT
    assert ev_finish.data["summary"] == inv.summary

    assert bundle.metadata.status == "COMPLETED"
    assert bundle.metadata.end_time is not None
    assert len(bundle.turns) >= 2
    collector.unsubscribe(queue)

@pytest.mark.asyncio
async def test_collector_logging_and_multiple_subscribers():
    collector = InvestigationCollector(target_query="ddos threat")
    q1 = collector.subscribe()
    q2 = collector.subscribe()

    collector.record_log(level="warning", message="Search rate limit reached")

    ev1 = await q1.get()
    ev2 = await q2.get()
    assert ev1.event_type == EventType.LOG
    assert ev1.data["level"] == "warning"
    assert ev1.data["message"] == "Search rate limit reached"
    assert ev2.event_type == EventType.LOG

    collector.unsubscribe(q1)
    collector.record_log(level="info", message="Next step")

    ev2_next = await q2.get()
    assert ev2_next.data["message"] == "Next step"
    assert q1.empty()

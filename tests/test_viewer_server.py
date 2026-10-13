# tests/test_viewer_server.py
import json
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from modules.viewer.server import create_viewer_app

class TestViewerServer(AioHTTPTestCase):
    async def get_application(self):
        import shutil
        self.tmp_dir = Path("/tmp/robin_test_investigations")
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
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

    @unittest_run_loop
    async def test_get_run_detail_not_found(self):
        resp = await self.client.request("GET", "/api/runs/nonexistent_run")
        assert resp.status == 404

    @unittest_run_loop
    async def test_websocket_live_ping(self):
        ws = await self.client.ws_connect("/ws/live")
        await ws.send_str("ping")
        msg = await ws.receive_str()
        assert msg == "pong"
        await ws.close()

    @unittest_run_loop
    async def test_abort_nonexistent_run(self):
        resp = await self.client.request("POST", "/api/investigate/nonexistent/abort")
        assert resp.status == 404

    @unittest_run_loop
    async def test_get_index(self):
        resp = await self.client.request("GET", "/")
        assert resp.status == 200
        text = await resp.text()
        assert "Robin-CTI" in text

    @unittest_run_loop
    async def test_investigate_empty_query(self):
        resp = await self.client.request("POST", "/api/investigate", json={"query": ""})
        assert resp.status == 400

    @unittest_run_loop
    async def test_followup_empty_question(self):
        resp = await self.client.request("POST", "/api/followup", json={"question": ""})
        assert resp.status == 400

    @unittest_run_loop
    async def test_followup_missing_context(self):
        resp = await self.client.request("POST", "/api/followup", json={"question": "What leaked?"})
        assert resp.status == 400

    @unittest_run_loop
    async def test_investigate_start_and_abort(self):
        # Launch investigation with dummy query
        resp = await self.client.request(
            "POST",
            "/api/investigate",
            json={"query": "test query", "search_scope": "darkweb"},
        )
        assert resp.status == 202
        data = await resp.json()
        assert "run_id" in data
        assert data["status"] == "IN_PROGRESS"
        run_id = data["run_id"]

        # Abort the investigation
        abort_resp = await self.client.request("POST", f"/api/investigate/{run_id}/abort")
        assert abort_resp.status == 200
        abort_data = await abort_resp.json()
        assert abort_data["status"] == "ABORTED"

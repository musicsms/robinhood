from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from modules.viewer.server import STATIC_DIR, create_viewer_app


def test_static_assets_exist():
    assert (STATIC_DIR / "index.html").exists()
    assert (STATIC_DIR / "style.css").exists()
    assert (STATIC_DIR / "viewer.js").exists()

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "Robin-CTI" in html
    assert "ws/live" in (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")


def test_index_html_structure():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # Brand and header status badges
    assert "Robin-CTI" in html
    assert "status-tor" in html
    assert "status-cti" in html
    assert "status-model" in html
    assert "run-select" in html
    assert "btn-new-investigation" in html

    # New Investigation Form
    assert "investigation-form" in html
    assert "query-input" in html
    assert "darkweb" in html
    assert "clearweb" in html
    assert "preset-select" in html
    assert "iterations-input" in html
    assert "btn-start-investigation" in html
    assert "btn-abort-investigation" in html

    # Pipeline Stage Timeline cards
    for stage in ["refine", "cti_lookup", "search", "filter", "scrape", "eval_gap", "summarize"]:
        assert f"stage-{stage}" in html

    # Right Workspace Tabs (6 tabs)
    assert "tab-terminal" in html
    assert "tab-reasoning" in html
    assert "tab-findings" in html
    assert "tab-report" in html
    assert "tab-followup" in html
    assert "tab-json" in html

    # Bottom Replay Bar
    assert "timeline-slider" in html
    assert "btn-play-pause" in html
    assert "btn-prev" in html
    assert "btn-next" in html
    assert "speed-select" in html


def test_style_css_rules():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert "#0d1117" in css or "#0a0e17" in css
    assert "#161b22" in css or "#0f172a" in css
    assert "terminal-screen" in css
    assert "tab-btn" in css


def test_viewer_js_features():
    js = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    assert "/ws/live" in js
    assert "/api/runs" in js
    assert "/api/investigate" in js
    assert "/api/followup" in js
    assert "timeline-slider" in js or "goToStep" in js


def test_settings_modal_html_structure():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # Header gear button
    assert "btn-open-settings" in html

    # Settings modal container
    assert "modal-settings" in html

    # Provider and input controls
    assert "settings-provider" in html
    assert "settings-base-url" in html
    assert "settings-api-key" in html
    assert "btn-toggle-key-visibility" in html
    assert "settings-model-name" in html

    # Alert box and action buttons
    assert "settings-alert" in html
    assert "btn-save-settings" in html
    assert "settings-test-save" in html
    assert "btn-close-settings" in html

    # Provider options
    for prov in ["deepseek", "openai", "anthropic", "google", "ollama", "custom"]:
        assert prov in html.lower()


def test_settings_modal_css_rules():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert "modal-overlay" in css
    assert "modal-card" in css
    assert "btn-icon" in css
    assert "settings-alert" in css or "alert" in css


def test_settings_modal_js_integration():
    js = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    assert "/api/settings/llm" in js
    assert "modal-settings" in js or "openSettingsModal" in js or "elModalSettings" in js
    assert "btn-open-settings" in js
    assert "btn-save-settings" in js or "saveSettings" in js
    assert "btn-toggle-key-visibility" in js or "toggleKeyVisibility" in js



class TestViewerStaticServing(AioHTTPTestCase):
    async def get_application(self):
        tmp_dir = Path("/tmp/robin_test_static_serve")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        return create_viewer_app(investigations_dir=tmp_dir)

    @unittest_run_loop
    async def test_serves_index_html(self):
        resp = await self.client.request("GET", "/")
        assert resp.status == 200
        text = await resp.text()
        assert "Robin-CTI" in text
        assert resp.content_type == "text/html"

    @unittest_run_loop
    async def test_serves_static_assets(self):
        css_resp = await self.client.request("GET", "/static/style.css")
        assert css_resp.status == 200
        assert "text/css" in css_resp.content_type

        js_resp = await self.client.request("GET", "/static/viewer.js")
        assert js_resp.status == 200
        assert "javascript" in js_resp.content_type

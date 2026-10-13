# Web LLM Settings & Dynamic Provider Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to configure LLM providers, API keys, endpoints, and model names directly from the Robin-CTI Web Console, test connections in real-time, and hot-reload runtime configuration without restarting servers or editing `.env` manually.

**Architecture:** A settings service (`modules/viewer/settings.py`) providing masked reads, credential validation tests, hot-reloading of `RobinConfig` and model cache, and persistence to `.env`. Two REST endpoints (`GET /api/settings/llm` and `POST /api/settings/llm`) in `modules/viewer/server.py`. A cyber-styled settings modal in `modules/viewer/static/` with presets for DeepSeek, OpenAI, Anthropic, Google Gemini, Ollama, and Custom endpoints.

**Tech Stack:** Python 3.10+, `aiohttp`, `pydantic`, Vanilla JS (ES6), HTML5, CSS3.

**Spec:** `docs/superpowers/specs/2026-10-13-web-llm-settings-design.md`

## Global Constraints
- Zero-build frontend requirement: Vanilla HTML/CSS/JS only.
- Invariant: Preserve all 390 existing unit tests and safety guarantees.
- Security: Never expose raw API keys in GET responses (mask as `sk-...xxxx`).

---

### Task 1: Backend Settings Endpoints & Hot-Reload

**Files:**
- Create: `modules/viewer/settings.py`
- Modify: `modules/viewer/server.py`
- Test: `tests/test_viewer_settings.py`

**Interfaces:**
- Consumes: `config.RobinConfig`, `llm.get_llm`, `model_registry`.
- Produces:
  - `get_llm_settings(cfg: RobinConfig) -> dict`
  - `update_llm_settings(data: dict, app: web.Application) -> Tuple[bool, str, dict]`
  - `test_llm_connection(model: str, cfg: RobinConfig) -> Tuple[bool, str]`
  - `GET /api/settings/llm`, `POST /api/settings/llm` in `server.py`

- [ ] **Step 1: Write failing test in `tests/test_viewer_settings.py`**

```python
# tests/test_viewer_settings.py
from pathlib import Path
import pytest
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from config import RobinConfig
from modules.viewer.server import create_viewer_app
from modules.viewer.settings import mask_api_key, get_llm_settings

def test_mask_api_key():
    assert mask_api_key("sk-1234567890abcdef") == "sk-1234...cdef"
    assert mask_api_key("short") == "s...t"
    assert mask_api_key("") == ""
    assert mask_api_key(None) == ""

class TestSettingsEndpoints(AioHTTPTestCase):
    async def get_application(self):
        self.tmp_dir = Path("/tmp/robin_test_settings")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        return create_viewer_app(investigations_dir=self.tmp_dir)

    @unittest_run_loop
    async def test_get_settings_masked(self):
        resp = await self.client.request("GET", "/api/settings/llm")
        assert resp.status == 200
        data = await resp.json()
        assert "provider" in data
        assert "has_api_key" in data
        assert "api_key_masked" in data

    @unittest_run_loop
    async def test_post_settings_invalid_provider(self):
        resp = await self.client.request("POST", "/api/settings/llm", json={"provider": "unknown_provider"})
        assert resp.status == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_viewer_settings.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'modules.viewer.settings'`

- [ ] **Step 3: Implement `modules/viewer/settings.py` and mount endpoints in `server.py`**

Implement:
- `mask_api_key(key: Optional[str]) -> str`
- `get_llm_settings(cfg: RobinConfig) -> dict`
- `test_llm_connection(model: str, cfg: RobinConfig) -> Tuple[bool, str]` (attempts simple dummy invocation or resolution)
- `persist_to_env(updates: dict, env_path: Path)` (updates `.env` preserving existing comments)
- In `server.py`: add `GET /api/settings/llm` and `POST /api/settings/llm`

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_viewer_settings.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add modules/viewer/settings.py modules/viewer/server.py tests/test_viewer_settings.py
git commit -m "feat(viewer): implement LLM settings backend endpoints and hot-reload"
```

---

### Task 2: Web UI Settings Modal & Dynamic Connection Testing

**Files:**
- Modify: `modules/viewer/static/index.html`
- Modify: `modules/viewer/static/style.css`
- Modify: `modules/viewer/static/viewer.js`
- Test: `tests/test_viewer_static.py`

**Interfaces:**
- Consumes: REST endpoints `/api/settings/llm`.
- Produces: Cyberpunk settings modal with presets (DeepSeek, OpenAI, Gemini, Claude, Ollama, Custom), realtime test feedback, and hot-reloading.

- [ ] **Step 1: Write static asset verification test**

In `tests/test_viewer_static.py`:
Add tests asserting `#modal-settings`, `#btn-open-settings`, `#settings-provider`, `#settings-test-save`, and JS methods exist.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_viewer_static.py -k "settings" -v`  
Expected: FAIL

- [ ] **Step 3: Implement settings modal in `index.html`, `style.css`, and `viewer.js`**

- `index.html`:
  - Add gear button `<button id="btn-open-settings" class="btn-icon" title="LLM Provider Settings">⚙️</button>` in top header.
  - Add `#modal-settings` containing:
    - Provider Select (DeepSeek, OpenAI, Anthropic, Google Gemini, Ollama, Custom).
    - Base URL Input.
    - API Key Input (with show/hide eye toggle button).
    - Model Name Input.
    - Status Alert Box (`#settings-alert`).
    - Actions: `Test & Save` (`#btn-save-settings`), `Cancel` (`#btn-close-settings`).
- `style.css`:
  - Styles for `.modal-overlay`, `.modal-card`, `.settings-grid`, `.btn-icon`, eye toggle button, alert styles.
- `viewer.js`:
  - Wire opening/closing modal.
  - Fetch `/api/settings/llm` on open and populate fields.
  - Auto-fill default endpoint on provider change (e.g. DeepSeek -> `https://api.deepseek.com/v1`, OpenAI -> `https://api.openai.com/v1`).
  - Wire `Test & Save` button sending payload to `POST /api/settings/llm`.
  - Handle success/error feedback.
  - On success, reload status and update top model badge and model dropdown.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_viewer_static.py -v`  
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/pytest tests/ -v`  
Expected: ALL tests PASS (390+ tests).

- [ ] **Step 6: Commit**

```bash
git add modules/viewer/static/ tests/test_viewer_static.py
git commit -m "feat(viewer): implement cyber settings modal with dynamic LLM provider configuration"
```

# Design Specification: Web LLM Settings & Dynamic Provider Configuration

**Date:** 2026-10-13  
**Status:** Approved  
**Author:** Antigravity & Musicsms  

---

## 1. Executive Summary & Goals

### 1.1 Motivation
Currently, configuring LLM providers, API keys, endpoints, and model names in Robin-CTI requires editing `.env` files or passing environment variables into Docker containers. When credentials are missing or changed, investigations fail at the `load_llm` stage. Users need the ability to configure, test, and hot-reload LLM providers directly from the Web Console interface.

### 1.2 Core Objectives
1. **Interactive Settings Modal in Web UI:** A cyber-themed settings modal accessible from the top navigation bar (gear button ⚙️ next to Model badge) to configure:
   - Provider (DeepSeek, OpenAI, Anthropic, Google Gemini, Ollama, Custom OpenAI-Compatible)
   - Base URL / Endpoint (e.g. `https://api.deepseek.com/v1`, `http://localhost:11434`)
   - API Key (with secure masking `sk-...xxxx` and visibility toggle)
   - Model Name (e.g. `deepseek-chat`, `deepseek-reasoner`, `gpt-4o-mini`, `gemini-2.0-flash`)
2. **Connection Testing & Validation:** A "Test & Save" button sending a lightweight verification ping to the model provider, returning instant visual feedback (green checkmark or descriptive error message).
3. **Hot-Reloading & Persistence:**
   - Immediately updates the in-memory runtime `RobinConfig` and model registry without restarting the server.
   - Persists changes back to `.env` if writable, ensuring continuity across container and server restarts.
4. **Security & Redaction:**
   - `GET /api/settings/llm` masks secret keys (only returns prefix and last 4 characters, e.g. `sk-8f19...22aa`).
   - Secret redaction adheres to `config.redact_secrets` invariants.
5. **Zero-Build & Regression Safety:**
   - Pure Vanilla JS, HTML, CSS.
   - 100% preservation of all 390 existing unit tests and safety filters.

---

## 2. API Design & Endpoints (`modules/viewer/server.py`)

### 2.1 `GET /api/settings/llm`
Returns the current LLM configuration:
```json
{
  "provider": "custom",
  "base_url": "https://api.deepseek.com/v1",
  "api_key_masked": "sk-8f19...22aa",
  "has_api_key": true,
  "model": "deepseek-flash",
  "available_models": ["deepseek-flash", "deepseek-chat"]
}
```

### 2.2 `POST /api/settings/llm`
Accepts a new LLM configuration:
```json
{
  "provider": "custom",
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "sk-8f19a5cf663e4d00b414cbf48afe22aa",
  "model": "deepseek-chat",
  "test_connection": true
}
```
Response on success:
```json
{
  "success": true,
  "message": "Connected and saved successfully!",
  "model": "deepseek-chat",
  "provider": "custom"
}
```
Response on failure:
```json
{
  "success": false,
  "error": "Authentication failed: 401 Unauthorized"
}
```

---

## 3. UI/UX Specification (`modules/viewer/static/`)

### 3.1 Header Navigation Integration
- Add a settings button `<button id="btn-open-settings" class="btn-icon" title="LLM Provider Settings">⚙️</button>` directly beside `#status-model`.

### 3.2 Settings Modal (`#modal-settings`)
- Backdrop: Dark translucent overlay (`rgba(0, 0, 0, 0.75)` with blur).
- Modal Card: Cyber Dark `#161b22`, border `#30363d`, neon blue/green accents.
- Fields:
  - Provider Preset Selector:
    - DeepSeek (`https://api.deepseek.com/v1`)
    - OpenAI (`https://api.openai.com/v1`)
    - Google Gemini
    - Anthropic Claude
    - Ollama Local (`http://localhost:11434`)
    - Custom Endpoint
  - Endpoint Base URL input (autofilled by preset, editable).
  - API Key input (`type="password"`, eye toggle to reveal, placeholder shows masked key if already set).
  - Model Name input or dropdown.
  - Buttons: `Test & Save`, `Cancel`.
  - Alert banner: displays green success or red connection error with clear error text.

---

## 4. Verification & Testing Plan
1. Unit tests in `tests/test_viewer_settings.py`:
   - `test_get_llm_settings_masked`: verifies masking of API keys and correct provider detection.
   - `test_post_llm_settings_validation`: tests saving custom endpoint and hot-reloading `RobinConfig`.
   - `test_test_connection_failure_and_success`: tests mock LLM ping with valid/invalid credentials.
   - `test_env_persistence`: tests updating `.env` file cleanly.
2. Frontend integration test in `tests/test_viewer_static.py`:
   - Verify presence of modal markup, settings button, and JS handler functions.
3. Full regression suite run: All 390+ unit tests must pass.

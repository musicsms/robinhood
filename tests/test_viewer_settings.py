# tests/test_viewer_settings.py
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from config import RobinConfig
from modules.viewer.server import create_viewer_app
from modules.viewer.settings import (
    mask_api_key,
    get_llm_settings,
    persist_to_env,
    test_llm_connection,
    update_llm_settings,
)


def test_mask_api_key():
    assert mask_api_key("sk-1234567890abcdef") == "sk-1234...cdef"
    assert mask_api_key("short") == "s...t"
    assert mask_api_key("") == ""
    assert mask_api_key(None) == ""


def test_persist_to_env(tmp_path: Path):
    env_file = tmp_path / ".env"
    initial_content = (
        "# Existing comment\n"
        "OPENAI_API_KEY=sk-initial\n"
        "\n"
        "# Another comment\n"
        "ROBIN_MODEL=gpt-4\n"
    )
    env_file.write_text(initial_content, encoding="utf-8")

    updates = {
        "OPENAI_API_KEY": "sk-updated",
        "CUSTOM_API_BASE_URL": "https://api.example.com/v1",
    }
    persist_to_env(updates, env_file)

    result = env_file.read_text(encoding="utf-8")
    assert "# Existing comment\n" in result
    assert "OPENAI_API_KEY=sk-updated\n" in result
    assert "# Another comment\n" in result
    assert "ROBIN_MODEL=gpt-4\n" in result
    assert "CUSTOM_API_BASE_URL=https://api.example.com/v1\n" in result


def test_test_llm_connection_no_model():
    cfg = RobinConfig()
    ok, err = test_llm_connection("", cfg)
    assert not ok
    assert "No model" in err


@patch("llm.get_llm")
def test_test_llm_connection_success(mock_get_llm):
    mock_client = MagicMock()
    mock_get_llm.return_value = mock_client
    cfg = RobinConfig(openai_api_key="sk-test")
    ok, msg = test_llm_connection("gpt-4o", cfg)
    assert ok
    assert "successful" in msg
    mock_client.invoke.assert_called_once()


@patch("llm.get_llm")
def test_test_llm_connection_failure_redacted(mock_get_llm):
    cfg = RobinConfig(openai_api_key="sk-secret123456789")
    mock_get_llm.side_effect = Exception("Failed with secret key sk-secret123456789 in error")
    ok, msg = test_llm_connection("gpt-4o", cfg)
    assert not ok
    assert "sk-secret123456789" not in msg
    assert "***" in msg


class TestSettingsEndpoints(AioHTTPTestCase):
    def setUp(self):
        import os
        self._environ_backup = dict(os.environ)
        self._persist_patch = patch("modules.viewer.settings.persist_to_env")
        self.mock_persist = self._persist_patch.start()
        super().setUp()

    def tearDown(self):
        import os
        super().tearDown()
        self._persist_patch.stop()
        os.environ.clear()
        os.environ.update(self._environ_backup)

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
        assert "model" in data
        assert "available_models" in data

    @unittest_run_loop
    async def test_post_settings_invalid_provider(self):
        resp = await self.client.request("POST", "/api/settings/llm", json={"provider": "unknown_provider"})
        assert resp.status == 400
        data = await resp.json()
        assert data.get("success") is False

    @unittest_run_loop
    async def test_post_settings_invalid_json(self):
        resp = await self.client.request("POST", "/api/settings/llm", data="not-json")
        assert resp.status == 400

    @unittest_run_loop
    async def test_post_settings_custom_success(self):
        payload = {
            "provider": "custom",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-8f19a5cf663e4d00b414cbf48afe22aa",
            "model": "deepseek-chat",
            "test_connection": False,
        }
        resp = await self.client.request("POST", "/api/settings/llm", json=payload)
        assert resp.status == 200
        data = await resp.json()
        assert data["success"] is True
        assert data["model"] == "deepseek-chat"
        assert data["provider"] == "custom"

        # Verify GET now returns the updated settings
        get_resp = await self.client.request("GET", "/api/settings/llm")
        assert get_resp.status == 200
        get_data = await get_resp.json()
        assert get_data["provider"] == "custom"
        assert get_data["model"] == "deepseek-chat"
        assert get_data["has_api_key"] is True
        assert get_data["api_key_masked"] == "sk-8f19...22aa"

    @unittest_run_loop
    async def test_post_settings_test_connection_failure(self):
        with patch("modules.viewer.settings.test_llm_connection") as mock_test:
            mock_test.return_value = (False, "Authentication failed: 401 Unauthorized")
            payload = {
                "provider": "openai",
                "api_key": "sk-badkey",
                "model": "gpt-4o",
                "test_connection": True,
            }
            resp = await self.client.request("POST", "/api/settings/llm", json=payload)
            assert resp.status == 400
            data = await resp.json()
            assert data["success"] is False
            assert "401" in data["error"]

    @unittest_run_loop
    async def test_post_settings_test_connection_success(self):
        with patch("modules.viewer.settings.test_llm_connection") as mock_test:
            mock_test.return_value = (True, "Connection successful.")
            payload = {
                "provider": "openai",
                "api_key": "sk-goodkey12345678",
                "model": "gpt-4o",
                "test_connection": True,
            }
            resp = await self.client.request("POST", "/api/settings/llm", json=payload)
            assert resp.status == 200
            data = await resp.json()
            assert data["success"] is True
            assert "Connected" in data["message"]
            assert data["model"] == "gpt-4o"

    @unittest_run_loop
    async def test_post_settings_switch_provider_clears_custom_endpoint(self):
        # 1. Set custom endpoint first
        custom_payload = {
            "provider": "custom",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-custom123456",
            "model": "deepseek-chat",
            "test_connection": False,
        }
        resp = await self.client.request("POST", "/api/settings/llm", json=custom_payload)
        assert resp.status == 200

        # 2. Switch to gemini
        gemini_payload = {
            "provider": "gemini",
            "api_key": "AIzaSyTest123456",
            "model": "gemini-2.0-flash",
            "test_connection": False,
        }
        resp2 = await self.client.request("POST", "/api/settings/llm", json=gemini_payload)
        assert resp2.status == 200

        # 3. Check GET returns gemini and no deepseek base_url
        get_resp = await self.client.request("GET", "/api/settings/llm")
        assert get_resp.status == 200
        get_data = await get_resp.json()
        assert get_data["provider"] == "gemini"
        assert get_data["model"] == "gemini-2.0-flash"
        assert "deepseek" not in get_data.get("base_url", "")

# tests/test_main_entrypoint.py
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from main import main, parse_args


def test_parse_args_defaults():
    args = parse_args([])
    assert args.host == "0.0.0.0"
    assert args.port == 8080


def test_parse_args_custom():
    args = parse_args(["--host", "127.0.0.1", "--port", "9090"])
    assert args.host == "127.0.0.1"
    assert args.port == 9090


def test_parse_args_sys_argv(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--host", "0.0.0.0", "--port", "8888"])
    args = parse_args()
    assert args.host == "0.0.0.0"
    assert args.port == 8888


@patch("main.run_viewer_server")
@patch("main.check_tor_proxy")
@patch("main.init_cti_index")
def test_main_execution_flow(mock_cti, mock_tor, mock_run_server):
    mock_tor.return_value = {"status": "up", "latency_ms": 120, "error": None}
    mock_cti.return_value = 42

    main(["--host", "127.0.0.1", "--port", "9000"])

    mock_tor.assert_called_once()
    mock_cti.assert_called_once()
    mock_run_server.assert_called_once_with(host="127.0.0.1", port=9000)


@patch("main.run_viewer_server")
@patch("main.check_tor_proxy")
@patch("main.init_cti_index")
def test_main_handles_tor_down(mock_cti, mock_tor, mock_run_server):
    mock_tor.return_value = {"status": "down", "error": "Connection refused"}

    main(["--port", "8080"])

    mock_tor.assert_called_once()
    mock_run_server.assert_called_once_with(host="0.0.0.0", port=8080)


@patch("main.run_viewer_server")
@patch("main.check_tor_proxy")
@patch("main.init_cti_index")
def test_main_handles_cti_error(mock_cti, mock_tor, mock_run_server):
    mock_tor.return_value = {"status": "up"}
    mock_cti.side_effect = RuntimeError("Database locked")

    main([])

    mock_run_server.assert_called_once_with(host="0.0.0.0", port=8080)


def test_viewer_js_markdown_link_protocol_sanitizer():
    from pathlib import Path
    js_path = Path("modules/viewer/static/viewer.js")
    assert js_path.exists()
    content = js_path.read_text(encoding="utf-8")
    # Verify protocol validation regex prevents javascript: execution
    assert "https?:\\/\\/|#" in content

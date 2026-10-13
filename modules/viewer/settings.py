"""Settings service for Robin-CTI LLM provider configuration and hot-reloading."""

import dataclasses
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import web

import config
from config import RobinConfig, get_config, redact_secrets

logger = logging.getLogger(__name__)

VALID_PROVIDERS = {
    "custom",
    "deepseek",
    "openai",
    "anthropic",
    "google",
    "gemini",
    "mistral",
    "openrouter",
    "ollama",
}


def mask_api_key(key: Optional[str]) -> str:
    """Mask sensitive API key string, showing prefix and suffix."""
    if not key:
        return ""
    key = str(key).strip()
    if not key:
        return ""
    if len(key) >= 12:
        return f"{key[:7]}...{key[-4:]}"
    if len(key) >= 2:
        return f"{key[0]}...{key[-1]}"
    return "..."


def get_llm_settings(cfg: Optional[RobinConfig] = None) -> dict:
    """Return the current LLM configuration and available models."""
    if cfg is None:
        cfg = get_config()

    env_provider = (os.getenv("ROBIN_PROVIDER") or "").strip().lower()

    if env_provider in VALID_PROVIDERS:
        provider = env_provider
    elif cfg.custom_api_base_url or cfg.custom_api_key:
        provider = "custom"
    elif cfg.openai_api_key:
        provider = "openai"
    elif cfg.anthropic_api_key:
        provider = "anthropic"
    elif cfg.google_api_key:
        provider = "google"
    elif cfg.mistral_api_key:
        provider = "mistral"
    elif cfg.openrouter_api_key:
        provider = "openrouter"
    elif cfg.ollama_base_url and cfg.ollama_base_url != config.DEFAULT_OLLAMA_BASE_URL:
        provider = "ollama"
    else:
        provider = "custom"

    base_url = ""
    raw_key = None
    default_model_name = ""

    if provider in ("custom", "deepseek"):
        base_url = cfg.custom_api_base_url or ("https://api.deepseek.com/v1" if provider == "deepseek" else "")
        raw_key = cfg.custom_api_key
        default_model_name = cfg.custom_api_model or cfg.robin_model or ""
    elif provider == "openai":
        base_url = "https://api.openai.com/v1"
        raw_key = cfg.openai_api_key
        default_model_name = cfg.robin_model or "gpt-4o-mini"
    elif provider == "anthropic":
        base_url = "https://api.anthropic.com/v1"
        raw_key = cfg.anthropic_api_key
        default_model_name = cfg.robin_model or "claude-3-5-sonnet"
    elif provider in ("google", "gemini"):
        base_url = "https://generativelanguage.googleapis.com/v1beta"
        raw_key = cfg.google_api_key
        default_model_name = cfg.robin_model or "gemini-2.0-flash"
    elif provider == "mistral":
        base_url = "https://api.mistral.ai/v1"
        raw_key = cfg.mistral_api_key
        default_model_name = cfg.robin_model or "mistral-large-latest"
    elif provider == "openrouter":
        base_url = cfg.openrouter_base_url or "https://openrouter.ai/api/v1"
        raw_key = cfg.openrouter_api_key
        default_model_name = cfg.robin_model or ""
    elif provider == "ollama":
        base_url = cfg.ollama_base_url or config.DEFAULT_OLLAMA_BASE_URL
        raw_key = None
        default_model_name = cfg.robin_model or ""

    has_key = bool(raw_key and str(raw_key).strip() and not str(raw_key).strip().lower().startswith("your_"))
    masked_key = mask_api_key(raw_key) if has_key else ""

    available_models: List[str] = []
    if cfg.custom_api_model:
        available_models.append(cfg.custom_api_model)
    if cfg.robin_model and cfg.robin_model not in available_models:
        available_models.append(cfg.robin_model)
    try:
        from model_registry import get_entries
        entries = get_entries(cfg)
        available_models.extend([e["key"] for e in entries])
    except Exception:
        pass

    available_models = list(dict.fromkeys(available_models))

    active_model = default_model_name
    if not active_model:
        if cfg.robin_model:
            active_model = cfg.robin_model
        elif available_models:
            active_model = available_models[0]

    return {
        "provider": provider,
        "base_url": base_url,
        "api_key_masked": masked_key,
        "has_api_key": has_key,
        "model": active_model,
        "available_models": available_models,
    }


def test_llm_connection(model: str, cfg: RobinConfig) -> Tuple[bool, str]:
    """Test LLM connection with lightweight ping."""
    target_model = model or cfg.custom_api_model or cfg.robin_model or ""
    if not target_model:
        return False, "No model specified for connection test."

    try:
        from llm import get_llm
        from langchain_core.messages import HumanMessage

        test_cfg = cfg
        if test_cfg.custom_api_base_url and test_cfg.custom_api_model != target_model:
            test_cfg = dataclasses.replace(test_cfg, custom_api_model=target_model)

        client = get_llm(target_model, cfg=test_cfg)
        client.invoke([HumanMessage(content="ping")])
        return True, "Connection successful."
    except Exception as exc:
        safe_err = redact_secrets(str(exc), cfg)
        return False, safe_err


test_llm_connection.__test__ = False


def persist_to_env(updates: dict, env_path: Optional[Path] = None) -> None:
    """Update or append key-value pairs in .env while preserving existing comments."""
    if env_path is None:
        env_path = Path(".env")

    lines: List[str] = []
    if env_path.exists():
        try:
            content = env_path.read_text(encoding="utf-8")
            lines = content.splitlines(keepends=True)
        except OSError as exc:
            logger.warning("Could not read %s for persistence: %s", env_path, exc)
            return

    new_lines: List[str] = []
    pending_updates = dict(updates)

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            if key in pending_updates:
                val = pending_updates.pop(key)
                val_str = "" if val is None else str(val)
                new_lines.append(f"{key}={val_str}\n")
                continue
        new_lines.append(line)

    if pending_updates:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] = new_lines[-1] + "\n"
        for key, val in pending_updates.items():
            val_str = "" if val is None else str(val)
            new_lines.append(f"{key}={val_str}\n")

    try:
        env_path.write_text("".join(new_lines), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write to %s for persistence: %s", env_path, exc)


def update_llm_settings(data: dict, app: web.Application) -> Tuple[bool, str, dict]:
    """Hot-reload runtime RobinConfig, test connection if requested, and persist to .env."""
    provider = (data.get("provider") or "").strip().lower()
    if not provider or provider not in VALID_PROVIDERS:
        return False, f"Invalid provider '{provider}'. Must be one of: {', '.join(sorted(VALID_PROVIDERS))}", {}

    base_url = (data.get("base_url") or "").strip()
    api_key = (data.get("api_key") or "").strip()
    model = (data.get("model") or "").strip()
    test_connection = bool(data.get("test_connection", False))

    current_cfg = None
    try:
        from modules.viewer.server import ROBIN_CONFIG_HOLDER_KEY
        if ROBIN_CONFIG_HOLDER_KEY in app:
            holder = app[ROBIN_CONFIG_HOLDER_KEY]
            if isinstance(holder, dict) and "cfg" in holder:
                current_cfg = holder["cfg"]
    except Exception:
        pass

    if current_cfg is None:
        holder = app.get("robin_config_holder")
        if isinstance(holder, dict) and "cfg" in holder:
            current_cfg = holder["cfg"]
    if current_cfg is None:
        current_cfg = app.get("robin_config") or get_config()

    existing_key = None
    if provider in ("custom", "deepseek"):
        existing_key = current_cfg.custom_api_key
    elif provider == "openai":
        existing_key = current_cfg.openai_api_key
    elif provider == "anthropic":
        existing_key = current_cfg.anthropic_api_key
    elif provider in ("google", "gemini"):
        existing_key = current_cfg.google_api_key
    elif provider == "mistral":
        existing_key = current_cfg.mistral_api_key
    elif provider == "openrouter":
        existing_key = current_cfg.openrouter_api_key

    if not api_key or "..." in api_key:
        effective_api_key = existing_key or ""
    else:
        effective_api_key = api_key

    env_updates: Dict[str, str] = {}
    cfg_updates: Dict[str, Any] = {}

    target_model = model or current_cfg.robin_model or ""

    if provider in ("custom", "deepseek"):
        target_base_url = base_url or ("https://api.deepseek.com/v1" if provider == "deepseek" else (current_cfg.custom_api_base_url or ""))
        env_updates["CUSTOM_API_BASE_URL"] = target_base_url
        cfg_updates["custom_api_base_url"] = target_base_url

        if effective_api_key:
            env_updates["CUSTOM_API_KEY"] = effective_api_key
            cfg_updates["custom_api_key"] = effective_api_key

        target_model = model or current_cfg.custom_api_model or "deepseek-chat"
        env_updates["CUSTOM_API_MODEL"] = target_model
        cfg_updates["custom_api_model"] = target_model
        env_updates["ROBIN_MODEL"] = target_model
        cfg_updates["robin_model"] = target_model
        env_updates["ROBIN_PROVIDER"] = provider
    else:
        # Avoid retaining stale custom_api_base_url and custom_api_model on provider switch
        cfg_updates["custom_api_base_url"] = None
        cfg_updates["custom_api_model"] = None
        env_updates["CUSTOM_API_BASE_URL"] = ""
        env_updates["CUSTOM_API_MODEL"] = ""

    if provider == "openai":
        if effective_api_key:
            env_updates["OPENAI_API_KEY"] = effective_api_key
            cfg_updates["openai_api_key"] = effective_api_key
        target_model = model or current_cfg.robin_model or "gpt-4o-mini"
        env_updates["ROBIN_MODEL"] = target_model
        cfg_updates["robin_model"] = target_model
        env_updates["ROBIN_PROVIDER"] = provider
    elif provider == "anthropic":
        if effective_api_key:
            env_updates["ANTHROPIC_API_KEY"] = effective_api_key
            cfg_updates["anthropic_api_key"] = effective_api_key
        target_model = model or current_cfg.robin_model or "claude-3-5-sonnet"
        env_updates["ROBIN_MODEL"] = target_model
        cfg_updates["robin_model"] = target_model
        env_updates["ROBIN_PROVIDER"] = provider
    elif provider in ("google", "gemini"):
        if effective_api_key:
            env_updates["GOOGLE_API_KEY"] = effective_api_key
            cfg_updates["google_api_key"] = effective_api_key
        target_model = model or current_cfg.robin_model or "gemini-2.0-flash"
        env_updates["ROBIN_MODEL"] = target_model
        cfg_updates["robin_model"] = target_model
        env_updates["ROBIN_PROVIDER"] = provider
    elif provider == "mistral":
        if effective_api_key:
            env_updates["MISTRAL_API_KEY"] = effective_api_key
            cfg_updates["mistral_api_key"] = effective_api_key
        target_model = model or current_cfg.robin_model or "mistral-large-latest"
        env_updates["ROBIN_MODEL"] = target_model
        cfg_updates["robin_model"] = target_model
        env_updates["ROBIN_PROVIDER"] = provider
    elif provider == "openrouter":
        if effective_api_key:
            env_updates["OPENROUTER_API_KEY"] = effective_api_key
            cfg_updates["openrouter_api_key"] = effective_api_key
        if base_url:
            env_updates["OPENROUTER_BASE_URL"] = base_url
            cfg_updates["openrouter_base_url"] = base_url
        if target_model:
            env_updates["ROBIN_MODEL"] = target_model
            cfg_updates["robin_model"] = target_model
        env_updates["ROBIN_PROVIDER"] = provider
    elif provider == "ollama":
        target_base_url = base_url or config.DEFAULT_OLLAMA_BASE_URL
        env_updates["OLLAMA_BASE_URL"] = target_base_url
        cfg_updates["ollama_base_url"] = target_base_url
        if target_model:
            env_updates["ROBIN_MODEL"] = target_model
            cfg_updates["robin_model"] = target_model
        env_updates["ROBIN_PROVIDER"] = provider

    candidate_cfg = dataclasses.replace(current_cfg, **cfg_updates)

    if test_connection:
        test_model_choice = model or target_model
        ok, test_msg = test_llm_connection(test_model_choice, candidate_cfg)
        if not ok:
            return False, test_msg, {}

    for k, v in env_updates.items():
        if v:
            os.environ[k] = str(v)
        else:
            os.environ.pop(k, None)

    new_cfg = RobinConfig.from_env()
    config._env_config = new_cfg
    try:
        from modules.viewer.server import ROBIN_CONFIG_HOLDER_KEY
        if ROBIN_CONFIG_HOLDER_KEY in app and isinstance(app[ROBIN_CONFIG_HOLDER_KEY], dict):
            app[ROBIN_CONFIG_HOLDER_KEY]["cfg"] = new_cfg
    except Exception:
        pass
    holder = app.get("robin_config_holder")
    if isinstance(holder, dict):
        holder["cfg"] = new_cfg
    if not getattr(app, "frozen", False):
        try:
            app["robin_config"] = new_cfg
        except Exception:
            pass

    try:
        import llm_utils
        for fn in (llm_utils.fetch_ollama_models, llm_utils.fetch_llama_cpp_models, llm_utils.fetch_custom_api_models):
            if hasattr(fn, "cache_clear"):
                fn.cache_clear()
    except Exception:
        pass

    try:
        import model_registry
        model_registry.refresh(new_cfg)
    except Exception:
        pass

    env_file = Path(".env")
    persist_to_env(env_updates, env_file)

    msg = "Connected and saved successfully!" if test_connection else "Settings saved successfully!"
    return True, msg, {
        "model": target_model,
        "provider": provider,
    }

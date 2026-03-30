#!/usr/bin/env python3
"""LLM provider abstraction — supports Ollama, OpenAI-compatible APIs,
Anthropic, Google Gemini, and OpenRouter.

All providers expose the same interface: send messages, get text back.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("agent_runner")

# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------

# Registry of known providers with their default base URLs and API paths.
# Users can override base_url via LLM_BASE_URL env var.
PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "ollama": {
        "base_url": "http://ollama:11434",
        "chat_path": "/api/chat",
        "format": "ollama",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "chat_path": "/chat/completions",
        "format": "openai",
        "default_model": "gpt-4o",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "chat_path": "/chat/completions",
        "format": "openai",
        "default_model": "deepseek-coder",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "chat_path": "/v1/messages",
        "format": "anthropic",
        "default_model": "claude-3-5-sonnet-20241022",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "chat_path": "",  # Built dynamically with model name.
        "format": "gemini",
        "default_model": "gemini-2.0-flash",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "chat_path": "/chat/completions",
        "format": "openai",
        "default_model": "deepseek/deepseek-coder",
    },
}


@dataclass
class LLMConfig:
    """Resolved configuration for an LLM provider."""

    provider: str
    base_url: str
    chat_path: str
    api_format: str
    model: str
    api_key: str
    timeout: int
    temperature: float = 0.2
    max_tokens: int = 16384
    extra_headers: dict[str, str] = field(default_factory=dict)


def resolve_config() -> LLMConfig:
    """Build an LLMConfig from environment variables.

    Env vars:
        LLM_PROVIDER   — provider name (default: ollama)
        LLM_BASE_URL   — override base URL
        LLM_MODEL      — override model name
        LLM_API_KEY    — API key (required for cloud providers)
        LLM_TIMEOUT_SEC — request timeout in seconds
    """
    provider = os.getenv("LLM_PROVIDER", "ollama").lower().strip()
    defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["ollama"])

    base_url = os.getenv("LLM_BASE_URL", "").strip() or defaults["base_url"]
    model = os.getenv("LLM_MODEL", "").strip() or defaults.get("default_model", "llama3")
    api_key = os.getenv("LLM_API_KEY", "").strip()
    timeout = int(os.getenv("LLM_TIMEOUT_SEC", "300"))

    # Cloud providers require an API key.
    if provider != "ollama" and not api_key:
        log.warning(
            "LLM_API_KEY is not set for provider '%s'. "
            "Requests will likely fail. Set it in .env.",
            provider,
        )

    return LLMConfig(
        provider=provider,
        base_url=base_url.rstrip("/"),
        chat_path=defaults["chat_path"],
        api_format=defaults["format"],
        model=model,
        api_key=api_key,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Unified chat function
# ---------------------------------------------------------------------------


def chat(config: LLMConfig, messages: list[dict[str, str]]) -> str:
    """Send a chat request to the configured LLM and return the response text."""
    log.info("Calling LLM: provider=%s model=%s ...", config.provider, config.model)

    if config.api_format == "ollama":
        return _chat_ollama(config, messages)
    elif config.api_format == "openai":
        return _chat_openai(config, messages)
    elif config.api_format == "anthropic":
        return _chat_anthropic(config, messages)
    elif config.api_format == "gemini":
        return _chat_gemini(config, messages)
    else:
        raise ValueError(f"Unknown API format: {config.api_format}")


# ---------------------------------------------------------------------------
# Provider-specific implementations
# ---------------------------------------------------------------------------


def _chat_ollama(config: LLMConfig, messages: list[dict[str, str]]) -> str:
    """Ollama native API."""
    url = f"{config.base_url}{config.chat_path}"
    payload = {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": config.temperature, "num_predict": config.max_tokens},
    }
    resp = _post(url, payload, config)
    return resp.get("message", {}).get("content", "")


def _chat_openai(config: LLMConfig, messages: list[dict[str, str]]) -> str:
    """OpenAI-compatible API (also DeepSeek, OpenRouter)."""
    url = f"{config.base_url}{config.chat_path}"
    headers: dict[str, str] = {"Authorization": f"Bearer {config.api_key}"}
    if config.provider == "openrouter":
        headers["HTTP-Referer"] = "https://nexora.local"
        headers["X-Title"] = "Nexora Agent"
    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }
    resp = _post(url, payload, config, extra_headers=headers)
    choices = resp.get("choices", [])
    if choices:
        return choices[0].get("message", {}).get("content", "")
    return ""


def _chat_anthropic(config: LLMConfig, messages: list[dict[str, str]]) -> str:
    """Anthropic Messages API."""
    url = f"{config.base_url}{config.chat_path}"
    headers = {
        "x-api-key": config.api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    # Anthropic separates system prompt from messages.
    system_text = ""
    user_messages: list[dict[str, str]] = []
    for m in messages:
        if m["role"] == "system":
            system_text += m["content"] + "\n"
        else:
            user_messages.append(m)

    payload: dict[str, Any] = {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "messages": user_messages,
    }
    if system_text.strip():
        payload["system"] = system_text.strip()

    resp = _post(url, payload, config, extra_headers=headers)
    content_blocks = resp.get("content", [])
    texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
    return "\n".join(texts)


def _chat_gemini(config: LLMConfig, messages: list[dict[str, str]]) -> str:
    """Google Gemini API."""
    url = (
        f"{config.base_url}/models/{config.model}:generateContent"
        f"?key={config.api_key}"
    )
    # Convert messages to Gemini format.
    contents: list[dict[str, Any]] = []
    system_text = ""
    for m in messages:
        if m["role"] == "system":
            system_text += m["content"] + "\n"
        else:
            role = "user" if m["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

    # Prepend system text to the first user message.
    if system_text.strip() and contents:
        first_text = contents[0]["parts"][0]["text"]
        contents[0]["parts"][0]["text"] = f"{system_text.strip()}\n\n{first_text}"

    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": config.temperature,
            "maxOutputTokens": config.max_tokens,
        },
    }
    resp = _post(url, payload, config)
    candidates = resp.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        return "\n".join(p.get("text", "") for p in parts)
    return ""


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _post(
    url: str,
    payload: dict[str, Any],
    config: LLMConfig,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """POST JSON and return the parsed response."""
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=config.timeout)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]
    except httpx.HTTPStatusError as exc:
        log.error(
            "LLM HTTP error %s: %s",
            exc.response.status_code,
            exc.response.text[:500],
        )
        raise
    except httpx.ConnectError:
        log.error("Cannot connect to LLM at %s. Is the service running?", url)
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Preflight check
# ---------------------------------------------------------------------------


def preflight_check(config: LLMConfig) -> None:
    """Verify the LLM is reachable. For Ollama, also check the model is pulled."""
    if config.provider == "ollama":
        _preflight_ollama(config)
    else:
        # For cloud providers, do a lightweight connectivity check.
        log.info("Preflight: checking %s connectivity...", config.provider)
        try:
            # Just verify DNS + TLS by hitting the base URL.
            httpx.get(config.base_url, timeout=10)
            log.info("Preflight: %s is reachable.", config.provider)
        except httpx.ConnectError:
            log.error(
                "Cannot reach %s at %s. Check your internet connection.",
                config.provider,
                config.base_url,
            )
            raise SystemExit(1)
        except httpx.HTTPStatusError:
            # A 401/403 is fine here — it means the server is reachable.
            log.info("Preflight: %s is reachable (auth checked at request time).", config.provider)


def _preflight_ollama(config: LLMConfig) -> None:
    """Check Ollama is running and the model is available."""
    log.info("Preflight: checking Ollama at %s ...", config.base_url)
    try:
        resp = httpx.get(f"{config.base_url}/api/tags", timeout=15)
        resp.raise_for_status()
    except httpx.ConnectError:
        log.error(
            "Cannot connect to Ollama at %s.\n"
            "  Make sure the Ollama container is running:\n"
            "    docker compose up -d ollama\n"
            "  Then try again.",
            config.base_url,
        )
        raise SystemExit(1)
    except httpx.HTTPStatusError as exc:
        log.error("Ollama returned HTTP %s: %s", exc.response.status_code, exc.response.text[:300])
        raise SystemExit(1)

    data = resp.json()
    available_models: list[str] = []
    for m in data.get("models", []):
        name = m.get("name", "")
        available_models.append(name)
        if name == config.model or name.startswith(f"{config.model}:"):
            log.info("Preflight: model '%s' is available.", config.model)
            return

    if available_models:
        models_str = ", ".join(available_models)
        log.error(
            "Model '%s' is not downloaded. Available models: %s\n"
            "  Pull the model first:\n"
            "    docker exec nexora-ollama ollama pull %s",
            config.model,
            models_str,
            config.model,
        )
    else:
        log.error(
            "No models found in Ollama. Pull a model first:\n"
            "    docker exec nexora-ollama ollama pull %s",
            config.model,
        )
    raise SystemExit(1)

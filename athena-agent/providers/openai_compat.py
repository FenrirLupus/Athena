"""OpenAI-compatible provider — OpenAI, DeepSeek, OpenRouter, vLLM, llama.cpp, LM Studio, Ollama.

One adapter for every endpoint that speaks `/chat/completions` with tool calling, which is why a local
model costs nothing extra: llama.cpp's server, Ollama's `/v1` and DeepSeek's API are the same wire
format, so "run a downloaded model" and "use a custom URL" are the same configuration with a different
`base_url`.

Translation lives in `providers/wire.py`; the key comes from the environment or `.secret`, never from
config.yaml.
"""
from __future__ import annotations

import json

import httpx

from providers import wire
from providers.base import BaseProvider, ProviderError, api_key


class OpenAICompatProvider(BaseProvider):
    name = "openai"

    def __init__(self, base_url: str, model: str, timeout: float = 120.0,
                 allow_empty_base: bool = False, key_name: str = "", secret_root=None):
        # `allow_empty_base` exists for LocalProvider, which fills the URL once its server is up.
        if not base_url and not allow_empty_base:
            raise ProviderError(
                "model.base_url is empty — set it (e.g. https://api.deepseek.com/v1) or enable model.local")
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.timeout = timeout
        self.key_name = key_name          # a line in .secret; empty means the format's default key
        self.secret_root = secret_root    # the agent's profile, so its own `.secret` is consulted first

    def describe(self) -> dict:
        return {"provider": "openai", "model": self.model, "base_url": self.base_url}

    def build_request(self, messages: list[dict], *, tools: list[dict] | None = None,
                      max_tokens: int = 1024, temperature: float = 0.7) -> tuple[str, dict, dict]:
        """(url, headers, payload) — the wire shape, exposed so a test can assert it offline."""
        if not self.base_url:
            raise ProviderError("no base_url — the local model server has not been started")
        payload: dict = {
            "model": self.model,
            "messages": wire.to_openai(messages),
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "stream": False,
        }
        if tools:
            payload["tools"] = [t if t.get("type") == "function" else {"type": "function", "function": t}
                                for t in tools]
        endpoint = wire.join_endpoint(self.base_url, "/chat/completions")

        # Sidecar: this process never holds a key. It names the upstream and the proxy signs it on the far side.
        from providers import base as provider_base

        proxy = getattr(provider_base, "PROXY", None) or {}
        if proxy.get("url"):
            return (proxy["url"].rstrip("/") + "/forward",
                    {"Content-Type": "application/json",
                     "X-Athena-Upstream": endpoint,
                     "X-Athena-Proxy": proxy.get("token", "")},
                    payload)

        headers = {"Content-Type": "application/json"}
        key = api_key("openai", self.key_name, root=self.secret_root)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        # A local server needs no key; sending an empty bearer makes some of them 401.
        return endpoint, headers, payload

    def call(self, messages: list[dict], *, tools: list[dict] | None = None,
             max_tokens: int = 1024, temperature: float = 0.7) -> dict:
        url, headers, payload = self.build_request(
            messages, tools=tools, max_tokens=max_tokens, temperature=temperature)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"request failed: {exc}") from exc

        if resp.status_code >= 400:
            detail = resp.text[:300]
            try:
                body = resp.json()
                detail = (body.get("error") or {}).get("message") or detail
            except (json.JSONDecodeError, AttributeError):
                pass
            raise ProviderError(f"HTTP {resp.status_code}: {detail}")

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(f"non-JSON response: {resp.text[:200]}") from exc

        return wire.from_openai(data)

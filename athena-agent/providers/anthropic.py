"""Anthropic provider — the Messages API.

The second wire format Athena must speak. It differs from OpenAI in ways that silently corrupt a
conversation if translated carelessly, so all of the translation lives in `providers/wire.py` and this
file only moves bytes:

  * `system` is a top-level string, not a message
  * tool results are `tool_result` blocks inside a **user** message
  * roles must alternate
  * tools carry `input_schema`, not `parameters`
  * `max_tokens` is required, not optional

`base_url` defaults to the public API but any gateway that keeps the Messages API works — which is the
point: a different URL is a different provider, not a different code path.
"""
from __future__ import annotations

import json

import httpx

from core import trace
from providers import base as provider_base
from providers import wire
from providers.base import BaseProvider, ProviderError, api_key

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_BASE = "https://api.anthropic.com"


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, base_url: str = DEFAULT_BASE, model: str = "claude-sonnet-4-5",
                 timeout: float = 120.0, key_name: str = "", secret_root=None):
        self.base_url = (base_url or DEFAULT_BASE).rstrip("/")
        self.model = model
        self.timeout = timeout
        self.key_name = key_name
        self.secret_root = secret_root

    def describe(self) -> dict:
        return {"provider": "anthropic", "model": self.model, "base_url": self.base_url}

    def build_request(self, messages: list[dict], *, tools: list[dict] | None = None,
                      max_tokens: int = 1024, temperature: float = 0.7) -> tuple[str, dict, dict]:
        """(url, headers, payload) — exposed so the selftest can assert the wire shape without a network."""
        system, turns = wire.to_anthropic(messages)
        payload: dict = {
            "model": self.model,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "messages": turns,
        }
        if system.strip():
            payload["system"] = system
        anth_tools = wire.tools_to_anthropic(tools)
        if anth_tools:
            payload["tools"] = anth_tools

        headers = {"Content-Type": "application/json",
                   "anthropic-version": ANTHROPIC_VERSION}
        key = api_key("anthropic", self.key_name, root=self.secret_root)
        if key:
            headers["x-api-key"] = key
        else:
            raise ProviderError(
                "no Anthropic key: set ANTHROPIC_API_KEY or put `anthropic=<key>` in .athena/.secret")

        return wire.join_endpoint(self.base_url, "/messages"), headers, payload

    def call(self, messages: list[dict], *, tools: list[dict] | None = None,
             max_tokens: int = 1024, temperature: float = 0.7) -> dict:
        url, headers, payload = self.build_request(
            messages, tools=tools, max_tokens=max_tokens, temperature=temperature)
        scope = provider_base.scope_fields()
        trace.event("model.http", level="trace", **scope, provider=self.name, model=self.model,
                    endpoint=self.base_url, messages=len(payload.get("messages") or []),
                    tools=len(payload.get("tools") or []), stream=False)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            trace.event("model.http.failed", level="debug", **scope, provider=self.name,
                        model=self.model, exc=f"{type(exc).__name__}: {exc}")
            raise ProviderError(f"request failed: {exc}") from exc

        if resp.status_code >= 400:
            detail = resp.text[:300]
            try:
                body = resp.json()
                detail = (body.get("error") or {}).get("message") or detail
            except (json.JSONDecodeError, AttributeError):
                pass
            trace.event("model.http.rejected", level="debug", **scope, provider=self.name,
                        model=self.model, status=resp.status_code, detail=detail[:200])
            raise ProviderError(f"HTTP {resp.status_code}: {detail}")

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            trace.event("model.http.nonjson", level="debug", **scope, provider=self.name,
                        model=self.model, status=resp.status_code, body=resp.text[:200])
            raise ProviderError(f"non-JSON response: {resp.text[:200]}") from exc

        return wire.from_anthropic(data)

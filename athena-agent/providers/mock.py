"""Mock provider — scripted, offline, and the reason 0.0.1 is testable without a network.

Behaviour:
  * a user message containing "echo <text>" produces ONE tool call to `echo`, then on the next
    call returns the tool's output — this exercises the iterative path
  * anything else returns a short canned reply quoting the user

Usage is reported as a rough token estimate (characters / 4) so the *measurement* path — tokens in, tokens
out, cost, throughput — is exercised offline instead of only against a live provider. Cost is left at 0.0 so
the pricing stays the operator's setting.
"""
from __future__ import annotations

import json

from providers.base import BaseProvider


class MockProvider(BaseProvider):
    name = "mock"

    def __init__(self, model_name: str = "mock-1"):
        self.model_name = model_name

    def call(self, messages: list[dict], *, tools: list[dict] | None = None,
             max_tokens: int = 1024, temperature: float = 0.7) -> dict:
        result = self._reply(messages, tools, max_tokens)
        prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
        prompt = max(1, prompt_chars // 4)
        completion = max(1, len(str(result.get("content") or "")) // 4)
        result["usage"] = {"prompt_tokens": prompt, "completion_tokens": completion,
                           "total_tokens": prompt + completion}
        return result

    def _reply(self, messages: list[dict], tools: list[dict] | None, max_tokens: int) -> dict:
        last_user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user = msg.get("content") or ""
                break

        # Already ran a tool THIS TURN? report its result instead of asking again.
        # Only tool messages after the latest user message count — otherwise a result from a
        # previous turn makes every later turn look like it already has its answer.
        last_user_idx = max((i for i, m in enumerate(messages) if m.get("role") == "user"),
                            default=-1)
        tool_results = [m for m in messages[last_user_idx + 1:] if m.get("role") == "tool"]
        if tool_results:
            return {"content": f"The tool said: {tool_results[-1].get('content', '')}",
                    "tool_calls": [], "cost_usd": 0.0, "finish_reason": "stop"}

        if last_user.lower().startswith("echo "):
            want = last_user[5:].strip()
            return {"content": "", "cost_usd": 0.0, "finish_reason": "tool_calls",
                    "tool_calls": [{"id": "call_mock_1", "name": "echo",
                                    "arguments": {"text": want}}]}

        names = ", ".join((t.get("function") or {}).get("name", "?")
                          for t in (tools or [])) or "none"
        return {"content": f"[mock:{self.model_name}] you said: {last_user!r}. "
                           f"tools available: {names}.",
                "tool_calls": [], "cost_usd": 0.0, "finish_reason": "stop"}

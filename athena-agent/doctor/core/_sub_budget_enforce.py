"""Iteration budget ENFORCEMENT test — the loop really stops at the cap."""
from __future__ import annotations


class _FakeProvider:
    """A provider that always asks for a tool — forcing iteration."""
    base_url = "http://fake"
    api_key = "x"
    models = ["fake-model"]
    name = "fake"
    ready = True

    def __init__(self, calls=None):
        self.calls = calls or []
        self.attempts = 0

    def record(self, body):
        self.calls.append(body)
        return {"choices": [{"message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "t1", "type": "function",
                            "function": {"name": "read", "arguments": "{\"path\": \"/tmp/x\"}"}}],
        }}]}


class _FakeTools:
    @staticmethod
    def schemas():
        return [{"type": "function", "function": {"name": "read"}}]

    @staticmethod
    def execute_tool_call(tc):
        return "file content"


def run() -> list[dict]:
    from core import message_loop as ml_mod
    from core.message_loop import MessageLoop

    checks = []
    # Backup the real tool registry and provider post.
    orig_schemas = ml_mod.tool_registry.schemas
    orig_exec = ml_mod.tool_registry.execute_tool_call
    orig_post = None
    try:
        from providers import provider as prov_mod
        orig_post = prov_mod._post_json
    except Exception:
        pass

    ml_mod.tool_registry.schemas = _FakeTools.schemas
    ml_mod.tool_registry.execute_tool_call = _FakeTools.execute_tool_call

    try:
        # 1. ITERATION ENFORCEMENT: a loop capped at 3 iterations must make
        #    exactly 3 provider calls even though the model keeps asking.
        calls = []
        provider = _FakeProvider(calls)

        def fake_post(url, key, body):
            calls.append(body)
            return {"choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "t1", "type": "function",
                                "function": {"name": "read", "arguments": "{\"path\": \"/tmp/x\"}"}}],
            }}]}

        if orig_post is not None:
            prov_mod._post_json = fake_post
        loop = MessageLoop(providers=_Chain(provider), system_prompt="s",
                           max_iterations=3, max_tokens=5120)
        # The budget test exercises the BLOCKING path (the fake post
        # mock); streaming is covered by the live chat tests.
        loop.streaming = False
        result = loop.run_turn("do it")
        # THE PROMPT-FIRST DESIGN (the CEO's 08-15 correction): the
        # workflow selection folds into the full prompt — there is NO
        # separate START call. Cap=3 iterations → exactly 3 provider calls.
        checks.append({
            "name": "iterations enforced (stops at cap)",
            "status": "ok" if len(calls) == 3 else "fail",
            "detail": f"{len(calls)} calls for cap=3",
        })
        checks.append({
            "name": "exit reason = budget exhausted",
            "status": "ok" if result.exit_reason == "budget_exhausted" else "fail",
            "detail": f"exit_reason={result.exit_reason}",
        })
        # 2. TOKEN ENFORCEMENT: every outgoing body carries max_tokens.
        bodies = [b for b in calls if isinstance(b, dict)]
        all_have = all(b.get("max_tokens") == 5120 for b in bodies)
        checks.append({
            "name": "max_tokens in every request",
            "status": "ok" if all_have else "fail",
            "detail": f"{sum(1 for b in bodies if b.get('max_tokens'))}/{len(bodies)} carry 5120",
        })
    finally:
        ml_mod.tool_registry.schemas = orig_schemas
        ml_mod.tool_registry.execute_tool_call = orig_exec
        if orig_post is not None:
            prov_mod._post_json = orig_post
    return checks


class _Chain:
    """A minimal provider chain stand-in with ready_provider() + the
    provider list the loop walks (primary → fallback)."""
    def __init__(self, provider):
        self._provider = provider
        self.providers = [provider]

    def ready_provider(self):
        return self._provider

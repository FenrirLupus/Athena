"""Multi-format provider parsing test — the major chat formats.

The message loop must parse ANY major provider's response shape and
normalize it to the schema's reason + usage groups:

    OpenAI / LM Studio / Qwen / DeepSeek / vLLM / Ollama
        choices[0].message.{content, tool_calls}  +  finish_reason  +  usage

    Anthropic native (/v1/messages)
        content = [text | tool_use blocks]  +  stop_reason  +  usage.{input, output}
"""
from __future__ import annotations


def run() -> list[dict]:
    import types
    from unittest.mock import patch
    from core.message_loop import MessageLoop, TurnResult

    checks = []

    class FakeProvider:
        base_url = "https://example.com/v1"
        api_key = "k"
        models = ["fake-model"]
        name = "fake"
        ready = True

    class FakeProviders:
        providers = [FakeProvider()]

        def ready_provider(self):
            return FakeProvider()

    fake_tools = types.SimpleNamespace(schemas=lambda: [])
    loop = MessageLoop.__new__(MessageLoop)
    loop.providers = FakeProviders()
    loop.max_tokens = None
    # The formats test exercises the BLOCKING call shape (the streaming
    # path is covered by the live chat tests) — keep the mockable path.
    loop.streaming = False

    # 1. OpenAI-compatible shape → finish_reason + usage + reasoning.
    openai_resp = {
        "choices": [{"message": {"content": "hello", "tool_calls": None,
                                 "reasoning_content": "I considered the options."},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 13, "completion_tokens": 18, "total_tokens": 31},
    }
    with patch("core.message_loop.tool_registry", fake_tools), \
         patch("providers.provider._post_json", return_value=openai_resp):
        r = loop._call_model([{"role": "user", "content": "hi"}])
    checks.append({
        "name": "OpenAI-compat: finish_reason + usage",
        "status": "ok" if r["finish_reason"] == "stop"
        and r["usage"].get("total_tokens") == 31 else "fail",
        "detail": f"fr={r['finish_reason']} usage={r['usage']}",
    })
    checks.append({
        "name": "OpenAI-compat: reasoning chain captured",
        "status": "ok" if r.get("reasoning") == "I considered the options."
        else "fail",
        "detail": f"reasoning={r.get('reasoning')!r}",
    })

    # 2. Anthropic native shape → blocks joined, stop_reason mapped,
    #    tool_use blocks → OpenAI tool_calls, input/output → prompt/completion,
    #    thinking blocks → the reasoning chain.
    anthropic_resp = {
        "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "toolu_01", "name": "read_file",
             "input": {"path": "/tmp/x"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 27, "output_tokens": 35},
    }
    FakeProvider.base_url = "https://api.anthropic.com/v1/messages"
    with patch("core.message_loop.tool_registry", fake_tools), \
         patch("providers.provider._post_json", return_value=anthropic_resp):
        r2 = loop._call_model([{"role": "user", "content": "hi"}])
    tc_ok = (r2["tool_calls"] and r2["tool_calls"][0]["function"]["name"] == "read_file"
             and "arguments" in r2["tool_calls"][0]["function"])
    checks.append({
        "name": "Anthropic: blocks → text + tool_calls",
        "status": "ok" if r2["content"] == "Let me check." and tc_ok else "fail",
        "detail": f"content={r2['content']!r}",
    })
    checks.append({
        "name": "Anthropic: stop_reason + usage mapped",
        "status": "ok" if r2["finish_reason"] == "tool_use"
        and r2["usage"].get("prompt_tokens") == 27
        and r2["usage"].get("total_tokens") == 62 else "fail",
        "detail": f"fr={r2['finish_reason']} usage={r2['usage']}",
    })

    # 2b. Anthropic thinking blocks → the reasoning chain.
    anthropic_reason = {
        "content": [
            {"type": "thinking", "thinking": "The user asked about files."},
            {"type": "text", "text": "Here is the file."},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    with patch("core.message_loop.tool_registry", fake_tools), \
         patch("providers.provider._post_json", return_value=anthropic_reason):
        r2b = loop._call_model([{"role": "user", "content": "hi"}])
    checks.append({
        "name": "Anthropic: thinking → reasoning chain",
        "status": "ok" if r2b.get("reasoning") == "The user asked about files."
        else "fail",
        "detail": f"reasoning={r2b.get('reasoning')!r}",
    })

    # 3. TurnResult carries the metadata (the schema wiring end-to-end).
    tr = TurnResult(reply="ok", finish_reason="stop",
                    usage={"prompt_tokens": 5, "completion_tokens": 2,
                           "total_tokens": 7})
    checks.append({
        "name": "TurnResult carries reason + usage",
        "status": "ok" if tr.finish_reason == "stop"
        and tr.usage.get("total_tokens") == 7 else "fail",
        "detail": f"fr={tr.finish_reason} total={tr.usage.get('total_tokens')}",
    })

    # 4. REVERSE direction (the hub): canonical history → Anthropic
    #    request — tool_calls become tool_use blocks, tool results become
    #    tool_result blocks; OpenAI stays pass-through.
    history = [
        {"role": "user", "content": "read the file"},
        {"role": "assistant", "content": "let me read", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "read", "arguments": '{"path": "/tmp/x"}'}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "file contents"},
    ]
    an = loop._request_for_provider(history, is_anthropic=True)
    tool_use_ok = (an[1]["content"][1]["type"] == "tool_use"
                   and an[1]["content"][1]["name"] == "read")
    tool_res_ok = (an[2]["content"][0]["type"] == "tool_result"
                   and an[2]["content"][0]["tool_use_id"] == "call_1")
    checks.append({
        "name": "hub: canonical → Anthropic request",
        "status": "ok" if tool_use_ok and tool_res_ok else "fail",
        "detail": f"tool_use={tool_use_ok} tool_result={tool_res_ok}",
    })
    checks.append({
        "name": "hub: canonical → OpenAI pass-through",
        "status": "ok" if loop._request_for_provider(history, False) == history else "fail",
        "detail": "unchanged",
    })

    FakeProvider.base_url = "https://example.com/v1"

    # 5. COMPLETENESS: the full system-populated field set must be
    #    normalized identically for BOTH format families (the Operator's rule:
    #    every system-populated cell is compatible across all chat
    #    language formats).
    required = {"content", "tool_calls", "finish_reason", "usage", "reasoning"}
    missing_openai = required - set(r)
    missing_anthropic = required - set(r2b)
    checks.append({
        "name": "completeness: all system fields in both formats",
        "status": "ok" if not missing_openai and not missing_anthropic
        else "fail",
        "detail": f"openai missing={sorted(missing_openai)} "
                  f"anthropic missing={sorted(missing_anthropic)}",
    })
    return checks

"""Wire formats — the only place that knows how a provider spells a conversation.

Internal shape (what the session stores, what the loop appends):

    {"role": "user"|"assistant"|"system"|"tool", "content": str,
     "tool_calls": [{"id", "name", "arguments": dict}],     # assistant only
     "tool_call_id": str, "name": str}                       # tool results only

Two wire formats must be spoken, and they disagree about almost everything (documentation/architecture/17):

| | OpenAI | Anthropic |
|---|---|---|
| system prompt | a `system` message in the list | a top-level `system` string |
| tool call | `tool_calls[].function.arguments` **as a JSON string** | a `tool_use` content block, `input` an object |
| tool result | its own message, `role: "tool"` | a `tool_result` block inside a **user** message |
| roles | any order | must alternate user/assistant |
| tools | `{"type":"function","function":{...,"parameters"}}` | `{"name","description","input_schema"}` |
| reply | `choices[0].message` | `content[]` blocks |

Adapting a provider therefore means translating **in** and **out**, and the adapters own nothing else.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


# -- helpers ---------------------------------------------------------------

def _caller(depth: int = 2) -> str:
    """`file.py:line in function` for the site that recorded a line, not the helper that wrote it."""
    try:
        frame = sys._getframe(depth)
    except ValueError:
        return "?"
    return f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno} in {frame.f_code.co_name}"


def _t(name: str, level: str = "debug", **fields) -> None:
    """Emit a wire line in the calling adapter's lane, when a trace is running.

    Resolved lazily and defensively: this module must stay importable on its own (the offload runtime loads the
    parser without an adapter package), and tracing must never be the reason a request fails.
    """
    try:
        from core import trace
    except ImportError:                               # noqa: BLE001 — no repo root, no trail
        return
    if not trace.enabled(level):
        return
    # `where=` names the site that made the decision, not this helper: the trace records a frame for every line
    fields.setdefault("where", _caller())
    try:
        from providers.base import scope_fields
        fields = {**scope_fields(), **fields}
    except Exception:                                 # noqa: BLE001 — a wire helper must not need the adapter
        pass
    trace.event(name, level=level, **fields)


def join_endpoint(base_url: str, path: str, *, version: str = "/v1") -> str:
    """Join a base URL and an endpoint without doubling a version segment.

    Users point `base_url` at every shape of endpoint: `https://api.deepseek.com`,
    `https://api.deepseek.com/v1`, `http://127.0.0.1:8080/v1`. Requiring the right one produces a
    404 that reads like a broken key, so accept all three.
    """
    base = (base_url or "").rstrip("/")
    if not base:
        _t("wire.endpoint", level="error", shape="refused", rule="empty base_url", path=path)
        raise ValueError("empty base_url")
    if base.endswith("/v1") or base.endswith("/v1beta"):
        _t("wire.endpoint", level="trace", shape="versioned", path=path)
        return base + path
    if "/v1/" in base:               # e.g. a proxy that already carries the version mid-path
        _t("wire.endpoint", level="trace", shape="mid-path", path=path)
        return base + path
    _t("wire.endpoint", level="trace", shape="appended", version=version, path=path)
    return base + version + path


def _text_of(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):     # already block-shaped; flatten the text blocks
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("text"))
    return "" if content is None else str(content)


# -- OpenAI ----------------------------------------------------------------

def to_openai(messages: list[dict]) -> list[dict]:
    """Internal messages -> OpenAI chat messages (arguments JSON-encoded, no bookkeeping fields)."""
    out: list[dict] = []
    for msg in messages:
        role = msg.get("role") or "user"
        if role == "tool":
            out.append({"role": "tool",
                        "tool_call_id": msg.get("tool_call_id") or "",
                        "content": _text_of(msg)})
            continue
        entry: dict = {"role": role, "content": _text_of(msg)}
        calls = msg.get("tool_calls") or []
        if calls:
            entry["tool_calls"] = [
                {"id": c.get("id") or f"call_{i}",
                 "type": "function",
                 "function": {"name": c.get("name") or "",
                              "arguments": json.dumps(c.get("arguments") or {}, ensure_ascii=False)}}
                for i, c in enumerate(calls)]
            if not entry["content"]:
                entry["content"] = None         # OpenAI rejects "" alongside tool_calls
        out.append(entry)
    _t("wire.openai.request", messages=len(messages), sent=len(out),
       tool_calls=sum(len(m.get("tool_calls") or []) for m in out),
       tool_results=sum(1 for m in out if m.get("role") == "tool"))
    return out


def from_openai(data: dict) -> dict:
    """OpenAI chat completion -> the internal response shape."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    calls = []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        raw = fn.get("arguments")
        if isinstance(raw, str):
            try:
                args = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": raw}
                _t("wire.openai.rejected", level="error", rule="arguments not JSON", tool=fn.get("name") or "",
                   chars=len(raw))
        else:
            args = raw or {}
        calls.append({"id": tc.get("id") or f"call_{i}",
                      "name": fn.get("name") or "",
                      "arguments": args})
    usage = data.get("usage") or {}
    finish = choice.get("finish_reason") or ("tool_calls" if calls else "stop")
    _t("wire.openai.response", calls=len(calls), chars=len(msg.get("content") or ""),
       finish_reason=finish, choices=len(data.get("choices") or []), usage=bool(usage))
    return {
        "content": msg.get("content") or "",
        "tool_calls": calls,
        "finish_reason": finish,
        "usage": usage,
        "cost_usd": 0.0,
    }


# -- Anthropic -------------------------------------------------------------

def tools_to_anthropic(tools: list[dict] | None) -> list[dict]:
    """OpenAI function schemas -> Anthropic tool definitions."""
    out = []
    skipped = 0
    for tool in tools or []:
        fn = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(fn, dict) or not fn.get("name"):
            skipped += 1                              # a nameless schema is not a tool: Anthropic would 400
            continue
        out.append({"name": fn["name"],
                    "description": fn.get("description") or "",
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    if skipped:
        _t("wire.anthropic.tool.skipped", level="error", rule="no function name", skipped=skipped,
           tools=len(tools or []))
    return out


def to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """Internal messages -> (system prompt, Anthropic messages).

    Two rules the API enforces and this function therefore must:
      * `system` is **not** a message — every system turn is concatenated into the top-level string;
      * roles must **alternate**, and every `tool_result` for one assistant turn must arrive in a
        **single user message**. Consecutive same-role turns are merged into one with multiple blocks.
    """
    system_parts: list[str] = []
    turns: list[dict] = []
    merges = 0

    def push(role: str, blocks: list[dict]) -> None:
        nonlocal merges
        if not blocks:
            return
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)     # merge: Anthropic forbids two turns in a row
            merges += 1
        else:
            turns.append({"role": role, "content": list(blocks)})

    for msg in messages:
        role = msg.get("role") or "user"
        if role == "system":
            system_parts.append(_text_of(msg))
            continue
        if role == "tool":
            push("user", [{"type": "tool_result",
                           "tool_use_id": msg.get("tool_call_id") or "",
                           "content": _text_of(msg)}])
            continue
        if role == "assistant":
            blocks: list[dict] = []
            text = _text_of(msg)
            if text:
                blocks.append({"type": "text", "text": text})
            for call in msg.get("tool_calls") or []:
                blocks.append({"type": "tool_use",
                               "id": call.get("id") or "",
                               "name": call.get("name") or "",
                               "input": call.get("arguments") or {}})
            push("assistant", blocks)
            continue
        push("user", [{"type": "text", "text": _text_of(msg)}])

    system = "\n\n".join(p for p in system_parts if p.strip())
    _t("wire.anthropic.request", messages=len(messages), turns=len(turns), merged=merges,
       system_chars=len(system), system_parts=len(system_parts),
       tool_use=sum(1 for t in turns for b in t["content"] if b.get("type") == "tool_use"),
       tool_results=sum(1 for t in turns for b in t["content"] if b.get("type") == "tool_result"))
    return system, turns


def from_anthropic(data: dict) -> dict:
    """Anthropic message response -> the internal response shape."""
    text_parts: list[str] = []
    calls: list[dict] = []
    blocks = data.get("content") or []
    for block in blocks:
        kind = block.get("type")
        if kind == "text":
            text_parts.append(block.get("text") or "")
        elif kind == "tool_use":
            calls.append({"id": block.get("id") or "",
                          "name": block.get("name") or "",
                          "arguments": block.get("input") or {}})
    stop = data.get("stop_reason") or ""
    finish = {"tool_use": "tool_calls", "end_turn": "stop",
              "max_tokens": "length", "stop_sequence": "stop"}.get(stop, stop or "stop")
    if stop and stop not in ("tool_use", "end_turn", "max_tokens", "stop_sequence"):
        # an unmapped stop reason reaches the loop verbatim, which reads as a completed turn
        _t("wire.anthropic.stop.unmapped", level="error", rule="unknown stop_reason", stop_reason=stop)
    usage = data.get("usage") or {}
    _t("wire.anthropic.response", blocks=len(blocks), text=len(text_parts), calls=len(calls),
       stop_reason=stop, finish_reason=finish)
    return {
        "content": "".join(text_parts),
        "tool_calls": calls,
        "finish_reason": finish,
        "usage": {"prompt_tokens": usage.get("input_tokens", 0),
                  "completion_tokens": usage.get("output_tokens", 0),
                  "total_tokens": int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))},
        "cost_usd": 0.0,
    }

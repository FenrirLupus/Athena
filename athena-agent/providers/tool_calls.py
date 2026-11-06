"""Tool-call parsing — every encoding a local model is known to emit.

A model does not "call a tool"; it emits text in the shape its chat template taught it, and the runtime
has to read that text. Models disagree, and a parser that reads only one shape leaves raw markup in the
reply, which a user reads as the agent printing gibberish at them. The shapes seen in practice:

    <tool_call>{"name": "x", "arguments": {...}}</tool_call>                  Qwen2.5 · Hermes
    <tool_call><function=x><parameter=a>1</parameter></function></tool_call>  Qwen3.5
    <function=x><parameter=a>1</parameter></function>                         Qwen3.5, unwrapped
    [TOOL_CALLS] [{"name": "x", "arguments": {...}}]                          Mistral
    <|python_tag|>{"name": "x", "parameters": {...}}<|eom_id|>                Llama 3.1
    ```json {"name": "x", "arguments": {...}} ```                             DeepSeek distills
    {"name": "x", "arguments": {...}}                                         Functionary (bare)

Arguments arrive under `arguments`, `parameters` or `args`, sometimes as a JSON string and sometimes as
an object. All of it normalises to {"id", "name", "arguments": dict}.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

THINK_RE = re.compile(r"<think>.*?</think>", re.S)

TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.S)
FUNCTION_RE = re.compile(r"<function=(?P<name>[^>\s]+)>(?P<body>.*?)(?:</function>|$)", re.S)
PARAMETER_RE = re.compile(r"<parameter=(?P<key>[^>\s]+)>(?P<value>.*?)</parameter>", re.S)
MISTRAL_RE = re.compile(r"\[TOOL_CALLS\]\s*(\[.*?\])\s*$", re.S)
PYTHON_TAG_RE = re.compile(r"<\|python_tag\|>\s*(\{.*?\})\s*(?:<\|eom_id\|>|$)", re.S)
FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
BARE_JSON_RE = re.compile(r'\{\s*"(?:name|tool|function)"\s*:.*\}\s*$', re.S)

ARG_KEYS = ("arguments", "parameters", "args", "input")
NAME_KEYS = ("name", "tool", "function")


def _t(name: str, level: str = "debug", **fields) -> None:
    """Emit a parser line in the calling adapter's lane, when a trace is running.

    Resolved lazily and defensively: the offload runtime imports this module without an adapter package, and a
    diagnostic must never be the reason a tool call fails to parse.
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
    except Exception:                                 # noqa: BLE001 — the parser must not need the adapter
        pass
    trace.event(name, level=level, **fields)


def _caller(depth: int = 2) -> str:
    """`file.py:line in function` for the site that recorded a line, not the helper that wrote it."""
    try:
        frame = sys._getframe(depth)
    except ValueError:
        return "?"
    return f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno} in {frame.f_code.co_name}"


def strip_thinking(text: str) -> str:
    """Drop a reasoning preamble, with or without its opening tag.

    A thinking model that runs out of budget mid-thought emits `…reasoning…</think>` and no opening tag,
    so a paired regex misses it and the reasoning lands in the reply. Everything before the last closing
    tag is preamble; an agent's answer is what comes after it.
    """
    if "</think>" in text:
        before = len(text)
        text = text.rsplit("</think>", 1)[1]
        _t("tool.parse.thinking", level="trace", rule="preamble before the last </think>",
           dropped=before - len(text))
    return THINK_RE.sub("", text).strip()


def _call_from_dict(raw: dict, index: int) -> dict | None:
    """Normalise one call object from any of the encodings."""
    name = ""
    for key in NAME_KEYS:
        value = raw.get(key)
        if isinstance(value, dict):                 # {"function": {"name": ...}}
            value = value.get("name")
        if value:
            name = str(value)
            break
    args = None
    for key in ARG_KEYS:
        if key in raw:
            args = raw[key]
            break
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            try:                                     # Qwen sometimes emits unquoted key: value pairs
                args = json.loads(args.replace("'", '"'))
            except json.JSONDecodeError:
                args = {"_raw": args}
                _t("tool.parse.rejected", level="error", rule="arguments not JSON after quote repair",
                   call=index, tool=name.strip(), chars=len(args.get("_raw") or ""))
    if not name:
        # a call with no name is not a call: dropping it silently is how a turn answers prose to a request
        _t("tool.parse.dropped", level="error", rule="no name key", call=index, keys=len(raw))
        return None
    if not isinstance(args, dict):
        _t("tool.parse.rejected", level="error", rule="arguments not an object", call=index,
           tool=name.strip(), shape=type(args).__name__)
        return {"id": raw.get("id") or f"call_{index}", "name": name.strip(), "arguments": {"_raw": args}}
    return {"id": raw.get("id") or f"call_{index}", "name": name.strip(), "arguments": args}


def _calls_from_json(blob: str, start: int = 0) -> list[dict]:
    """Parse one call, a list of calls, or the first brace-balanced object inside prose."""
    blob = (blob or "").strip()
    if not blob:
        return []
    parsed = None
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        depth, begin = 0, -1
        for i, ch in enumerate(blob):
            if ch == "{":
                if depth == 0:
                    begin = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and begin >= 0:
                    try:
                        parsed = json.loads(blob[begin:i + 1])
                        break
                    except json.JSONDecodeError:
                        begin = -1
    items = parsed if isinstance(parsed, list) else [parsed] if isinstance(parsed, dict) else []
    out = []
    for offset, item in enumerate(items):
        if isinstance(item, dict):
            call = _call_from_dict(item, start + offset)
            if call:
                out.append(call)
    return out


def _xml_calls(chunk: str, start: int = 0) -> list[dict]:
    """`<function=name><parameter=k>v</parameter></function>` — Qwen3.5's form."""
    out = []
    for name, body in FUNCTION_RE.findall(chunk):
        args = {k.strip(): v.strip() for k, v in PARAMETER_RE.findall(body)}
        out.append({"id": f"call_{start + len(out)}", "name": name.strip(), "arguments": args})
    return out


def parse_tool_calls(text: str) -> tuple[str, list[dict]]:
    """Return (prose, calls). The prose never contains call markup."""
    calls: list[dict] = []
    shapes: list[str] = []
    prose = text or ""

    # <tool_call> … </tool_call> — JSON, XML, or both
    for block in TOOL_CALL_BLOCK_RE.findall(prose):
        inner = block.strip()
        xml = _xml_calls(inner, len(calls))
        if xml:
            calls.extend(xml)
            shapes.append("tool_call/xml")
        else:
            found = _calls_from_json(inner, len(calls))
            if found:
                shapes.append("tool_call/json")
            calls.extend(found)
    prose = TOOL_CALL_BLOCK_RE.sub("", prose)

    # a bare <function=…> without the wrapper
    bare = _xml_calls(prose, len(calls))
    if bare:
        calls.extend(bare)
        shapes.append("function/xml")
        prose = FUNCTION_RE.sub("", prose)

    # [TOOL_CALLS] [ … ]  (Mistral)
    for blob in MISTRAL_RE.findall(prose):
        found = _calls_from_json(blob, len(calls))
        if found:
            shapes.append("mistral")
        calls.extend(found)
    prose = MISTRAL_RE.sub("", prose)

    # <|python_tag|>{ … }<|eom_id|>  (Llama 3.1)
    for blob in PYTHON_TAG_RE.findall(prose):
        found = _calls_from_json(blob, len(calls))
        if found:
            shapes.append("python_tag")
        calls.extend(found)
    prose = PYTHON_TAG_RE.sub("", prose)

    # a fenced json block (DeepSeek distills)
    for blob in FENCE_RE.findall(prose):
        found = _calls_from_json(blob, len(calls))
        if found:
            calls.extend(found)
            shapes.append("fence")
            prose = FENCE_RE.sub(lambda m: "" if m.group(1) == blob else m.group(0), prose)

    # a bare JSON call at the end (Functionary)
    if not calls:
        match = BARE_JSON_RE.search(prose.strip())
        if match:
            found = _calls_from_json(match.group(0), 0)
            if found:
                calls = found
                shapes.append("bare-json")
                prose = prose[: match.start()]

    # a template can echo the same call twice (XML body and JSON fallback); keep one
    seen, unique = set(), []
    for call in calls:
        key = (call["name"], json.dumps(call["arguments"], sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        call["id"] = f"call_{len(unique)}"
        unique.append(call)

    # which encoding was read, and how many the template echoed twice: a model that slips into a shape the
    # parser half-reads is visible here and nowhere else
    _t("tool.parse", calls=len(unique), shapes=",".join(shapes) or "-", found=len(calls),
       echoed=len(calls) - len(unique), chars=len(text or ""))
    return strip_thinking(prose).strip(), unique

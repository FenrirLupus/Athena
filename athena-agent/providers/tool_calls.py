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


def strip_thinking(text: str) -> str:
    """Drop a reasoning preamble, with or without its opening tag.

    A thinking model that runs out of budget mid-thought emits `…reasoning…</think>` and no opening tag,
    so a paired regex misses it and the reasoning lands in the reply. Everything before the last closing
    tag is preamble; an agent's answer is what comes after it.
    """
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
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
    if not name:
        return None
    return {"id": raw.get("id") or f"call_{index}", "name": name.strip(),
            "arguments": args if isinstance(args, dict) else {"_raw": args}}


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
    prose = text or ""

    # <tool_call> … </tool_call> — JSON, XML, or both
    for block in TOOL_CALL_BLOCK_RE.findall(prose):
        inner = block.strip()
        xml = _xml_calls(inner, len(calls))
        if xml:
            calls.extend(xml)
        else:
            calls.extend(_calls_from_json(inner, len(calls)))
    prose = TOOL_CALL_BLOCK_RE.sub("", prose)

    # a bare <function=…> without the wrapper
    bare = _xml_calls(prose, len(calls))
    if bare:
        calls.extend(bare)
        prose = FUNCTION_RE.sub("", prose)

    # [TOOL_CALLS] [ … ]  (Mistral)
    for blob in MISTRAL_RE.findall(prose):
        calls.extend(_calls_from_json(blob, len(calls)))
    prose = MISTRAL_RE.sub("", prose)

    # <|python_tag|>{ … }<|eom_id|>  (Llama 3.1)
    for blob in PYTHON_TAG_RE.findall(prose):
        calls.extend(_calls_from_json(blob, len(calls)))
    prose = PYTHON_TAG_RE.sub("", prose)

    # a fenced json block (DeepSeek distills)
    for blob in FENCE_RE.findall(prose):
        found = _calls_from_json(blob, len(calls))
        if found:
            calls.extend(found)
            prose = FENCE_RE.sub(lambda m: "" if m.group(1) == blob else m.group(0), prose)

    # a bare JSON call at the end (Functionary)
    if not calls:
        match = BARE_JSON_RE.search(prose.strip())
        if match:
            found = _calls_from_json(match.group(0), 0)
            if found:
                calls = found
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

    return strip_thinking(prose).strip(), unique

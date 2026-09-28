"""Result classification — simple classified outputs for the model.

the Operator's spec: the model sees SIMPLE INPUTS and OUTPUTS; the SYSTEM
architecture handles the COMPLEX scripting and backends. When a tool or
skill runs, its raw backend output (long paths, stack traces, big JSON)
is CLASSIFIED into a short, clean signal:

    "ok: file written"            (success)
    "empty: no results"           (ran, nothing returned)
    "not_found: path missing"     (expected miss)
    "error: <short reason>"       (real failure)
    "denied: guardrail/permission"

THE CONTENT-AWARE RULE (the Operator's 08-12 release fix): tools whose
OUTPUT IS THE ANSWER — browser_open (fetch), web_search, web_extract,
read_file, terminal, fs_stat (listings) — re-inject the FULL output
(capped at _MAX_CONTENT) so the model can ACTUALLY answer from evidence
(the old 200-char cap made every article/file/search unreadable — the
"she can't pull the article" bug). Status tools keep the short signal.

The full raw output stays in the vault (the record); the model sees the
classification for status tools and the real content for content tools.
Both tools AND skills get this treatment.
"""
from __future__ import annotations

import re

_MAX_SUMMARY = 200
# Content-bearing tools pass through their full output (capped — the
# browser's own fetch cap is 20k; keep the same window).
_MAX_CONTENT = 20000

# Tools whose output IS the payload the model must read.
_CONTENT_TOOLS = {
    "browser_open", "web_search", "web_extract",
    "read_file", "terminal", "fs_stat", "skill_load",
}

# Classifications
OK = "ok"
EMPTY = "empty"
NOT_FOUND = "not_found"
ERROR = "error"
DENIED = "denied"

# Signals that make a result NOT_FOUND (expected misses).
_NOT_FOUND_MARKERS = (
    "not found", "no such file", "no such table", "does not exist",
    "no results", "nothing found", "empty result", "404",
)
# Signals that make a result DENIED.
_DENIED_MARKERS = ("[denied", "permission", "not allowed", "guardrail",
                   "refused", "blocked")
# Signals that make a result ERROR (real failures).
_ERROR_MARKERS = ("error:", "traceback", "failed:", "exception",
                  "connection refused", "timed out", "timeout")


def classify_result(raw: str, *, kind: str = "tool") -> dict:
    """Classify a raw backend output into a short signal.

    kind: "tool" | "skill" — the classification label prefix.

    Returns {status, summary, raw_len}. The summary is what the model
    sees; the raw output is preserved in the vault separately.
    """
    raw = raw or ""
    low = raw.strip().lower()

    if not low:
        return {"status": EMPTY, "summary": "empty: no output",
                "raw_len": 0}
    if any(m in low for m in _DENIED_MARKERS):
        return {"status": DENIED, "summary": "denied: not permitted",
                "raw_len": len(raw)}
    if any(m in low for m in _NOT_FOUND_MARKERS):
        first = _first_line(raw)
        return {"status": NOT_FOUND,
                "summary": f"not_found: {first[:_MAX_SUMMARY]}",
                "raw_len": len(raw)}
    if any(m in low for m in _ERROR_MARKERS):
        first = _first_line(raw)
        return {"status": ERROR,
                "summary": f"error: {first[:_MAX_SUMMARY]}",
                "raw_len": len(raw)}
    first = _first_line(raw)
    return {"status": OK, "summary": f"ok: {first[:_MAX_SUMMARY]}",
            "raw_len": len(raw)}


def _first_line(raw: str) -> str:
    """The first non-empty line, flattened."""
    for line in raw.splitlines():
        line = line.strip()
        if line:
            return re.sub(r"\s+", " ", line)
    return raw.strip()


def present(raw: str, *, kind: str = "tool", tool_name: str = "") -> str:
    """The view the model sees: the SHORT signal for status tools, the
    FULL content for content-bearing tools (the 08-12 release fix)."""
    if tool_name in _CONTENT_TOOLS:
        text = raw or ""
        if len(text) > _MAX_CONTENT:
            text = text[:_MAX_CONTENT] + (f"\n...[truncated, "
                                          f"{len(raw)} chars total]")
        return text or "empty: no output"
    c = classify_result(raw, kind=kind)
    return c["summary"]

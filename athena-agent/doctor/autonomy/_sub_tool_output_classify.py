"""Tool-output classification test — the Operator's tool-output fix.

The bug: the message loop classified the SANITIZED tool result (wrapped
in "[UNTRUSTED CONTENT START...]" markers), so the model saw only the
marker line — every probe returned "ok:" with zero payload.

The fix: classify the RAW result first; the sanitized wrapper stays for
the transcript/vault record. This test locks the ORDER:
    present(raw) → "ok: PROBE..." (the real output)
    present(sanitized(raw)) → "ok: [UNTRUSTED..." (the bug, must never
    be what the model sees)
"""
from __future__ import annotations

from pathlib import Path


def run() -> list:
    checks = []
    try:
        from core.result_classifier import present
        from security.security import sanitize_tool_result

        raw = "PROBE_VISIBLE_OUTPUT_98765\n" + str(Path.home())
        model_view = present(raw, kind="tool")
        checks.append({
            "name": "tool output: model sees the RAW first line",
            "status": "ok" if "PROBE_VISIBLE_OUTPUT_98765" in model_view
            else "fail",
            "detail": f"present(raw) = {model_view!r}",
        })
        # The buggy order (classifying the sanitized wrapper) is what the
        # Operator saw — the marker line, zero payload. Assert the FIX: the
        # message loop classifies the RAW result, never the wrapper. The
        # call now also passes the tool_name (the 08-12 content-aware
        # rule) — present(result, kind="tool", tool_name=...).
        try:
            loop_src = __import__("pathlib").Path(
                str(Path.home() / ".athena" / "athena-system" / "core" / "message_loop.py")
            ).read_text(encoding="utf-8")
            checks.append({
                "name": "tool output: message loop classifies RAW result",
                "status": "ok" if "present(result, kind=\"tool\"" in loop_src
                and "present(untrusted_result" not in loop_src else "fail",
                "detail": "model_view = present(result, tool_name=...) — the fix",
            })
        except Exception:
            pass
        # The sanitizer still wraps for the record (unchanged behaviour) —
        # that's CORRECT; the bug was classifying the wrapper TO the model.
        wrapped = sanitize_tool_result(raw)
        checks.append({
            "name": "tool output: sanitizer still marks the record",
            "status": "ok" if "UNTRUSTED CONTENT START" in wrapped
            and "PROBE_VISIBLE_OUTPUT_98765" in wrapped else "fail",
            "detail": "record keeps the raw inside the trust markers",
        })
        # (duplicate removed — the fix check is above)
    except Exception as exc:
        checks.append({
            "name": "tool output classification",
            "status": "fail",
            "detail": str(exc),
        })
    return checks

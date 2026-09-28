"""JSONL I/O contract test — supply JSONL in, fetch JSONL out, identical."""
from __future__ import annotations

import json
import uuid


def run() -> list[dict]:
    from core import db as db_layer
    import core.db as dbmod
    import tempfile
    from pathlib import Path as _P

    checks = []
    # SESSION HYGIENE (the Operator's 08-12 spec): this test writes a
    # session — redirect sessions to a tempdir + use a FIXED id so the
    # REAL .default sessions/ never gets a session-{uuid}.db debris file.
    with tempfile.TemporaryDirectory() as _td:
        _orig = dbmod.sessions_dir
        dbmod.sessions_dir = staticmethod(lambda *a, **k: _P(_td) / "sessions")
        (_P(_td) / "sessions").mkdir(parents=True, exist_ok=True)
        try:
            sid = "jsonl-io-test"
            jsonl_in = (
                '{"role": "user", "content": "hello"}'
                "\n"
                '{"role": "assistant", "content": "hi there", "tool": "read"}'
            )
            imported = db_layer.import_session_jsonl(sid, jsonl_in)
            checks.append({
                "name": "import JSONL → rows",
                "status": "ok" if imported == 2 else "fail",
                "detail": f"{imported} rows",
            })
            jsonl_out = db_layer.export_session_jsonl(sid)
            parsed_in = [json.loads(l) for l in jsonl_in.splitlines()]
            parsed_out = [json.loads(l) for l in jsonl_out.splitlines()]
            checks.append({
                "name": "export JSONL ← rows",
                "status": "ok" if len(parsed_out) == 2 else "fail",
                "detail": f"{len(parsed_out)} entries",
            })
            checks.append({
                "name": "round-trip identical",
                "status": "ok" if parsed_in == parsed_out else "fail",
                "detail": "",
            })
            checks.append({
                "name": "tool carried through",
                "status": "ok" if parsed_out[1].get("tool") == "read" else "fail",
                "detail": f"tool={parsed_out[1].get('tool')}",
            })
            # canonical shape has role + content + optional tool
            entry = parsed_out[0]
            ok_shape = {"role", "content"} <= set(entry.keys())
            checks.append({
                "name": "canonical shape (role/content)",
                "status": "ok" if ok_shape else "fail",
                "detail": f"keys={sorted(entry.keys())}",
            })
            # The prompt History block uses the same canonical renderer.
            from context.prompt_builder import build_prompt_stack
            stack = build_prompt_stack(
                channel="user", channel_instructions="CI",
                history=db_layer.get_session_history(sid, limit=10),
            )
            blocks = stack.split("\n\n---\n\n")
            hist = next((b for b in blocks if "Recent conversation (JSONL)" in b), "")
            hist_parsed = [json.loads(l) for l in hist.splitlines()[1:]]
            checks.append({
                "name": "prompt history = canonical shape",
                "status": "ok" if hist_parsed == parsed_out else "fail",
                "detail": f"match={hist_parsed == parsed_out}",
            })
        finally:
            dbmod.sessions_dir = _orig
    return checks

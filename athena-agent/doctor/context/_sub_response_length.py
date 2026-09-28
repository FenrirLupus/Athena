"""Response length test — the 7-level word-cap system (the Operator's spec)."""
from __future__ import annotations


def run() -> list[dict]:
    import tempfile
    from pathlib import Path
    from core.response_length import (levels, gauge, cap_for, prompt_line)
    import core.response_length as rl

    # ISOLATE the learn-by-doing state: the gauge checks must never
    # depend on live learn.json (real usage drifts it — the doctor
    # verifies the SPEC, not the accumulated learning). The learn store
    # is resolved through _learn_dir(); point it at a tempdir.
    orig_learn_dir = rl._learn_dir
    with tempfile.TemporaryDirectory() as _iso_td:
        rl._learn_dir = lambda profile="": Path(_iso_td) / "runtime"

        checks = []
        # 1. The 7 levels with the exact word caps.
        lvls = levels()
        caps = {l["name"]: l["words"] for l in lvls}
        checks.append({
            "name": "7 levels with word caps",
            "status": "ok" if caps == {"extremely-low": 16, "very-low": 32,
                                       "low": 64, "medium": 128, "high": 256,
                                       "very-high": 512, "extremely-high": 1024} else "fail",
            "detail": str(caps),
        })
        # 2. Gauge picks ONE level from the message.
        cases = [
            ("hi", "very-low"),
            ("what time is it", "low"),
            ("can you explain how the provider chain works in detail?", "medium"),
            ("give me a tl;dr", "low"),
            ("research and document the complete architecture of the vault database, the retrieval ladder, the compression system, the provider selection model, and every component of the prompt stack with all the edge cases and configuration options and how they interact together across the whole system", "extremely-high"),
        ]
        all_ok = all(gauge(t)["name"] == want for t, want in cases)
        checks.append({
            "name": "gauge selects one level",
            "status": "ok" if all_ok else "fail",
            "detail": "; ".join(f"{gauge(t)['label']}" for t, _w in cases[:3]),
        })
        # 3. cap_for returns the ceiling.
        checks.append({
            "name": "cap_for returns ceiling",
            "status": "ok" if cap_for("hi") == 32 and cap_for(
                "explain the entire architecture in full detail please") >= 128 else "fail",
            "detail": f"hi→{cap_for('hi')}",
        })
        # 4. The guidelines line: reasoning uncapped + content capped (up to).
        line = prompt_line()
        checks.append({
            "name": "prompt line: content capped, reasoning uncapped",
            "status": "ok" if "uncapped" in line and "up to" in line
            and "never pad" in line and "CONTENT" in line else "fail",
            "detail": line.splitlines()[1][:50],
        })
        # 5. The prompt stack keeps 5 blocks; the line is inside guidelines.
        from context.prompt_builder import build_prompt_stack
        stack = build_prompt_stack(channel="system", profile_root=None,
            history=[{"role": "user", "content": "hi"},
                     {"role": "assistant", "content": "hello"}])
        blocks = stack.split("\n\n---\n\n")
        checks.append({
            "name": "stack stays 5 blocks, line in guidelines",
            "status": "ok" if len(blocks) == 5 and "Response length" in blocks[-1] else "fail",
            "detail": f"{len(blocks)} blocks",
        })
        # 6. LEARN-BY-DOING: a short answer learns the level down.
        q = "what time is it"
        # The Operator's example: prediction 64 (Low), actual 3 → adjusted
        # 16 (Extremely Low) — a DIRECT jump to the matching level.
        rl.learn_usage(q, actual_words=3)
        adjusted = gauge(q)["words"]
        checks.append({
            "name": "learn jumps to matching level",
            "status": "ok" if adjusted == 16 else "fail",
            "detail": f"3 words → {adjusted} (want 16 / Extremely Low)",
        })
        checks.append({
            "name": "learning persists",
            "status": "ok" if rl._learn_file().exists() else "fail",
            "detail": "learn.json written",
        })

        # 7. The response-length GROUP = 3 REAL COLUMNS (not a JSON blob).
        import tempfile as _tf
        from pathlib import Path as _P
        with _tf.TemporaryDirectory() as td2:
            from core import db as db_layer
            sid = "rl-meta-test"
            db_layer.record_session_message(
                sid, "user", "what time is it", profile="",
                response_length=3, response_prediction=64,
                response_adjustment=16)
            conn = db_layer.connect_session(sid, profile="")
            cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
            has_cols = all(c in cols for c in
                           ("response_length", "response_prediction",
                            "response_adjustment"))
            row = conn.execute(
                "SELECT response_length, response_prediction, "
                "response_adjustment, meta FROM messages").fetchone()
            conn.close()
            # 1 variable = 1 column; the trio is NOT in the meta blob.
            checks.append({
                "name": "rl trio = 3 real columns",
                "status": "ok" if has_cols and row[:3] == (3, 64, 16)
                and row[3] is None else "fail",
                "detail": f"row={row[:3]} meta={row[3]!r}",
            })
            # 8. The history JSONL reads the columns via the NESTED mapping
            #    (Athena's self-training view: "response": {length, ...}).
            rows = db_layer.get_session_history(sid, limit=5, profile="")
            from context.prompt_builder import build_prompt_stack
            stack = build_prompt_stack(channel="system", profile_root=None, history=rows)
            checks.append({
                "name": "history carries the response group (nested)",
                "status": "ok" if '"response"' in stack and '"length"' in stack else "fail",
                "detail": "nested response in JSONL",
            })
            # Clean up the test session (never pollute the real store).
            p = Path(db_layer.sessions_dir("")) / f"session-{sid}.db"
            if p.exists():
                p.unlink()

        rl._learn_dir = orig_learn_dir
        return checks

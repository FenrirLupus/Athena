"""Schema-consistency test — the response-length trio matches across stores.

The vault (entries) and every session (messages) must carry the same
column-family columns: response_length, response_prediction,
response_adjustment. Opened through the REAL path (connect_session runs
the migration), so existing files converge automatically.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def run() -> list[dict]:
    from core import db as db_layer

    checks = []
    trio = {"response_length", "response_prediction", "response_adjustment"}

    # 1. The vault has the trio.
    conn = db_layer.connect_vault("")
    vault_cols = {r[1] for r in conn.execute("PRAGMA table_info(entries)")}
    conn.close()
    checks.append({
        "name": "vault has the response trio",
        "status": "ok" if trio <= vault_cols else "fail",
        "detail": f"missing={sorted(trio - vault_cols)}",
    })

    # 2. Every session file, opened through the REAL path, has the trio.
    sessions = sorted(Path(db_layer.sessions_dir("")).glob("session-*.db"))
    bad = []
    for s in sessions:
        sid = s.stem.replace("session-", "")
        try:
            c = db_layer.connect_session(sid, profile="")
            msg_cols = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
            c.close()
        except Exception:
            bad.append(sid[:8])
            continue
        if not trio <= msg_cols:
            bad.append(sid[:8])
    checks.append({
        "name": "all sessions have the trio (migrated)",
        "status": "ok" if not bad else "fail",
        "detail": f"{len(sessions) - len(bad)}/{len(sessions)} ok"
                  f"{f' bad={bad[:3]}' if bad else ''}",
    })

    # 3. The column names obey the 1-2 word rule (the Operator's naming).
    from core.db import validate_families
    bad_names = validate_families()
    checks.append({
        "name": "family columns ≤2 words",
        "status": "ok" if not bad_names else "fail",
        "detail": f"bad={bad_names}" if bad_names else "ok",
    })

    # 4. The family columns exist in BOTH stores (the Operator's bundles:
    #    name, tool_call/skill_call, reason, api — from ChatML/OpenAI/LM
    #    Studio). The VAULT dropped the parent `name` (name_first/last/
    #    nick remain); the SESSION keeps its full name column.
    vault_chat = {"name_first", "name_last", "name_nick",
                  "tool_call", "tool_id", "skill_call", "skill_id",
                  "reason", "reason_stop", "reason_start", "reason_pending",
                  "api_provider", "api_model"}
    session_chat = {"name", "name_first", "name_last", "name_nick",
                    "tool_call", "tool_id", "skill_call", "skill_id",
                    "reason_stop", "reason_start", "reason_pending",
                    "api_provider", "api_model"}
    usage_cols = {"usage_prompt", "usage_completion", "usage_total"}
    conn = db_layer.connect_vault("")
    vault_cols2 = {r[1] for r in conn.execute("PRAGMA table_info(entries)")}
    conn.close()
    checks.append({
        "name": "vault has chat-format columns",
        "status": "ok" if vault_chat <= vault_cols2 else "fail",
        "detail": f"missing={sorted(vault_chat - vault_cols2)}",
    })
    checks.append({
        "name": "vault has the usage group",
        "status": "ok" if usage_cols <= vault_cols2 else "fail",
        "detail": f"missing={sorted(usage_cols - vault_cols2)}",
    })
    # Any session file has them too (and tool_name is GONE — the swap).
    session_checked = False
    if sessions:
        sid = sessions[0].stem.replace("session-", "")
        try:
            c = db_layer.connect_session(sid, profile="")
            msg_cols = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
            c.close()
            session_checked = True
            checks.append({
                "name": "session has chat cols, tool_name swapped",
                "status": "ok" if session_chat <= msg_cols
                and usage_cols <= msg_cols
                and "tool_name" not in msg_cols else "fail",
                "detail": f"tool_call={'tool_call' in msg_cols} "
                          f"skill_call={'skill_call' in msg_cols} "
                          f"usage={usage_cols <= msg_cols}",
            })
        except Exception as exc:
            checks.append({
                "name": "session has chat cols, tool_name swapped",
                "status": "fail",
                "detail": str(exc),
            })
    if not session_checked:
        # No session files — create a temp one to prove the schema.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            import core.db as dbmod
            orig = dbmod.sessions_dir
            dbmod.sessions_dir = lambda *a, **k: Path(td)
            try:
                s2 = "schema-probe"
                db_layer.record_session_message(s2, "user", "hi", profile="",
                                                api_model="m", api_provider="p")
                c = db_layer.connect_session(s2, profile="")
                msg_cols = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
                c.close()
                checks.append({
                    "name": "fresh session has chat cols",
                    "status": "ok" if session_chat <= msg_cols
                    and usage_cols <= msg_cols
                    and "tool_name" not in msg_cols else "fail",
                    "detail": f"len={len(msg_cols)}",
                })
                p = Path(td) / f"session-{s2}.db"
                if p.exists():
                    p.unlink()
            finally:
                dbmod.sessions_dir = orig
    return checks

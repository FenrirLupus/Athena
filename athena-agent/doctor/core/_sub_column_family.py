"""Column-family doctrine test — the Operator's grouped-variable schema.

A variable group is a column-name FAMILY: the base column is the PARENT
(the primary counted value), the suffix columns are system-operation
variants. Max 3 members per family; the family is NAMING, never a blob.
"""
from __future__ import annotations


def run() -> list[dict]:
    from core.db import COLUMN_FAMILIES, column_family

    checks = []
    # 1. The response-length family exists with the parent + 2 variants.
    fam = COLUMN_FAMILIES.get("response_length")
    checks.append({
        "name": "response_length family defined",
        "status": "ok" if fam and fam.get("json_key") == "response"
        and set(fam.get("members", {})) == {
            "response_length", "response_prediction",
            "response_adjustment"} else "fail",
        "detail": str(fam),
    })
    # 1b. Families capped at 4 members (the Operator's bundles — the name
    # group has parent + first/last/nick = 4; others are ≤3).
    ok_cap = all(len(f["members"]) <= 4 for f in COLUMN_FAMILIES.values())
    checks.append({
        "name": "families capped at 4 members",
        "status": "ok" if ok_cap else "fail",
        "detail": f"{len(COLUMN_FAMILIES)} families",
    })
    # 1c. Every column name is 1-2 words (the Operator's naming rule).
    from core.db import validate_families
    bad_names = validate_families()
    checks.append({
        "name": "column names = 1-2 words",
        "status": "ok" if not bad_names else "fail",
        "detail": f"bad={bad_names}" if bad_names else "all ≤2 words",
    })
    # 3. The base IS the parent; the suffix names extend it.
    checks.append({
        "name": "base column = parent",
        "status": "ok" if fam and "response_length" in fam.get("members", {})
        else "fail",
        "detail": fam.get("json_key", "none") if fam else "none",
    })
    # 4. The family columns are REAL columns in the session schema.
    import tempfile
    from pathlib import Path
    from core import db as db_layer
    with tempfile.TemporaryDirectory() as td:
        sid = "family-test"
        db_layer.record_session_message(
            sid, "user", "test", profile="",
            response_length=3, response_prediction=64,
            response_adjustment=16)
        conn = db_layer.connect_session(sid, profile="")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        conn.close()
        members = set(fam["members"]) if isinstance(fam, dict) else set()
        present = all(c in cols for c in members)
        checks.append({
            "name": "family = 3 real columns",
            "status": "ok" if present else "fail",
            "detail": f"present={present}",
        })
        # cleanup
        try:
            from core.db import sessions_dir
            import os
            p = sessions_dir("") / f"session-{sid}.db"
            if p.exists():
                os.remove(p)
        except Exception:
            pass
    # 5. The SQL↔JSON mapping: flat → nested on export, nested → flat on
    #    import (the Operator's transport mapping).
    from core.db import row_to_json, json_to_row
    row = {"role": "user", "content": "hi",
           "response_length": 3, "response_prediction": 64,
           "response_adjustment": 16,
           "name_first": "Athena", "name_last": "Bella", "name_nick": "A",
           "tool_call": "call_1", "tool_id": "t1",
           "reason": "thinking chain", "reason_stop": "stop",
           "api_provider": "opencode-go", "api_model": "deepseek-v4-flash",
           "usage_prompt": 13, "usage_completion": 18, "usage_total": 31}
    j = row_to_json(row)
    checks.append({
        "name": "export maps flat → nested groups",
        "status": "ok" if j.get("response") == {"length": 3, "prediction": 64,
                                                "adjusted": 16}
        and j.get("name") == {"first": "Athena", "last": "Bella",
                              "nick": "A"}
        and j.get("tool_call") == {"call": "call_1", "id": "t1"}
        and j.get("reason") == {"chain": "thinking chain", "stop": "stop"}
        and j.get("api") == {"provider": "opencode-go",
                             "model": "deepseek-v4-flash"}
        and j.get("usage") == {"prompt": 13, "completion": 18, "total": 31}
        and "response_length" not in j and "name_first" not in j
        and "tool_id" not in j and "usage_prompt" not in j else "fail",
        "detail": str(j.get("api")),
    })
    back = json_to_row(j)
    checks.append({
        "name": "import maps nested → flat (round-trip)",
        "status": "ok" if back.get("response_length") == 3
        and back.get("response_prediction") == 64
        and back.get("response_adjustment") == 16
        and back.get("name_first") == "Athena"
        and back.get("name_last") == "Bella"
        and back.get("tool_call") == "call_1"
        and back.get("tool_id") == "t1"
        and back.get("reason") == "thinking chain"
        and back.get("reason_stop") == "stop"
        and back.get("api_provider") == "opencode-go"
        and back.get("api_model") == "deepseek-v4-flash"
        and back.get("usage_prompt") == 13
        and back.get("usage_completion") == 18
        and back.get("usage_total") == 31
        and "response" not in back else "fail",
        "detail": str({k: back.get(k) for k in
                       ("response_length", "name_first", "tool_id",
                        "reason", "api_model", "usage_total")}),
    })
    return checks

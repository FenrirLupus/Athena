"""Nurse session + workflow test — the Operator's spec.

The nurse has her OWN session .db (profiles/.nurse/sessions/): the
Doctor's messages land on the System/User side ("hey look, there are
issues"), her replies on the Assistant side — never the caller's profile
session. Her system prompt is the modified Programmer's Workflow
(Diagnose → Plan → Build → Execute → Verify → Summarize) and she works
from a checklist.
"""
from __future__ import annotations


def run() -> list[dict]:
    import tempfile
    from pathlib import Path
    from doctor.nurse import (
        nurse_session_id, nurse_talk, nurse_recent, nurse_checklist,
        NURSE_WORKFLOW, NURSE_PROFILE)
    from core import db as db_layer
    import core.db as dbmod

    checks = []

    # 1. The workflow prompt has all six steps, in order.
    steps = ["DIAGNOSE", "PLAN", "BUILD", "EXECUTE", "VERIFY", "SUMMARIZE"]
    checks.append({
        "name": "nurse workflow: Diagnose→Plan→Build→Execute→Verify→Summarize",
        "status": "ok" if all(s in NURSE_WORKFLOW for s in steps) else "fail",
        "detail": "6-step modified programmer workflow",
    })

    # 2. The checklist is the six steps.
    checks.append({
        "name": "nurse checklist (6 steps)",
        "status": "ok" if nurse_checklist() == [
            "Diagnose", "Plan", "Build", "Execute", "Verify", "Summarize"]
        else "fail",
        "detail": str(nurse_checklist()),
    })

    # 3. Her messages persist to HER OWN session .db (the .nurse profile).
    orig = dbmod.sessions_dir
    try:
        with tempfile.TemporaryDirectory() as td:
            # Redirect the sessions dir so the test never touches the
            # real profiles/ tree — the routing itself is what we test.
            # Accepts the (profile, kind) signature of the 08-12 split.
            dbmod.sessions_dir = staticmethod(lambda *a, **k: Path(td))
            sid = nurse_session_id()
            nurse_talk("[doctor] hey look, there are issues", side="user")
            nurse_talk("[nurse] diagnosed and repaired", side="assistant")
            recent = nurse_recent(10)
            roles = [r["role"] for r in recent]
            names = [r.get("name_nick") for r in recent]
            checks.append({
                "name": "nurse session: doctor user-side, nurse assistant-side",
                "status": "ok" if "user" in roles and "assistant" in roles
                and "Doctor" in names and "Nurse" in names else "fail",
                "detail": f"roles={roles} names={names}",
            })
            # The session file exists (her own .db) — the STRICT name:
            # session-{UUID}.db (the Operator's 08-12 rule — never a
            # nurse- prefix, never a short hex).
            import re as _re
            import uuid as _uuid_mod
            _uuid = _re.compile(
                r"^session-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}\.db$", _re.I)
            files = [f for f in Path(td).glob("session-*.db")
                     if _uuid.match(f.name)]
            checks.append({
                "name": "nurse has her own session .db (strict UUID name)",
                "status": "ok" if files else "fail",
                "detail": f"{len(files)} session-UUID.db file(s)",
            })
    finally:
        dbmod.sessions_dir = orig

    # 4. The profile constant is the dot-prefixed SYSTEM profile.
    checks.append({
        "name": "nurse profile is .nurse (system-based)",
        "status": "ok" if NURSE_PROFILE == ".nurse" else "fail",
        "detail": NURSE_PROFILE,
    })
    return checks

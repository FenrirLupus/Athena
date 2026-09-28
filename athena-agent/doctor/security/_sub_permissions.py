"""Interactive permissions test — the Operator's Allow/Deny/Block gate.

THE 08-15 MODEL: the 4-channel permissions.yaml store (operator/agent/
system name lists + the global channel's flags). Default = allow ONCE
(prompt every time); an allow at session/global POPULATES the name into
the channel list; the session_id flows through so session allows apply.
"""
from __future__ import annotations
from core.config import ATHENA_ROOT


def run() -> list[dict]:
    import tempfile
    from pathlib import Path
    import security.permissions as perm

    checks = []
    with tempfile.TemporaryDirectory() as td:
        orig = perm._rules_path
        perm._rules_path = staticmethod(lambda profile="": Path(td) / "permissions.yaml")
        try:
            # 1. Risk classification.
            cases = [
                ("read", {}, "safe"),
                ("terminal", {"cmd": "ls"}, "unsafe"),
                ("terminal", {"cmd": "rm -rf /"}, "unsafe"),
                ("kill", {}, "blocked"),
            ]
            ok = all(perm.classify(t, a) == want for t, a, want in cases)
            checks.append({
                "name": "risk classification (safe/unsafe/blocked)",
                "status": "ok" if ok else "fail",
                "detail": "; ".join(f"{t}:{perm.classify(t, a)}" for t, a, _ in cases),
            })
            # 2. Gate decisions.
            checks.append({
                "name": "gate: safe allow, blocked block",
                "status": "ok" if perm.check("read")["verdict"] == perm.ALLOW
                and perm.check("kill")["verdict"] == perm.BLOCK else "fail",
                "detail": f"read={perm.check('read')['verdict']} kill={perm.check('kill')['verdict']}",
            })
            # An OUT-OF-BOUNDS unsafe call with no rule → needs_prompt.
            r = perm.check("terminal", {"command": "ls /outside"})
            checks.append({
                "name": "gate: unknown unsafe out-of-bounds → needs_prompt",
                "status": "ok" if r["verdict"] == perm.NEEDS_PROMPT
                and r["needs_prompt"] else "fail",
                "detail": str(r),
            })
            # An IN-BOUNDS unsafe call is approved (the platform's own work).
            r2 = perm.check("terminal",
                            {"command": "ls " + str(ATHENA_ROOT / "workflows")})
            checks.append({
                "name": "gate: in-bounds unsafe auto-approved",
                "status": "ok" if r2["verdict"] == perm.ALLOW else "fail",
                "detail": str(r2),
            })
            # 3. ONCE grants without persisting.
            perm.decide("terminal", "allow", "once")
            store1 = perm._load_rules("")
            checks.append({
                "name": "ONCE grants, does not persist",
                "status": "ok" if store1["operator_channel"]["tools"] == []
                else "fail",
                "detail": str(store1["operator_channel"]),
            })
            # 4. GLOBAL persists + populates the operator list.
            perm.decide("terminal", "allow", "global")
            store2 = perm._load_rules("")
            checks.append({
                "name": "GLOBAL persists + populates the operator list",
                "status": "ok" if perm.check("terminal", {"command": "ls /outside"},
                                            session_id="")["verdict"] == perm.ALLOW
                and "terminal" in store2["operator_channel"]["tools"] else "fail",
                "detail": str(store2["operator_channel"]),
            })
            # 5. SESSION is memory-scoped + applies with the session id.
            perm.decide("write", "allow", "session", session_id="s1")
            checks.append({
                "name": "SESSION scoped to one session",
                "status": "ok" if perm.check("write", {"path": "/outside"},
                                            session_id="s1")["verdict"] == perm.ALLOW
                and perm.check("write", {"path": "/outside"},
                               session_id="s2")["verdict"] == perm.NEEDS_PROMPT
                else "fail",
                "detail": f"s1={perm.check('write', {'path': '/outside'}, session_id='s1')['verdict']} "
                          f"s2={perm.check('write', {'path': '/outside'}, session_id='s2')['verdict']}",
            })
            # 6. Invalid scope refused.
            checks.append({
                "name": "invalid scope refused",
                "status": "ok" if not perm.decide("write", "allow", "forever") else "fail",
                "detail": "forever → False",
            })
            # 7. The global channel flags (NULL default — set them).
            perm.set_global_flags("", "tools", "allow", "global")
            checks.append({
                "name": "global channel flags apply (allow@global)",
                "status": "ok" if perm.check("terminal", {"command": "ls /outside"},
                                            session_id="")["verdict"] == perm.ALLOW
                else "fail",
                "detail": str(perm._load_rules("")["global_channel"]),
            })
            # 8. NULL default is skipped (the CEO's 08-15 order).
            # The step-4 GLOBAL allow populated the operator list — clear
            # BOTH the list and the flags so the fall-through is clean.
            perm.set_channel_entry("", "operator_channel", "tools",
                                   "terminal", False)
            perm.set_global_flags("", "tools", "", "")
            checks.append({
                "name": "global channel null → skipped (falls through)",
                "status": "ok" if perm.check("terminal", {"command": "ls /outside"},
                                            session_id="")["verdict"]
                == perm.NEEDS_PROMPT else "fail",
                "detail": str(perm._load_rules("")["global_channel"]),
            })
        finally:
            perm._rules_path = orig
    return checks

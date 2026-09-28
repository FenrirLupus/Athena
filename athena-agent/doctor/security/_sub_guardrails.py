"""Guardrail System test — the Operator's safety spec.

Guardrails are the pre-execution safety layer for Plugins, Tools, and
Skills, working WITH the security/permissions gate:
  • permissions = WHO is allowed (Allow/Deny/Block)
  • guardrails  = WHAT a call may do (intent validation: capabilities,
    argument patterns, sensitive targets)

Verdicts: pass / hold / reject. Rejections refuse outright; holds need
the interactive surface; passes go to the permission gate.
"""
from __future__ import annotations


def run() -> list[dict]:
    from security.guardrails import (check, declare, registry_status,
                                     PASS, HOLD, REJECT)

    checks = []

    # 1. Hard rejections: destructive/credential-exfil patterns.
    rej = check("tool", "terminal", {"command": "rm -rf /"})
    rej2 = check("tool", "terminal", {"command": "cat ~/.env"})
    rej3 = check("tool", "terminal", {"command": "mkfs /dev/sda1"})
    checks.append({
        "name": "guardrails reject destructive/credential calls",
        "status": "ok" if rej["verdict"] == REJECT
        and rej2["verdict"] == REJECT and rej3["verdict"] == REJECT
        else "fail",
        "detail": f"rm={rej['verdict']} env={rej2['verdict']} "
                  f"mkfs={rej3['verdict']}",
    })

    # 2. Sensitive-target HOLD: /etc paths need the interactive surface.
    hold = check("tool", "read", {"path": "/etc/passwd"})
    p = check("tool", "read", {"path": "/tmp/x.txt"})
    checks.append({
        "name": "guardrails hold sensitive paths, pass benign",
        "status": "ok" if hold["verdict"] == HOLD and p["verdict"] == PASS
        else "fail",
        "detail": f"hold={hold['verdict']} benign={p['verdict']}",
    })

    # 3. Safe exec passes (the guardrail's intent check).
    safe = check("tool", "execute", {"command": "ls -la"})
    checks.append({
        "name": "guardrails pass safe exec arguments",
        "status": "ok" if safe["verdict"] == PASS else "fail",
        "detail": f"exec={safe['verdict']}",
    })

    # 4. Skills + plugins declare their scope on load/activate.
    declare("test-plugin", capabilities=["read", "write"], description="t")
    status = registry_status()
    checks.append({
        "name": "plugins/skills declare guardrail scope",
        "status": "ok" if "test-plugin" in status
        and status["test-plugin"].get("capabilities") == ["read", "write"]
        else "fail",
        "detail": f"registered={sorted(status.keys())[:5]}",
    })

    # 5. The guardrail is wired into the tool gate (message_loop imports it).
    import core.message_loop as ml
    src = open(ml.__file__, encoding="utf-8").read()
    checks.append({
        "name": "guardrails wired into the tool gate",
        "status": "ok" if "security.guardrails" in src
        and "guard_check" in src else "fail",
        "detail": "message_loop validates intent before execution",
    })
    return checks

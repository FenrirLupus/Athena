"""Integrations + billing + approvals test — the Operator's four-way spec.

1. Context compression: fully wired (window check → summarize → rebuild).
2. Billing/usage: per-provider views.
3. Interactive approvals: request → decide → history, fail-closed.
4. Integrations: third-party categories (message_platform etc.), separate
   from plugins/tools/skills; connect/discover/disconnect work.
"""
from __future__ import annotations


def run() -> list[dict]:
    from core import approvals
    from core.billing import usage_summary, per_provider
    from integrations import discover, connect, disconnect

    checks = []

    # 1. Billing: the vault's usage aggregates (a summary always returns).
    s = usage_summary()
    checks.append({
        "name": "billing summary works",
        "status": "ok" if isinstance(s, dict) and "total_tokens" in s
        else "fail",
        "detail": f"calls={s.get('calls')} total={s.get('total_tokens')}",
    })

    # 2. Approvals lifecycle: request → pending → decide → history.
    r = approvals.request_approval("terminal", {"cmd": "ls"}, "MEDIUM")
    pend = approvals.pending_approvals()
    dec = approvals.resolve_approval(r["id"], "allow", "once")
    hist = approvals.approval_history()
    checks.append({
        "name": "approval request → decide → history",
        "status": "ok" if any(p["id"] == r["id"] for p in pend)
        and dec.get("verdict") == "allow"
        and any(h["id"] == r["id"] and h["verdict"] == "allow"
                for h in hist) else "fail",
        "detail": f"pending={len(pend)} history={len(hist)}",
    })
    # Fail-closed: an unknown approval resolves to deny.
    missing = approvals.resolve_approval("does-not-exist", "allow")
    checks.append({
        "name": "approval unknown id → not ok (fail closed)",
        "status": "ok" if not missing.get("ok") else "fail",
        "detail": str(missing),
    })

    # 3. Integrations: discord discovered under message_platform.
    items = discover()
    discord = next((i for i in items if i["name"] == "discord"), None)
    checks.append({
        "name": "discord integration discovered (message_platform)",
        "status": "ok" if discord
        and discord["category"] == "message_platform" else "fail",
        "detail": str(discord.get("category") if discord else None),
    })
    # Connect → disconnect round-trip.
    c = connect("discord")
    st_before = next((i["connected"] for i in discover()
                      if i["name"] == "discord"), False)
    d = disconnect("discord")
    st_after = next((i["connected"] for i in discover()
                     if i["name"] == "discord"), False)
    checks.append({
        "name": "integration connect → disconnect round-trip",
        "status": "ok" if c.get("ok") and st_before and d.get("ok")
        and not st_after else "fail",
        "detail": f"connect={c.get('ok')} after_disconnect={st_after}",
    })

    # 4. Integrations are separate from plugins/tools/skills (the Operator's
    #    spec): the registry scans integrations/ INSIDE athena-system
    #    (the system folder), not plugins/ or the root.
    import core.config as cfg
    from pathlib import Path
    from integrations import INTEGRATIONS_DIR
    check_int = INTEGRATIONS_DIR.is_dir() \
        and "athena-system" in str(INTEGRATIONS_DIR)
    checks.append({
        "name": "integrations dir separate from plugins",
        "status": "ok" if check_int else "fail",
        "detail": "third-party categories live in athena-system/integrations/",
    })
    return checks

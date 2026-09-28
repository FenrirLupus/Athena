"""Resource + browser + web toolset + subagent lifecycle test.

the Operator's spec:
1. Resource Manager (memory_manager → resource_manager) with a Monitor.
2. Browser tool: hands-off default-app open OR silent terminal fetch.
3. Web Toolset: the web category (browser/search/extract).
4. Subagent FULL lifecycle: spawn → queued → running → done/failed,
   plus health (stale detection), reap, and cleanup.
"""
from __future__ import annotations


def run() -> list[dict]:
    import tempfile
    from pathlib import Path
    from core.resource_manager import sample, status
    from core.browser import browser_open
    from web.toolset import register as register_web
    from autonomy import kanban as kb

    checks = []

    # 1. Resource Manager samples all four resources + advisory status.
    snap = sample()
    checks.append({
        "name": "resource manager samples memory/disk/context/subagents",
        "status": "ok" if {"memory", "disk", "context", "subagents"}
        <= set(snap.keys()) else "fail",
        "detail": f"disk={snap['disk'].get('percent')}% "
                  f"ctx={snap['context'].get('utilization')}",
    })
    st = status()
    checks.append({
        "name": "resource monitor reports health + issues",
        "status": "ok" if "healthy" in st and "issues" in st
        and "thresholds" in st else "fail",
        "detail": f"healthy={st['healthy']}",
    })

    # 2. Browser: the silent fetch works (terminal fallback, no window).
    r = browser_open("https://example.com", visible=False)
    checks.append({
        "name": "browser silent fetch works (no window)",
        "status": "ok" if r.get("ok") and r.get("mode") == "silent"
        and (r.get("text") or r.get("detail")) else "fail",
        "detail": f"via={r.get('via')} ok={r.get('ok')}",
    })
    # 2b. The browser is Linux/Windows only (the Operator's spec): no darwin.
    import core.browser as browser_mod
    src = open(browser_mod.__file__, encoding="utf-8").read()
    checks.append({
        "name": "browser supports Linux+Windows only (no macOS)",
        "status": "ok" if "darwin" not in src
        and "xdg-open" in src and "start" in src else "fail",
        "detail": "Linux (xdg-open) + Windows (start); no darwin branch",
    })

    # 3. Web Toolset registers the three web tools.
    from filesystem.tools import TOOLS
    register_web()
    names = [n for n in ("browser_open", "web_search", "web_extract")
             if n in TOOLS]
    checks.append({
        "name": "web toolset registers browser/search/extract",
        "status": "ok" if len(names) == 3 else "fail",
        "detail": str(names),
    })

    # 4. Subagent lifecycle: the full spawn→run→complete + health/reap/
    #    cleanup in a temp DB (patch BOARDS_ROOT so board_path resolves
    #    every board inside the tempdir — never the real profile dirs).
    import autonomy.kanban as kbmod
    orig_boards = kbmod.BOARDS_ROOT
    with tempfile.TemporaryDirectory() as td:
        kbmod.BOARDS_ROOT = Path(td)
        sub = kb.spawn_subagent("probe", "lifecycle", "do")
        nxt = kb.next_subagent()
        kb.complete_subagent(sub["id"], "ok")
        health = kb.subagent_health()
        reaped = kb.reap_stale()
        removed = kb.cleanup_done(keep=0)
        checks.append({
            "name": "subagent full lifecycle (spawn→run→complete→cleanup)",
            "status": "ok" if sub["status"] == "queued"
            and nxt and nxt["id"] == sub["id"]
            and health["by_status"].get("done", 0) >= 1
            and removed >= 1 else "fail",
            "detail": f"by_status={health['by_status']} removed={removed}",
        })
    kbmod.BOARDS_ROOT = orig_boards
    return checks

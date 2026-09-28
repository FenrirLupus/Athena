"""Filesystem toolset test — the 25 platform wrappers (command-assembled)."""
from __future__ import annotations


def run() -> list[dict]:
    from filesystem.tools import TOOLS
    from filesystem.safety import ATHENA_ROOT
    from core.config import DEFAULT_PROFILE_ROOT
    import tempfile
    from pathlib import Path

    checks = []
    expected = {"read", "write", "append", "replace", "patch", "delete", "copy",
                "move", "rename", "list", "tree", "find", "search", "mkdir",
                "exists", "stat", "hash", "execute", "terminal", "process",
                "kill", "download", "upload", "compress", "extract"}
    registered = set(TOOLS.keys())
    missing = expected - registered
    checks.append({
        "name": "all 25 wrappers registered",
        "status": "ok" if not missing else "fail",
        "detail": f"{len(registered)} tools; missing={sorted(missing)[:4]}" if missing else f"{len(registered)} tools",
    })

    with tempfile.TemporaryDirectory(dir=DEFAULT_PROFILE_ROOT / "workspace") as td:
        p = Path(td) / "demo.txt"
        TOOLS["write"].run({"path": str(p), "content": "alpha beta\nalpha two"})
        checks.append({
            "name": "write creates",
            "status": "ok" if p.exists() and "alpha" in p.read_text() else "fail",
            "detail": "",
        })
        TOOLS["append"].run({"path": str(p), "content": " MORE"})
        checks.append({
            "name": "append adds",
            "status": "ok" if "MORE" in p.read_text() else "fail",
            "detail": "",
        })
        r = TOOLS["read"].run({"path": str(p)})
        checks.append({
            "name": "read returns content",
            "status": "ok" if "alpha" in r else "fail",
            "detail": r[:40],
        })
        TOOLS["replace"].run({"path": str(p), "old": "alpha", "new": "OMEGA",
                              "replace_all": True})
        checks.append({
            "name": "replace rewrites",
            "status": "ok" if "OMEGA" in p.read_text() and "alpha" not in p.read_text() else "fail",
            "detail": p.read_text()[:40],
        })
        r = TOOLS["search"].run({"pattern": "OMEGA", "path": td})
        checks.append({
            "name": "search finds",
            "status": "ok" if "OMEGA" in r else "fail",
            "detail": r[:40],
        })
        r = TOOLS["stat"].run({"path": str(p)})
        checks.append({
            "name": "stat reports",
            "status": "ok" if "demo.txt" in r or "Size" in r else "fail",
            "detail": r[:40],
        })
        r = TOOLS["list"].run({"path": td})
        checks.append({
            "name": "list shows",
            "status": "ok" if "demo.txt" in r else "fail",
            "detail": r[:40],
        })
        TOOLS["mkdir"].run({"path": str(Path(td) / "sub" / "a")})
        checks.append({
            "name": "mkdir creates",
            "status": "ok" if (Path(td) / "sub" / "a").is_dir() else "fail",
            "detail": "",
        })
        r = TOOLS["exists"].run({"path": str(p)})
        checks.append({
            "name": "exists true",
            "status": "ok" if "true" in r.lower() else "fail",
            "detail": r,
        })
        r = TOOLS["hash"].run({"path": str(p)})
        checks.append({
            "name": "hash computes",
            "status": "ok" if len(r) > 10 else "fail",
            "detail": r[:30],
        })
        # Scope: outside write refused (returns error string, never crashes).
        r = TOOLS["write"].run({"path": "/tmp/forbidden.txt", "content": "x"})
        checks.append({
            "name": "outside write refused",
            "status": "ok" if "error" in r.lower() else "fail",
            "detail": r[:60],
        })
        TOOLS["delete"].run({"path": str(p)})
        checks.append({
            "name": "delete cleans up",
            "status": "ok" if not p.exists() else "fail",
            "detail": "",
        })
    return checks

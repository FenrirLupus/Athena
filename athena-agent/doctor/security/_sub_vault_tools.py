"""Vault tools test — registered, channel-gated, functional.

Uses a FRESH temp vault (never the real profile dirs) so repeated test
runs can't pollute real stores, and the round-trip is deterministic.
"""
from __future__ import annotations


def run() -> list[dict]:
    import tempfile
    import uuid
    from pathlib import Path
    from filesystem.tools import TOOLS
    from core.channels import load_channels

    checks = []
    names = ["vault_query", "vault_semantic", "vault_store"]
    for n in names:
        checks.append({
            "name": f"{n} registered",
            "status": "ok" if n in TOOLS else "fail",
            "detail": "",
        })
    user = load_channels({})["user"]
    allowed = all(user.allows_tool(n) for n in names)
    checks.append({
        "name": "user channel allows vault tools",
        "status": "ok" if allowed else "fail",
        "detail": f"allowed={[n for n in names if user.allows_tool(n)]}",
    })

    # Isolate: the test's vault lives in a temp dir (never the real store).
    from core import db as db_layer
    import core.db as dbmod
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        orig_vault = db_layer.vault_path
        db_layer.vault_path = staticmethod(
            lambda *a, **k: td_path / "vault.db")
        try:
            r1 = TOOLS["vault_store"].run(
                {"content": "test-fact-alpha-xyz", "profile": ""})
            checks.append({
                "name": "vault_store works",
                "status": "ok" if r1.startswith("stored") else "fail",
                "detail": r1[:50],
            })
            r2 = TOOLS["vault_query"].run(
                {"query": "test-fact-alpha-xyz", "profile": ""})
            checks.append({
                "name": "vault_query finds stored",
                "status": "ok" if "test-fact-alpha-xyz" in r2 else "fail",
                "detail": r2[:60],
            })
            r3 = TOOLS["vault_query"].run(
                {"query": "zzz-no-such-thing-999", "profile": ""})
            checks.append({
                "name": "vault_query no-match graceful",
                "status": "ok" if "zzz-no-such-thing-999" not in r3 else "fail",
                "detail": r3[:60],
            })
        finally:
            db_layer.vault_path = orig_vault
    return checks

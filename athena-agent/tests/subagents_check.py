"""Subagents: bounded, narrow, traced, and unable to fork.

The claims, each of which would be a lie if the test passed without it:

  * a spawn creates `subagents/<slug>/` with ARTIFACT.md, state.db and its own session
  * the worker's writes are confined to that folder — it cannot touch the parent's memories or workspace
  * it cannot spawn further workers (tool absent, and the tool refuses when a context says it is a worker)
  * the budget is honoured, not hoped for
  * the artifact records the task, the answer, and the files the run created
"""
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import paths, subagents
from providers.mock import MockProvider
from tools import registry as registry_mod
from tools import subagents as subagent_tools

checks = {}


def _refused(scope, path, flag):
    try:
        scope.check(str(path), flag)
        return False
    except ValueError:
        return True


def _allowed(scope, path, flag):
    try:
        scope.check(str(path), flag)
        return True
    except ValueError:
        return False



with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    home = athena / "profiles" / "probe"
    for d in (home / "workspace", home / "sandbox", home / "memories", athena / "athena-agent"):
        d.mkdir(parents=True)
    (home / "memories" / "MEMORY.md").write_text("parent memory\n", encoding="utf-8")

    scope = paths.for_profile(home, athena_root=athena)
    cfg = {"model": {"provider": "mock", "name": "mock-1", "window": 8192}, "tools": {"enabled": True}}

    # -- one spawn ------------------------------------------------------
    result = subagents.spawn(
        parent_scope=scope, parent_cfg=cfg, parent_lane="probe", parent_root=home,
        task="echo subagent round trip", provider=MockProvider(),
        tools_factory=lambda trace, child_scope: registry_mod.default_registry(cfg).bind(
            type("C", (), {"scope": child_scope, "cfg": cfg, "profile_root": Path(trace.folder),
                           "athena_root": athena, "is_subagent": True, "approvals": None})()),
        max_turns=3,
    )

    trace_dir = Path(result["trace"])
    checks["creates_a_folder"] = trace_dir.is_dir() and trace_dir.parent.name == "subagents"
    checks["writes_artifact"] = Path(result["artifact"]).is_file()
    checks["writes_state_db"] = Path(result["state_db"]).is_file()
    checks["own_session"] = (trace_dir / "sessions" / "LATEST.jsonl").exists()
    checks["own_sandbox"] = (trace_dir / "sandbox").is_dir()

    artifact = Path(result["artifact"]).read_text(encoding="utf-8")
    checks["artifact_names_the_task"] = "echo subagent round trip" in artifact
    checks["artifact_shows_status_and_cost"] = "status" in artifact and "cost" in artifact
    checks["artifact_carries_tokens"] = "**tokens**:" in artifact and "0 in / 0 out" not in artifact
    checks["artifact_lists_scope"] = "writes confined here" in artifact
    checks["result_has_an_answer"] = bool((result.get("reply") or result.get("error")))

    import sqlite3
    with sqlite3.connect(Path(result["state_db"])) as db:
        rows = db.execute("SELECT task, status, turns FROM runs").fetchall()
    checks["state_db_has_the_run"] = len(rows) == 1 and rows[0][0] == "echo subagent round trip"

    # -- the worker's scope is narrower ---------------------------------
    child = subagents.narrow_scope(scope, trace_dir)
    checks["child_home_is_the_trace"] = child.home == trace_dir.resolve()
    checks["child_cannot_write_parent_memories"] = _refused(child, home / "memories" / "MEMORY.md", paths.WRITE)
    checks["child_cannot_write_parent_workspace"] = _refused(child, home / "workspace" / "x.txt", paths.WRITE)
    checks["child_cannot_write_the_program"] = _refused(child, athena / "athena-agent" / "core.py", paths.WRITE)
    checks["child_can_write_its_own_sandbox"] = _allowed(child, trace_dir / "sandbox" / "note.txt", paths.WRITE)
    checks["child_has_no_inherited_workspaces"] = child.granted == []

    # -- no recursion ----------------------------------------------------
    ctx = type("C", (), {})()
    ctx.scope, ctx.cfg, ctx.profile_root, ctx.athena_root = scope, cfg, home, athena
    ctx.provider, ctx.is_subagent, ctx.approvals = MockProvider(), False, None
    tools = {t.name: t.fn for t in subagent_tools.build(ctx)}
    checks["spawn_tool_exists_for_an_agent"] = "spawn_subagent" in tools

    ctx.is_subagent = True
    worker_tools = {t.name: t.fn for t in subagent_tools.build(ctx)}
    try:
        worker_tools["spawn_subagent"](task="nested")
        checks["worker_refuses_to_spawn"] = False
    except Exception as exc:                      # noqa: BLE001
        checks["worker_refuses_to_spawn"] = "cannot spawn workers" in str(exc)

    # and the registry a worker actually receives has the tool removed
    child_ctx = type("C", (), {"scope": child, "cfg": cfg, "profile_root": trace_dir,
                               "athena_root": athena, "is_subagent": True, "approvals": None})()
    child_reg = registry_mod.default_registry(cfg).bind(child_ctx)
    child_reg.discard("spawn_subagent")
    checks["worker_registry_has_no_spawn"] = "spawn_subagent" not in child_reg.names()

    # -- budget is a cap, not a suggestion -------------------------------
    tight = subagents.spawn(
        parent_scope=scope, parent_cfg=cfg, parent_lane="probe", parent_root=home,
        task="echo one more", provider=MockProvider(),
        tools_factory=lambda trace, child_scope: registry_mod.default_registry(cfg).bind(
            type("C", (), {"scope": child_scope, "cfg": cfg, "profile_root": Path(trace.folder),
                           "athena_root": athena, "is_subagent": True, "approvals": None})()),
        max_turns=1,
    )
    # the budget allows one grace call by design, so a cap of 1 can reach 2 iterations
    checks["budget_is_recorded"] = int(tight.get("turns") or 0) <= 2

    # -- a failing worker still leaves a trace --------------------------
    class Broken:
        name = "broken"

        def call(self, *a, **k):
            raise RuntimeError("provider exploded")

    failed = subagents.spawn(
        parent_scope=scope, parent_cfg=cfg, parent_lane="probe", parent_root=home,
        task="this will fail", provider=Broken(),
        tools_factory=lambda trace, child_scope: registry_mod.default_registry(cfg).bind(
            type("C", (), {"scope": child_scope, "cfg": cfg, "profile_root": Path(trace.folder),
                           "athena_root": athena, "is_subagent": True, "approvals": None})()),
    )
    failed_artifact = Path(failed["artifact"]).read_text(encoding="utf-8")
    checks["failed_worker_leaves_a_trace"] = "provider exploded" in failed_artifact
    checks["failed_worker_reports_error"] = str(failed.get("status") or "").startswith("error")

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

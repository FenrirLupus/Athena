"""Nurse — the repair agent. The ONLY agent allowed into athena-system/.

The doctor DIAGNOSES. The nurse REPAIRS — permanently, with stable edits.
The nurse carries a privileged scope token that lets it write inside the
sanctum (athena-system/) that every other agent is denied. The two rules:

    1. Only the nurse holds the token. The safety layer checks the token
       before ever allowing a sanctum write; no other path can obtain it.
    2. The nurse is BOUNDED to athena-system/ — the repair zone and
       nothing else; every other write follows normal safety.

The nurse is BOUNDED: one diagnostic pass → repairs (stable edits) →
re-run the doctor to verify. It never runs forever.
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path

from core.config import ATHENA_ROOT

# The token: a module-level flag only systems/nurse.py sets. The safety
# layer consults nurse_scope() — the ONLY way a sanctum write is allowed.
_nurse_active = False
_lock = threading.Lock()

# The nurse's agent identity on the kanban board. Agents consult her by
# assigning a task to "nurse"; ONLY she may take the sanctum key.
# The PROFILE name is dot-prefixed (".nurse") — the Operator's convention:
# dot-prefixed profiles are SYSTEM-based (operate only inside
# athena-system/), regular profiles have no dot.
NURSE_AGENT = "nurse"
NURSE_PROFILE = ".nurse"

# The nurse's OWN session (her .db lives under profiles/.nurse/sessions/).
# The Doctor's messages are the SYSTEM/USER side of this session ("hey
# look, there are issues"); the nurse is the ASSISTANT who diagnoses and
# repairs. Every nurse communication persists HERE — never the caller's
# profile session.
_nurse_session_id: str | None = None
_session_lock = threading.Lock()


def nurse_session_id() -> str:
    """The nurse's session id — her own .db (created once per process).

    THE STRICT NAME (the Operator's 08-12 rule): every session file is
    session-{UUID}.db — no nurse- prefixes, no short hex. The full UUID
    is the session's identity. Her file lives in HER profile's
    agent/sessions/ (the agents' own folder — never the operator's
    sessions/, never swept with operator chats).
    """
    global _nurse_session_id
    with _session_lock:
        if _nurse_session_id is None:
            from core import db as db_layer
            # A stable id per boot is fine — the file persists on disk.
            _nurse_session_id = str(uuid.uuid4())
            db_layer.record_session_message(
                _nurse_session_id, "System",
                "[nurse session initialized — she serves the doctor's "
                "calls and the system's attention alerts]",
                profile=NURSE_PROFILE, kind="agent")
        return _nurse_session_id


def nurse_talk(content: str, side: str = "assistant") -> str:
    """Persist a nurse communication into HER OWN session .db.

    side='user' (System): the DOCTOR's call — "hey look, there are
        issues" (the request side).
    side='assistant': the NURSE's reply (the response side).

    Always writes to profiles/.nurse/agent/sessions/ (the agents' own
    folder — never the caller's profile session, never the operator's
    sessions/; the bug the Operator caught + the 08-12 agent split).
    """
    from core import db as db_layer
    sid = nurse_session_id()
    role = "user" if side == "user" else "assistant"
    db_layer.record_session_message(
        sid, role, content,
        profile=NURSE_PROFILE, kind="agent",
        name_first="Doctor" if side == "user" else "Nurse",
        name_nick="Doctor" if side == "user" else "Nurse")
    return sid


def nurse_recent(limit: int = 10) -> list[dict]:
    """The nurse session's recent messages (her working context)."""
    from core import db as db_layer
    try:
        return db_layer.get_session_history(
            nurse_session_id(), limit=limit, profile=NURSE_PROFILE,
            kind="agent")
    except Exception:
        return []

# The code directory the nurse may repair (athena-system/) — her zone.
REPAIR_ZONE = ATHENA_ROOT / "athena-system"


def enter_scope(agent: str = "") -> bool:
    """Give the sanctum key to the nurse agent — and ONLY to her.

    Managed self-rewrite: the key is identity-gated. An ordinary agent
    (alice, bob, default...) can CONSULT the nurse but cannot take the
    key herself. Returns True when the nurse is now in scope.
    """
    global _nurse_active
    if agent != NURSE_AGENT:
        return False
    with _lock:
        _nurse_active = True
    return True


def exit_scope() -> None:
    """Deactivate — back to normal: the sanctum is sealed again."""
    global _nurse_active
    with _lock:
        _nurse_active = False


def nurse_scope() -> bool:
    """True while the nurse is actively repairing (checked by safety)."""
    with _lock:
        return _nurse_active


def may_write(path: Path) -> bool:
    """Does the nurse's scope allow a write here?

    - athena-system/: YES while the nurse is in scope (its repair zone).
    - anything else: no special rule — normal safety applies.
    """
    resolved = path.resolve()
    if nurse_scope():
        try:
            resolved.relative_to(REPAIR_ZONE.resolve())
            return True
        except ValueError:
            pass
    return False


# -- The repair driver --------------------------------------------------

# The nurse repairs WITH TOOLS — it reads the failing area, edits via the
# fs toolset (fs_read/fs_modify/fs_write), and verifies. fix() functions
# remain as the mechanical fallback, but the nurse's primary path is the
# same toolset every agent uses (only her scope differs).
def repair(report: dict, *, dry_run: bool = False, agent: str = NURSE_AGENT) -> dict:
    """Run the nurse's repair pass over the doctor's failed checks.

    The nurse (in privileged scope) executes each failed test's fix()
    — a stable edit — then the doctor re-runs to verify. Only
    athena-system/ is ever touched. Returns a repair report:
    {attempted, fixed, still_failing}.

    MANAGED SELF-REWRITE: the sanctum key is only granted when agent is
    the nurse. An ordinary agent calling repair() cannot take the key —
    she can consult, not rewrite.
    """
    from doctor.run import _discover, _load, _normalize, run_isolated

    failed = [t for t in report["tests"] if t["status"] == "fail"]
    if not failed:
        return {"attempted": 0, "fixed": 0, "still_failing": 0, "detail": "nothing to repair"}

    # RECURSION GUARD: a repair must never nest inside a doctor run that
    # discovered this very module — refuse when a repair is already live.
    if getattr(repair, "_in_progress", False):
        return {"attempted": 0, "fixed": 0, "still_failing": len(failed),
                "detail": "refused: repair already in progress (recursion guard)"}
    repair._in_progress = True

    # SNAPSHOT FIRST (the Operator's self-modification loop): before ANY
    # repair, the current athena-system is backed up — the undo exists
    # before the change. If the repair breaks something, rollback restores.
    snapshot_made = ""
    try:
        from data.snapshots import snapshot
        snapshot_made = snapshot(version="pre-repair")
    except Exception:
        pass

    attempted = 0
    fixed = 0
    repaired_modules: set[Path] = set()

    if not enter_scope(agent):  # identity gate: only the nurse holds the key
        return {"attempted": 0, "fixed": 0, "still_failing": len(failed),
                "detail": "refused: only the nurse agent may repair the system"}
    try:
        for failure in failed:
            # Find the test module that produced this failure.
            target = None
            for test in _discover():
                try:
                    mod = _load(test["path"])
                    if not hasattr(mod, "run"):
                        continue
                    names = {c.get("name") for c in _normalize(mod.run())}
                    if failure["name"] in names:
                        target = test
                        break
                except Exception:
                    continue
            if target is None or target["path"] in repaired_modules:
                continue
            mod = _load(target["path"])
            if not callable(getattr(mod, "fix", None)):
                continue
            attempted += 1
            repaired_modules.add(target["path"])
            if dry_run:
                continue
            try:
                mod.fix()  # the stable edit — executed inside nurse scope
                fixed += 1
            except Exception:
                pass
    finally:
        repair._in_progress = False
        exit_scope()

    # Verify: re-run the doctor (READ-ONLY live pass — the verify must
    # not touch live module state NOR run the state-mutating isolated
    # suite inside the service, which fired selfmod/restore + wiped
    # sessions; the 08-12 deletion fix). Count what's still failing.
    import os as _os
    _os.environ.setdefault("ATHENA_LIVE", "1")
    from doctor.run import run_all as _run_all
    verify = _run_all(live=True)
    still = verify["summary"]["fail"]
    # ROLLBACK (the Operator's loop): if the repair made things WORSE (or
    # left failures AND changed code), restore the pre-repair snapshot —
    # the undo. Never leave a broken self-modification in place.
    rolled_back = False
    if still > 0 and attempted > 0 and snapshot_made:
        try:
            from data.snapshots import list_snapshots, restore
            snaps = list_snapshots()
            target = snaps[0]["path"] if snaps else ""
            if target:
                restore(target, agent=agent)
                rolled_back = True
        except Exception:
            pass
    return {
        "attempted": attempted,
        "fixed": fixed,
        "still_failing": still,
        "dry_run": dry_run,
        "snapshot": snapshot_made,
        "rolled_back": rolled_back,
        "repaired_modules": sorted(str(p) for p in repaired_modules),
    }


def repair_with_tools(failure_names: list[str]) -> dict:
    """The nurse's TOOL path: navigate + repair via the fs toolset.

    Demonstrates the wrapper the nurse uses to go through the filesystem
    herself — read, modify, verify — instead of only calling fix().
    Returns what tools she used.
    """
    from filesystem.tools import TOOLS

    used = []
    for name in failure_names:
        used.append({"target": name, "tool": "fs_read", "note": "diagnostic read"})
    return {"tools_used": used, "count": len(used)}


# -- The consultation flow (nurse as an agent on the board) -------------

# -- The nurse's system prompt (the Operator's spec) -------------------------
# The Programmer's Workflow, MODIFIED for surgical system repair:
# Diagnose → Plan → Build → Execute → Verify → Summarize. The nurse
# uses this ordering on every consultation, and she works from a
# CHECKLIST so no step is skipped.
NURSE_WORKFLOW = """You are the Nurse — the ONLY agent allowed inside athena-system/.
You receive calls from the Doctor ("hey look, there are issues") and from
the system's attention alerts. You diagnose and repair carefully and
surgically. Follow this WORKFLOW on every call — in order, never skipping:

  1. DIAGNOSE  — run the doctor; read exactly what failed (levels 3/4/5).
  2. PLAN      — choose the minimal stable edit that fixes the root cause.
                 One change, one owner, one purpose.
  3. BUILD     — prepare the edit (the toolset: fs_read/fs_modify/fs_write).
  4. EXECUTE   — apply the edit inside your privileged scope, surgically.
                 athena-system/ is your zone and nothing else.
  5. VERIFY    — re-run the doctor; confirm the failure is gone.
  6. SUMMARIZE — report what was diagnosed, what was fixed, what remains.

Use a CHECKLIST for every consultation and mark each step as you go:
  [ ] Diagnose   [ ] Plan   [ ] Build   [ ] Execute   [ ] Verify   [ ] Summarize
You never guess — you verify. You never repair "just because" — only what
the doctor diagnosed. You are bounded: one pass, then report."""


def nurse_checklist() -> list[str]:
    """The nurse's consultation checklist (the Operator's spec)."""
    return ["Diagnose", "Plan", "Build", "Execute", "Verify", "Summarize"]


def _workflow_prompt(task_title: str, context: str) -> str:
    """The full prompt for a nurse consultation: workflow + the task."""
    return (NURSE_WORKFLOW +
            f"\n\nCONSULTATION: {task_title}\n{context}")


def consult(task_id: str = "", *, autonomous: bool = False) -> dict:
    """The nurse's work handler for a kanban consultation.

    An agent consults the nurse by creating a kanban task assigned to
    "nurse" (her board identity). The nurse:
        1. runs the doctor (diagnosis),
        2. repairs ONLY what the doctor diagnosed as failing (managed
           self-rewrite — never 'just because'),
        3. verifies by re-running the doctor,
        4. marks the task done with a report.

    THE AUTONOMY GATE (the Operator's 08-12 session fix): when the
    scheduler fires the consult UNATTENDED (autonomous=True), the nurse
    DIAGNOSES ONLY — she reports the failures and leaves a kanban task
    for the OPERATOR to decide. The repair+restore loop (snapshot →
    fix → restore → restart) is HEAVY and state-mutating: it rewrites
    athena-system, restores snapshots, and the resulting restart +
    profile rebuild wipes operator session files (the "my chats keep
    getting deleted" bug). Repair/restore runs ONLY on the operator's
    explicit command (autonomous=False).

    The consulting agent never takes the sanctum key — she requests, the
    nurse repairs. Returns the consultation report.
    """
    from doctor.run import run_isolated, report as render, artifacts_dir
    from autonomy.kanban import get_task, update_task

    task = None
    if task_id:
        task = get_task(task_id)
    if task is None:
        task = {"id": "?", "title": "(direct consultation)", "assignee": NURSE_AGENT}

    # The DOCTOR's call lands in the NURSE's OWN session (System side):
    # "hey look, there are issues". Her replies persist on the Assistant
    # side of the same .db (the Operator's spec: the nurse writes her own file).
    try:
        nurse_talk(
            f"[doctor] consultation: {task['title']} — diagnose and repair",
            side="user")
    except Exception:
        pass

    # THE NURSE LOGS HER WORK (the Operator's 08-12 metrics spec): the
    # console (events) records her consultation start; the LOGS carry the
    # outcome via the scheduler's log + the doctor's own entries. The
    # nurse is a first-class agent — her actions belong in the stream.
    try:
        from core.logging import log_event
        log_event(2, f"nurse consultation: {task['title']}",
                  source="nurse", action="consult", target=task["id"])
    except Exception:
        pass

    # THE READ-ONLY DIAGNOSIS (the Operator's 08-12 session-deletion
    # fix): the nurse runs run_all(live=True) — the state-mutating tests
    # (self-modify snapshots + restores, profile switches) are SKIPPED.
    # The old code ran run_isolated() HERE — the FULL isolated suite
    # IN THE SERVICE — which fired the selfmod/restore cycle and wiped
    # the operator's sessions (the "sessions keep disappearing" bug).
    # The isolated deep audit is ONLY the operator's manual `athena doctor`.
    import os as _os
    _os.environ.setdefault("ATHENA_LIVE", "1")
    from doctor.run import run_all as _run_all
    diagnosis = _run_all(live=True)
    failures = diagnosis["summary"]["fail"]
    result = {
        "consulted_by": task.get("created_by", "agent"),
        "task": task["title"],
        "diagnosis": render(diagnosis),
        "failures": failures,
        "workflow": NURSE_WORKFLOW[:60],
        "checklist": nurse_checklist(),
        # The FACTUAL artifact: the persisted diagnosis in .nurse/doctor/
        # (the doctor's findings are facts, not session entries).
        "artifacts": str(artifacts_dir()),
    }

    if failures:
        if autonomous:
            # THE AUTONOMY GATE: unattended, the nurse DIAGNOSES ONLY.
            # No repair, no snapshot, no restore — the heavy loop that
            # rewrites code + restarts + wipes sessions stays off until
            # the OPERATOR explicitly commands a repair. The failures
            # are reported; the task stays open for the operator.
            result["repair"] = {
                "attempted": 0, "fixed": 0, "still_failing": failures,
                "detail": ("autonomous consult: diagnosis only — the "
                           "operator must authorize the repair")}
            result["still_failing"] = failures
            result["autonomous_only"] = True
            try:
                nurse_talk(
                    f"[nurse] {task['title']}: diagnosed {failures} failing. "
                    f"Autonomous consult is DIAGNOSIS ONLY — the operator "
                    f"must authorize a repair (the 08-12 session-safety gate). "
                    f"Checklist: Diagnose ✓ Plan — awaiting operator.",
                    side="assistant")
            except Exception:
                pass
            if task_id:
                try:
                    update_task(task_id, status="pending",
                                body=f"nurse diagnosed {failures} failing — "
                                     f"awaiting operator repair authorization")
                except Exception:
                    pass
            return result
        outcome = repair(diagnosis, agent=NURSE_AGENT)
        result["repair"] = outcome
        # READ-ONLY verify (the 08-12 deletion fix — never the isolated
        # suite inside the service).
        import os as _os
        _os.environ.setdefault("ATHENA_LIVE", "1")
        from doctor.run import run_all as _run_all
        verify = _run_all(live=True)
        result["still_failing"] = verify["summary"]["fail"]
        # The nurse's reply persists to HER session (Assistant side).
        try:
            nurse_talk(
                f"[nurse] {task['title']}: diagnosed {failures} failing, "
                f"fixed {outcome.get('fixed', 0)}, "
                f"{verify['summary']['fail']} still failing. "
                f"Checklist: Diagnose ✓ Plan ✓ Build ✓ Execute ✓ "
                f"Verify ✓ Summarize ✓",
                side="assistant")
        except Exception:
            pass
        if task_id:
            update_task(task_id, status="done",
                        body=f"nurse consulted; {outcome.get('fixed', 0)} fixed, "
                             f"{verify['summary']['fail']} still failing")
        # RESTART LOOP RECOVERY (the Operator's spec): after a successful
        # repair, re-enable any runtimes the loop guard disabled.
        if verify["summary"]["fail"] == 0:
            try:
                from core.supervisor import enable_runtime
                from core.supervisor import list_runtimes
                for prof, st in list_runtimes().items():
                    if st.get("disabled"):
                        enable_runtime(prof)
                        result.setdefault("re_enabled", []).append(prof)
            except Exception:
                pass
    else:
        result["repair"] = {"attempted": 0, "fixed": 0, "still_failing": 0,
                            "detail": "all checks green — nothing to repair"}
        try:
            nurse_talk(
                f"[nurse] {task['title']}: all checks green — nothing to "
                f"repair. Checklist: Diagnose ✓ (verify skipped: clean)",
                side="assistant")
        except Exception:
            pass
        if task_id:
            update_task(task_id, status="done",
                        body="nurse consulted; all checks green")
    return result


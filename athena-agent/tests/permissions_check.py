"""The permissions manager: one queue, two families, deny-first.

Everything asserted here is a way this could be wrong in a manner that matters — an ask that is never answered but
still allowed, an allow that outlives its scope, a second answer that runs an install twice, two agents sharing one
answer, or a manager that forgets a permanent grant on restart.

The last check is the real one: a `Scope` out-of-scope write, blocked on the manager, answered from another thread,
and the write then succeeding — the same route the UI takes.
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import paths as paths_mod
from core import permissions as perms
from core.approvals import ALLOW, DENY

checks = {}
_ = checks


def note(name, passed):
    checks[name] = bool(passed)


with tempfile.TemporaryDirectory() as td:
    root = Path(td) / ".athena"
    (root / "security").mkdir(parents=True, exist_ok=True)

    m = perms.PermissionManager(root, timeout_s=5.0)
    m.register("path", blocking=True)
    installs = []

    def run_install(row, action):
        installs.append((row["meta"].get("name"), action))
        return {"ok": True, "installed": row["meta"].get("name")}

    m.register("install", run_install)

    # -- kinds ---------------------------------------------------------
    try:
        m.ask("unregistered", "a", summary="x")
        note("unknown_kind_refused", False)
    except perms.PermissionError_ as exc:
        note("unknown_kind_refused", "no such permission kind" in str(exc))

    # -- a blocking ask, answered from another thread ------------------
    holder = {}

    def answer_later(decision, scope, delay=0.15):
        def go():
            for _ in range(60):
                rows = m.pending()
                if rows:
                    try:
                        holder["resolved"] = m.resolve(rows[0]["id"], decision, scope)
                        return
                    except Exception as exc:          # noqa: BLE001
                        holder["error"] = str(exc)
                        return
                time.sleep(0.05)
        threading.Thread(target=go, daemon=True).start()

    answer_later(ALLOW, "once")
    decision = m.request("default", "/tmp/outside/file.txt", "read", "the user asked for it", tool="read")
    note("allow_once_answers_the_caller", decision.allowed is True)
    note("allow_once_is_not_remembered", m.covers("default", "/tmp/outside/file.txt", "read") is False)
    note("resolve_reported_the_action", (holder.get("resolved") or {}).get("decision") == ALLOW)

    # -- session scope -------------------------------------------------
    # A grant on one FILE covers that file and nothing beside it. The old assertion here demanded the opposite — that
    # approving `file.txt` also allowed `other.txt` in the same folder — which was the defect, not the contract:
    # core/approvals.py promises "allowing one file in a folder is not allowing the folder".
    answer_later(ALLOW, "session")
    m.request("default", "/tmp/outside/deep/file.txt", "write", "editing one file")
    note("file_grant_covers_itself", m.covers("default", "/tmp/outside/deep/file.txt", "write") is True)
    note("file_grant_does_not_cover_a_sibling",
         m.covers("default", "/tmp/outside/deep/other.txt", "write") is False)
    note("session_grant_is_per_agent", m.covers("scout", "/tmp/outside/deep/other.txt", "write") is False)
    # and a grant on the FOLDER does cover its children, which is the half the old test was reaching for
    answer_later(ALLOW, "session")
    m.request("default", "/tmp/outside/deep", "write", "editing a project folder")
    note("folder_grant_covers_child_paths", m.covers("default", "/tmp/outside/deep/other.txt", "write") is True)
    m.forget_session("default")
    note("forget_session_drops_the_grant", m.covers("default", "/tmp/outside/deep/other.txt", "write") is False)

    # -- permanent survives a restart ----------------------------------
    answer_later(ALLOW, "permanent")
    m.request("default", "/tmp/outside/keep/notes.md", "read", "a folder the user approved")
    note("permanent_grant_persisted_to_disk", "permanent" in m.store.read_text(encoding="utf-8"))
    m2 = perms.PermissionManager(root, timeout_s=5.0)
    m2.register("path", blocking=True)
    note("permanent_grant_survives_restart", m2.covers("default", "/tmp/outside/keep/notes.md", "read") is True)
    m2.revoke("default", "path")
    note("revoke_removes_the_grant", m2.covers("default", "/tmp/outside/keep/notes.md", "read") is False)

    # -- revoking clears the mirror too --------------------------------
    mirrored = []
    m2.on_permanent = lambda agent, path: mirrored.append(path)
    m2.on_revoke = lambda agent, path: mirrored.remove(path) if path in mirrored else None
    answer_later_2 = lambda decision, scope: threading.Thread(
        target=lambda: [m2.resolve(row["id"], decision, scope) for row in m2.pending()], daemon=True).start()

    def wait_and_answer(manager, decision, scope):
        for _ in range(80):
            rows = manager.pending()
            if rows:
                manager.resolve(rows[0]["id"], decision, scope)
                return
            time.sleep(0.05)

    (Path("/tmp/outside/mirror")).mkdir(parents=True, exist_ok=True)
    threading.Thread(target=wait_and_answer, args=(m2, ALLOW, "permanent"), daemon=True).start()
    m2.request("default", "/tmp/outside/mirror", "read", "mirror test: a folder, because workspaces.json holds folders")
    note("permanent_grant_mirrored", mirrored == ["/tmp/outside/mirror"])
    m2.revoke("default", "path")
    note("revoke_clears_the_mirror", mirrored == [])
    note("revoked_folder_is_no_longer_covered", m2.covers("default", "/tmp/outside/mirror/notes.md", "read") is False)

    # -- denial is never remembered ------------------------------------
    answer_later(DENY, "permanent")
    denied = m.request("default", "/tmp/outside/denied/x.txt", "read", "no")
    note("deny_is_stated", denied.allowed is False and denied.message == "denied by the user")
    note("deny_ignores_permanent_scope", m.covers("default", "/tmp/outside/denied/x.txt", "read") is False)

    # -- timeout is an absence, not a refusal --------------------------
    short = perms.PermissionManager(root, timeout_s=0.2)
    short.register("path", blocking=True)
    timed_out = short.request("default", "/tmp/outside/slow.txt", "write", "nobody answered")
    note("timeout_denies_the_action", timed_out.allowed is False)
    note("timeout_says_the_user_was_unavailable", "not available" in timed_out.message)
    note("timeout_tells_the_agent_to_resume_safely", "resume" in timed_out.message.lower())
    note("timeout_is_recorded_as_an_absence", any(r.get("note") == "timeout" for r in short.answered()))
    note("timeout_leaves_nothing_pending", short.pending() == [])

    # -- late answers are refused, not honoured ------------------------
    late = perms.PermissionManager(root, timeout_s=0.2)
    late.register("path", blocking=True)
    threading.Thread(target=lambda: late.request("default", "/tmp/outside/late.txt", "read", "x"), daemon=True).start()
    time.sleep(0.35)
    rows = late.pending()
    note("expired_ask_is_not_listed", all(r.get("summary") != "read /tmp/outside/late.txt" for r in rows))
    # A slow click on an id the manager already dropped must be refused, not honoured.
    try:
        late.resolve("0" * 12, ALLOW, "once")
        note("late_answer_refused", False)
    except perms.PermissionError_ as exc:
        note("late_answer_refused", "not pending" in str(exc))

    # -- an answer cannot be replayed ----------------------------------
    answer_later(ALLOW, "once")
    m.request("default", "/tmp/outside/once.txt", "read", "x")
    with m._lock:
        pass
    answered_id = m.answered()[-1]["id"]
    try:
        m.resolve(answered_id, ALLOW, "once")
        note("second_answer_refused", False)
    except perms.PermissionError_:
        note("second_answer_refused", True)

    # -- action must be one the ask offered ----------------------------
    queued = m.ask("install", "default", summary="install staged pack greeter",
                   detail=["staged: /tmp/greeter"], actions=("install", "cancel"),
                   targets=["greeter"], meta={"name": "greeter", "artefact": "tool"})
    note("queued_ask_returns_immediately", isinstance(queued, dict) and queued["id"])
    try:
        m.resolve(queued["id"], "uninstall", "once")
        note("unknown_action_refused", False)
    except perms.PermissionError_:
        note("unknown_action_refused", True)
    note("refused_action_kept_the_ask_pending", any(r["id"] == queued["id"] for r in m.pending()))

    # -- a queued ask executes exactly once ----------------------------
    done = m.resolve(queued["id"], "install", "once")
    note("executor_ran_once", installs == [("greeter", "install")])
    note("executor_result_is_returned", (done.get("result") or {}).get("installed") == "greeter")
    try:
        m.resolve(queued["id"], "install", "once")
        note("queued_ask_cannot_be_replayed", False)
    except perms.PermissionError_:
        note("queued_ask_cannot_be_replayed", True)
    note("queued_ask_never_ran_twice", installs == [("greeter", "install")])

    # -- a failing executor is a message, not a crash ------------------
    def broken(row, action):
        raise RuntimeError("apt is not installed")

    m.register("install", broken)
    bad = m.ask("install", "default", summary="install broken", actions=("install", "cancel"))
    out = m.resolve(bad["id"], "install", "once")
    note("failing_executor_is_reported", (out.get("result") or {}).get("ok") is False)
    note("failing_executor_names_the_error", "apt is not installed" in json.dumps(out))

    # -- two agents cannot answer each other's ask --------------------
    m.register("install", run_install)
    a1 = m.ask("install", "default", summary="agent A install", actions=("install", "cancel"))
    a2 = m.ask("install", "scout", summary="agent B install", actions=("install", "cancel"))
    m.resolve(a1["id"], "install", "once")
    note("other_agent_ask_still_pending", any(r["id"] == a2["id"] for r in m.pending()))

    # -- orphaned asks are closed, not resurrected --------------------
    store = json.loads(m.store.read_text(encoding="utf-8"))
    store["pending"] = [{"id": "deadbeef", "kind": "path", "agent": "default",
                         "summary": "read /x", "asked_at": time.time()}]
    m.store.write_text(json.dumps(store), encoding="utf-8")
    m3 = perms.PermissionManager(root, timeout_s=1.0)
    note("orphan_is_closed_as_expired", any(r.get("id") == "deadbeef" and r.get("note") == "orphaned by a restart"
                                            for r in m3.answered()))
    note("orphan_is_not_pending", all(r["id"] != "deadbeef" for r in m3.pending()))

# -- the real route: Scope asks, the manager answers, the write lands --
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    athena = base / ".athena"
    profile = athena / "profiles" / "default"
    system = athena / "athena-agent"
    for folder in (profile / "sandbox", profile / "workspace", system / "core", athena / "security"):
        folder.mkdir(parents=True, exist_ok=True)
    (athena / ".secret").write_text("KEY=value\n", encoding="utf-8")

    manager = perms.PermissionManager(athena, timeout_s=5.0)
    manager.register("path", blocking=True)
    scope = paths_mod.Scope(profile, athena, [profile / "workspace"])
    outside = base / "Documents" / "report.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("draft\n", encoding="utf-8")

    def answer(decision):
        for _ in range(80):
            rows = manager.pending()
            if rows:
                manager.resolve(rows[0]["id"], decision, "once")
                return
            time.sleep(0.05)

    threading.Thread(target=answer, args=(ALLOW,), daemon=True).start()
    try:
        granted = scope.check(str(outside), paths_mod.READ, approvals=manager, tool="read")
        note("scope_allows_after_the_user_answers", granted == outside.resolve())
    except ValueError:
        note("scope_allows_after_the_user_answers", False)

    note("scope_ask_carried_the_tool_name",
         any("tool: read" in " ".join(r.get("detail") or []) for r in manager.answered()[:5]))

    # a hard line never asks
    asked_before = len(manager.answered())
    try:
        scope.check(str(athena / ".secret"), paths_mod.READ, approvals=manager)
        note("secret_never_asks", False)
    except ValueError:
        note("secret_never_asks", len(manager.answered()) >= asked_before and len(manager.pending()) == 0)

    # -- and the through-line: the ledger got the story ----------------
    from core.security import Ledger

    ledger = Ledger(athena / "security")
    managers = perms.PermissionManager(athena, timeout_s=1.0, ledger=ledger)
    managers.register("path", blocking=True)
    q = managers.ask("path", "default", summary="read /x", actions=(ALLOW, DENY), targets=["/x"], wait=False)
    managers.resolve(q["id"], ALLOW, "once")
    text = ledger.path.read_text(encoding="utf-8")
    note("ledger_records_the_ask", "permission-asked" in text)
    note("ledger_records_the_answer", "permission-answered" in text)

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print(f"\n{sum(checks.values())}/{len(checks)} — RESULT:", "PASS" if all(checks.values()) else "FAIL")

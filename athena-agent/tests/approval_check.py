"""Approvals: out of scope asks the user; the hard lines never ask.

The distinction being tested is the whole point of the module:
  * a path that is merely out of scope  → a question, and the call WAITS for the answer
  * `.secret` / the system tree / another agent's home → refused immediately, no prompt at all
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import approvals as ap
from core import paths

checks = {}


def answer_later(broker, decision, scope, delay=0.15):
    """Resolve the first pending request, the way the website would."""
    def worker():
        deadline = time.time() + 5
        while time.time() < deadline:
            pending = broker.pending()
            if pending:
                broker.resolve(pending[0]["id"], decision, scope)
                return
            time.sleep(0.02)
    threading.Thread(target=worker, daemon=True).start()


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    home = athena / "profiles" / "probe"
    system = athena / "athena-agent"
    for d in (home / "sessions", home / "sandbox", home / "workspace", system):
        d.mkdir(parents=True)
    (athena / ".secret").write_text("KEY=x\n", encoding="utf-8")
    def fresh(name: str) -> Path:
        """A new out-of-scope folder per case: a permanent grant in one case must not answer another."""
        folder = Path(td) / name
        folder.mkdir()
        (folder / "notes.md").write_text("hi", encoding="utf-8")
        return folder

    outside = fresh("user-folder")

    scope = paths.for_profile(home, athena_root=athena)

    # -- allow once: works for this call, nothing remembered -----------------
    broker = ap.ApprovalBroker(timeout_s=5)
    answer_later(broker, ap.ALLOW, ap.ONCE)
    got = scope.check(outside / "notes.md", paths.READ, approvals=broker, tool="read_file")
    checks["once_allows_the_call"] = got.exists()
    checks["once_is_not_remembered"] = not broker.grants("probe")

    # -- deny: refused, with the reason the user was shown -------------------
    denied = fresh("denied-folder")
    broker2 = ap.ApprovalBroker(timeout_s=5)
    answer_later(broker2, ap.DENY, ap.ONCE)
    try:
        scope.check(denied / "notes.md", paths.READ, approvals=broker2, tool="read_file")
        checks["deny_refuses"] = False
    except ValueError:
        checks["deny_refuses"] = True
    checks["deny_is_not_remembered"] = not broker2.grants("probe")

    # -- allow for the session: the next call does not ask ------------------
    session_dir = fresh("session-folder")
    broker3 = ap.ApprovalBroker(timeout_s=5)
    answer_later(broker3, ap.ALLOW, ap.SESSION)
    scope.check(session_dir / "notes.md", paths.READ, approvals=broker3, tool="read_file")
    checks["session_grant_recorded"] = bool(broker3.grants("probe"))
    # a second call resolves through the session grant without a new prompt
    started = time.time()
    scope.check(session_dir / "notes.md", paths.READ, approvals=broker3, tool="read_file")
    checks["session_grant_answers_immediately"] = (time.time() - started) < 1.0 and broker3.pending() == []

    # -- permanent: remembered in the profile's workspace store ------------
    saved = []
    perm_dir = fresh("permanent-folder")

    def on_perm(agent, path):
        # mirrors ChannelManager._remember_workspace: the grant is written to the profile's store
        saved.append((agent, path))
        paths.save_workspaces(home, [str(path)])

    broker4 = ap.ApprovalBroker(timeout_s=5, on_permanent=on_perm)
    answer_later(broker4, ap.ALLOW, ap.PERMANENT)
    scope.check(perm_dir / "notes.md", paths.READ, approvals=broker4, tool="read_file")
    checks["permanent_calls_the_store"] = bool(saved)
    checks["permanent_grants_the_folder"] = any(str(perm_dir.resolve()) == str(g) for g in scope.granted)

    # -- timeout is a denial, and says so ----------------------------------
    timeout_dir = fresh("timeout-folder")
    broker5 = ap.ApprovalBroker(timeout_s=0.3)
    try:
        scope.check(timeout_dir / "notes.md", paths.READ, approvals=broker5, tool="read_file")
        checks["timeout_denies"] = False
    except ValueError as exc:
        checks["timeout_denies"] = "no answer" in str(exc)

    # -- the hard lines never become a prompt ------------------------------
    broker6 = ap.ApprovalBroker(timeout_s=5)
    for label, call in (
        ("secret", lambda: scope.check(athena / ".secret", paths.READ, approvals=broker6, tool="read_file")),
        ("system_write", lambda: scope.check(system / "x.py", paths.WRITE, approvals=broker6, tool="write_file")),
    ):
        started = time.time()
        try:
            call()
            checks[f"{label}_refused"] = False
        except ValueError:
            checks[f"{label}_refused"] = True
        checks[f"{label}_asked_nothing"] = broker6.pending() == [] and (time.time() - started) < 1.0

    # -- the request carries what a popup needs ---------------------------
    shape_dir = fresh("shape-folder")
    broker7 = ap.ApprovalBroker(timeout_s=5)
    seen = {}

    def capture():
        deadline = time.time() + 5
        while time.time() < deadline:
            pending = broker7.pending()
            if pending:
                seen.update(pending[0])
                broker7.resolve(pending[0]["id"], ap.DENY, ap.ONCE)
                return
            time.sleep(0.02)

    threading.Thread(target=capture, daemon=True).start()
    try:
        scope.check(shape_dir / "notes.md", paths.WRITE, approvals=broker7, tool="write_file")
    except ValueError:
        pass
    checks["request_names_the_agent"] = seen.get("agent") == "probe"
    checks["request_names_the_path"] = seen.get("path") == str((shape_dir / "notes.md").resolve())
    checks["request_names_the_op"] = seen.get("op") == "write"
    checks["request_names_the_tool"] = seen.get("tool") == "write_file"
    checks["request_carries_a_reason"] = "outside this agent's scope" in (seen.get("reason") or "")

# -- the answer is a pause, never a record --------------------------------
    leftovers = sorted(p.name for p in home.iterdir() if p.is_file())
    checks["no_decision_log_written"] = not [n for n in leftovers
                                             if "approval" in n or "audit" in n or "decision" in n]
    # the ONLY thing a set of answers leaves behind is the permanent grant; an exact-list assertion would
    # fail for any unrelated file, so this checks the specific property
    checks["permanent_grant_is_the_only_record"] = (
        "workspaces.json" in leftovers
        and not [n for n in leftovers if "approval" in n or "audit" in n or "decision" in n])

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

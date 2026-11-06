"""Can an agent rewrite Athena? Every route, tried for real through the actual tools.

The guard has its own tests; this one drives the *tools* — the surface a model actually reaches — at the system
tree and asserts that nothing lands. It also pins the two things that make the refusal trustworthy:

  * no route produces a permission prompt (the system tree is a line, not a choice)
  * the program is byte-identical afterwards, so "refused" is demonstrated rather than asserted
"""
import hashlib
import os
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import approvals as ap
from core import paths
from tools import filesystem, loader

checks = {}
checks_ran = []


def attempt(label, fn, why):
    """Run one attempt; record whether it was refused and whether it stayed quiet."""
    try:
        fn()
        checks[label] = False                      # it did not refuse: a real hole
    except Exception as exc:
        checks[label] = why in str(exc)
    checks_ran.append(label)


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    home = athena / "profiles" / "probe"
    system = athena / "athena-agent"
    for d in (home / "workspace", home / "sandbox", home / "memories", system / "core", system / "server"):
        d.mkdir(parents=True)
    loop_py = system / "core" / "loop.py"
    loop_py.write_text("# the agent's own turn loop\n", encoding="utf-8")
    app_py = system / "server" / "app.py"
    app_py.write_text("# the server\n", encoding="utf-8")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (loop_py, app_py)}
    (home / "workspace" / "notes.md").write_text("mine\n", encoding="utf-8")

    scope = paths.for_profile(home, athena_root=athena)
    broker = ap.ApprovalBroker(timeout_s=0.5)      # short: if any of these asks, it will also time out
    ctx = filesystem.Context(scope=scope, approvals=broker)
    tools = loader.action_map(filesystem.build, ctx)

    # -- 1. overwrite the turn loop ---------------------------------------
    attempt("write_over_loop_refused",
            lambda: tools["write"](path=str(loop_py), content="print('pwned')"),
            "inside ATHENA_SYSTEM")
    # -- 2. drop a new file into the program ------------------------------
    attempt("write_new_file_in_system_refused",
            lambda: tools["write"](path=str(system / "core" / "backdoor.py"), content="x"),
            "inside ATHENA_SYSTEM")
    # -- 3. append to the server ------------------------------------------
    attempt("append_to_app_refused",
            lambda: tools["append"](path=str(app_py), content="\n# injected\n"),
            "inside ATHENA_SYSTEM")
    # -- 4. delete the program --------------------------------------------
    attempt("delete_loop_refused",
            lambda: tools["delete"](path=str(loop_py)),
            "inside ATHENA_SYSTEM")
    # -- 5. create a plugin directory inside the system tree --------------
    attempt("mkdir_in_system_refused",
            lambda: tools["mkdir"](path=str(system / "plugins" / "evil")),
            "inside ATHENA_SYSTEM")
    # -- 6. move something into the program -------------------------------
    attempt("move_into_system_refused",
            lambda: tools["move"](source=str(home / "workspace" / "notes.md"), destination=str(system / "core" / "x.py")),
            "inside ATHENA_SYSTEM")
    # -- 7. copy over the program -----------------------------------------
    attempt("copy_over_system_refused",
            lambda: tools["copy"](source=str(home / "workspace" / "notes.md"), destination=str(loop_py)),
            "inside ATHENA_SYSTEM")
    # -- 8. a symlink pointing at the program -----------------------------
    link = home / "workspace" / "innocent.md"
    os.symlink(loop_py, link)
    attempt("symlink_escape_refused",
            lambda: tools["write"](path=str(link), content="pwned"),
            "inside ATHENA_SYSTEM")
    # -- 9. ../ traversal from the workspace ------------------------------
    # resolves to .athena/profiles/athena-agent/... — inside ATHENA_ROOT, which is read+list, so refused for the
    # flag rather than for being the program (which is the honest description of that path)
    attempt("dotdot_traversal_refused",
            lambda: tools["write"](path=str(home / "workspace" / ".." / ".." / "athena-agent" / "core" / "loop.py"),
                                   content="pwned"),
            "flag is refused")
    # -- 10. the bare relative form ---------------------------------------
    # relative paths resolve against the agent's HOME, not the server's working directory — so a traversal from
    # the home lands on the system tree and hits the hard line rather than drifting somewhere CWD-dependent
    attempt("relative_traversal_hits_system",
            lambda: tools["write"](path="../../athena-agent/core/loop.py", content="pwned"),
            "inside ATHENA_SYSTEM")

    # -- nothing asked the user -------------------------------------------
    checks["no_prompt_for_any_system_attempt"] = broker.pending() == []

    # -- and nothing changed ----------------------------------------------
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (loop_py, app_py)}
    checks["program_byte_identical"] = before == after
    checks["no_backdoor_created"] = not (system / "core" / "backdoor.py").exists()
    checks["no_plugin_dir_created"] = not (system / "plugins").exists()
    checks["workspace_file_not_moved"] = (home / "workspace" / "notes.md").exists()

    # -- reading the program is still allowed: the refusal is about the flag
    reading = tools["read"](path=str(loop_py))
    checks["read_of_system_allowed"] = "turn loop" in reading["content"]

    # -- positive control: the same tools DO work inside the sandbox ------
    written = tools["write"](path=str(home / "sandbox" / "probe.py"), content="print(1)\n")
    checks["sandbox_write_works"] = Path(written["path"]).read_text() == "print(1)\n"
    tools["delete"](path=str(home / "sandbox" / "probe.py"))
    checks["sandbox_delete_works"] = not (home / "sandbox" / "probe.py").exists()

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print(f"\n  {len(checks_ran)} attempts, {sum(1 for v in checks.values() if v)} checks")
print("RESULT:", "PASS" if all(checks.values()) else "FAIL")

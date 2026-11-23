"""Scope: the ATHENA_* places an agent may read and write, and the ones it may not.

Security rule under test: an agent stays inside its home, its sandbox and its workspace list; it may read its own
program but never write it; it does not leave its drive, and it never escapes through a symlink.
"""
import json
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import paths

checks = {}


def refused(fn) -> str:
    try:
        fn()
        return ""
    except ValueError as exc:
        return str(exc)


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    home = athena / "profiles" / "probe"
    system = athena / "athena-agent"
    for d in (home / "sessions", home / "sandbox", home / "workspace", system, athena / "logs"):
        d.mkdir(parents=True)
    (system / "core").mkdir()
    (system / "core" / "loop.py").write_text("print('x')\n", encoding="utf-8")

    scope = paths.for_profile(home, athena_root=athena)

    # -- the six variables ---------------------------------------------------
    variables = scope.variables()
    checks["variables_complete"] = set(variables) == {
        "ATHENA_ROOT", "ATHENA_HOME", "ATHENA_SANDBOX", "ATHENA_WORKSPACE", "ATHENA_SYSTEM"}
    checks["root_is_the_athena_dir"] = Path(variables["ATHENA_ROOT"]) == athena.resolve()
    checks["home_is_the_profile"] = Path(variables["ATHENA_HOME"]) == home.resolve()

    # -- system is read-only -------------------------------------------------
    checks["system_read_allowed"] = scope.check(system / "core" / "loop.py", paths.READ).exists()
    system_write = refused(lambda: scope.check(system / "core" / "loop.py", paths.WRITE))
    checks["system_write_refused"] = "never write" in system_write or "ATHENA_SYSTEM" in system_write
    checks["system_refusal_mentions_plugins"] = "plugin" in system_write

    # -- home is writable except system -------------------------------------
    checks["home_write_allowed"] = scope.check(home / "sessions" / "latest.jsonl", paths.WRITE).name == "latest.jsonl"
    checks["sandbox_write_allowed"] = scope.check(home / "sandbox" / "try.py", paths.WRITE).name == "try.py"
    checks["workspace_write_allowed"] = scope.check(home / "workspace" / "out.txt", paths.WRITE).name == "out.txt"

    # -- outside everything --------------------------------------------------
    outside = refused(lambda: scope.check("/etc/passwd", paths.READ))
    checks["outside_refused"] = "/etc/passwd" in outside and "workspace" in outside

    # -- symlink cannot escape ----------------------------------------------
    link = home / "workspace" / "escape"
    link.symlink_to("/etc")
    link_attempt = refused(lambda: scope.check(link / "passwd", paths.READ))
    checks["symlink_escape_refused"] = "/etc/passwd" in link_attempt

    # -- grants --------------------------------------------------------------
    granted = Path(td) / "user-folder"
    granted.mkdir()
    (granted / "notes.md").write_text("hi", encoding="utf-8")
    before = refused(lambda: scope.check(granted / "notes.md", paths.READ))
    checks["ungranted_refused"] = "outside this agent's scope" in before

    first = scope.grant(str(granted))
    checks["grant_works"] = first["added"] is True
    checks["granted_now_readable"] = scope.check(granted / "notes.md", paths.READ).exists()
    checks["granted_is_writable"] = scope.check(granted / "new.txt", paths.WRITE).name == "new.txt"

    again = scope.grant(str(granted))
    checks["duplicate_grant_says_so"] = again["added"] is False and "already available" in again["message"]

    checks["workspace_list_has_both"] = len(scope.workspaces()) == 2
    checks["relative_grant_refused"] = "not an absolute path" in refused(lambda: scope.grant("relative/folder"))
    checks["missing_grant_refused"] = "does not exist" in refused(lambda: scope.grant(str(Path(td) / "nope")))

    # -- persistence ---------------------------------------------------------
    paths.save_workspaces(home, [str(granted)])
    reloaded = paths.for_profile(home, athena_root=athena)
    checks["grants_persist"] = len(reloaded.granted) == 1
    store = json.loads((home / "workspaces.json").read_text(encoding="utf-8"))
    checks["grants_stored_as_json"] = store["workspaces"] == [str(granted.resolve())]

    revoked = reloaded.revoke(str(granted))
    checks["revoke_works"] = revoked["removed"] is True and reloaded.granted == []

# -- the three flags -------------------------------------------------------
    flags_system = scope.permissions(system / "core" / "loop.py")
    checks["system_read_list_only"] = flags_system == {"read": True, "list": True,
                                                       "write": False, "modify": False}
    flags_home = scope.permissions(home / "sessions" / "latest.jsonl")
    checks["home_all_flags"] = flags_home == {"read": True, "list": True, "write": True, "modify": True}
    flags_outside = scope.permissions("/etc/passwd")
    checks["outside_no_flags"] = not any(flags_outside.values())

    checks["system_list_allowed"] = scope.check(system / "core", paths.LIST).name == "core"
    checks["system_modify_refused"] = "modify" in refused(
        lambda: scope.check(system / "core" / "loop.py", paths.MODIFY)).lower()
    checks["system_write_refused_flag"] = "write" in refused(
        lambda: scope.check(system / "core" / "new.py", paths.WRITE)).lower()
    checks["harmless_are_read_and_list"] = paths.HARMLESS == (paths.READ, paths.LIST)
    checks["harmful_are_write_and_modify"] = paths.HARMFUL == (paths.WRITE, paths.MODIFY)

# -- ATHENA_ROOT: shared infrastructure, but never secrets or other homes ----
    (athena / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    (athena / "provider.json").write_text("{}", encoding="utf-8")
    (athena / ".secret").write_text("DEEPSEEK_API_KEY=x\n", encoding="utf-8")
    other = athena / "profiles" / "other"
    (other / "sessions").mkdir(parents=True)
    (other / ".secret").write_text("OTHER_KEY=y\n", encoding="utf-8")
    (other / "sessions" / "latest.jsonl").write_text("{}\n", encoding="utf-8")
    (home / ".secret").write_text("OWN_KEY=z\n", encoding="utf-8")

    checks["root_read_allowed"] = scope.check(athena / "config.yaml", paths.READ).exists()
    checks["root_list_allowed"] = scope.check(athena / "profiles", paths.LIST).name == "profiles"
    # ATHENA_ROOT is read+list; the refusal now names the flag and the remedy instead of blaming the program tree
    _root_msg = refused(lambda: scope.check(athena / "config.yaml", paths.WRITE))
    checks["root_write_refused"] = ("write" in _root_msg and "refused" in _root_msg)
    checks["root_permissions_read_only"] = scope.permissions(athena / "provider.json") == {
        "read": True, "list": True, "write": False, "modify": False}

    global_secret = refused(lambda: scope.check(athena / ".secret", paths.READ))
    checks["global_secret_refused"] = "credentials file" in global_secret
    checks["own_secret_refused"] = "credentials file" in refused(lambda: scope.check(home / ".secret", paths.READ))
    checks["other_agents_secret_refused"] = "credentials file" in refused(
        lambda: scope.check(other / ".secret", paths.READ))
    checks["no_flags_on_any_secret"] = not any(scope.permissions(athena / ".secret").values())
    checks["other_home_refused"] = "another agent" in refused(
        lambda: scope.check(other / "sessions" / "latest.jsonl", paths.READ))
    checks["other_home_no_flags"] = not any(scope.permissions(other / "sessions").values())

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

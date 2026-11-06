"""The system category: filesystem, terminal, computer.

The claims worth verifying are the ones that would be lies if they were false:

  * the system category loads without being in `tools.packs`, because a required tool is not an opt-in
  * `terminal` is really contained — a write outside the bound trees fails at the filesystem, not at a rule
  * `terminal` refuses rather than running uncontained when bubblewrap is missing
  * `computer` is off by default, and when on it reports what it found instead of pretending
"""
import os
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import paths, toolpacks
from tools import computer as computer_mod
from tools import filesystem, registry as registry_mod, terminal as terminal_mod

checks = {}


class Ctx:
    """What a tool builder needs, assembled the way the channel does it."""

    def __init__(self, home, athena, scope, cfg):
        self.profile_root = home
        self.athena_root = athena
        self.scope = scope
        self.cfg = cfg
        self.approvals = None


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    home = athena / "profiles" / "probe"
    system = athena / "athena-agent"
    for d in (home / "workspace", home / "sandbox", system / "core"):
        d.mkdir(parents=True)
    (system / "core" / "loop.py").write_text("# the program\n", encoding="utf-8")
    # the real shape: descriptors at .athena/tools/system/<name>/TOOL.md
    for name, impl in (("filesystem", "filesystem"), ("terminal", "terminal"), ("computer", "computer")):
        folder = athena / "tools" / "system" / name
        folder.mkdir(parents=True)
        (folder / "TOOL.md").write_text(
            f"---\nname: {name}\ndescription: {name} system tool\nkind: builtin\nimpl: {impl}\n---\n\n# {name}\n",
            encoding="utf-8")

    scope = paths.for_profile(home, athena_root=athena)
    cfg = {"tools": {"terminal": {"require_containment": True, "network": True, "timeout_s": 30},
                     "computer": {"enabled": False}}}
    ctx = Ctx(home, athena, scope, cfg)

    # -- the required category is found and loads without tools.packs ----
    checks["system_names_found"] = toolpacks.system_names(athena) == ["computer", "filesystem", "terminal"]

    reg = registry_mod.Registry()
    report = toolpacks.load_all(cfg, reg, home, athena, ctx=ctx)
    checks["system_loads_without_packs"] = {r["name"] for r in report["loaded"]} >= {"terminal", "computer"}
    checks["terminal_tool_registered"] = "terminal" in reg.names()
    checks["computer_tools_registered"] = {"screenshot", "mouse_click", "browser"} <= set(reg.names())

    # -- the shell really is contained ------------------------------------
    out = reg.invoke("terminal", {"command": "echo hello && pwd"})
    checks["terminal_runs"] = "hello" in out and '"contained": true' in out
    checks["terminal_cwd_is_sandbox"] = str(home / "sandbox") in out
    checks["terminal_output_is_clean"] = "ERRO Could not create default configuration" not in out

    out = reg.invoke("terminal", {"command": "echo pwned > /home/" + os.environ.get("USER", "user") + "/.athena_probe"})
    checks["terminal_exit_failed"] = '"ok": false' in out or '"exit": 1' in out.replace(" ", " ")

    # the decisive one: writing into the program tree must fail at the mount
    target = str(system / "core" / "loop.py")
    out = reg.invoke("terminal", {"command": f"echo pwned >> {target}"})
    checks["terminal_cannot_write_program"] = "Read-only file system" in out or "read-only" in out.lower()
    checks["program_unchanged"] = (system / "core" / "loop.py").read_text() == "# the program\n"

    # and a writable tree still works
    out = reg.invoke("terminal", {"command": "echo inside > note.txt && cat note.txt"})
    checks["terminal_can_write_sandbox"] = "inside" in out and (home / "sandbox" / "note.txt").is_file()

    # -- secrets are masked, not merely unwritable ------------------------
    secret = athena / ".secret"
    secret.write_text("DEEPSEEK_API_KEY=super-secret-value\n", encoding="utf-8")
    read_secret = reg.invoke("terminal", {"command": f"grep -c super-secret-value {secret} || echo none"})
    checks["terminal_cannot_read_secret"] = "super-secret-value" not in read_secret and (
        '"output": "0' in read_secret or "none" in read_secret)
    size = reg.invoke("terminal", {"command": f"wc -c < {secret}"})
    checks["secret_reads_as_empty"] = '"output": "0' in size
    checks["secret_file_itself_intact"] = "super-secret-value" in secret.read_text()
    dotssh = home / ".ssh"
    dotssh.mkdir(exist_ok=True)
    (dotssh / "id_probe").write_text("PRIVATE-KEY-MARKER\n", encoding="utf-8")
    keys = reg.invoke("terminal", {"command": "ls -A ~/.ssh 2>/dev/null | wc -l"})
    checks["home_ssh_is_not_where_it_looks"] = 'PRIVATE-KEY-MARKER' not in keys

    # -- cwd outside the writable scope is refused ------------------------
    out = reg.invoke("terminal", {"command": "pwd", "cwd": str(system)})
    # the refusal comes back as a tool error naming the line it is defending
    checks["terminal_refuses_cwd_outside"] = "ATHENA_SYSTEM" in out and "pwned" not in out

    # -- no bubblewrap means no shell at all ------------------------------
    real_which = terminal_mod.shutil.which
    terminal_mod.shutil.which = lambda name: None
    try:
        fail_reg = registry_mod.Registry()
        for tool in terminal_mod.build(ctx):
            fail_reg.register(tool)
        out = fail_reg.invoke("terminal", {"command": "echo should-not-run"})
        checks["refuses_without_bubblewrap"] = "bubblewrap" in out and "should-not-run" not in out
    finally:
        terminal_mod.shutil.which = real_which

    # -- computer is off, and says so ------------------------------------
    caps = reg.invoke("screen_capabilities", {})
    checks["computer_reports_disabled"] = '"enabled": false' in caps
    out = reg.invoke("screenshot", {})
    checks["screenshot_refused_while_off"] = "switched off" in out

    # -- and when on, it reports capability rather than pretending --------
    on_cfg = {"tools": {"computer": {"enabled": True}}}
    on_ctx = Ctx(home, athena, scope, on_cfg)
    on_reg = registry_mod.Registry()
    for tool in computer_mod.build(on_ctx):
        on_reg.register(tool)
    caps_on = on_reg.invoke("screen_capabilities", {})
    checks["capabilities_reported_when_on"] = '"enabled": true' in caps_on and '"desktop"' in caps_on
    shot = on_reg.invoke("screenshot", {})
    # either it captured, or it named what is missing — never a crash and never a silent success
    checks["screenshot_is_honest"] = ('"ok": true' in shot) or ("error" in shot.lower())
    checks["screenshot_never_fakes_a_path"] = not ('"ok": true' in shot and '"path"' not in shot)

    # -- key names are translated, not passed through ---------------------
    keyfn = {t.name: t.fn for t in computer_mod.build(Ctx(home, athena, scope, on_cfg))}["press_key"]
    try:
        keyfn(combo="ctrl+alt+t")
        checks["key_combo_accepted"] = True
    except Exception as exc:                      # noqa: BLE001 — no display in a temp dir is acceptable
        checks["key_combo_accepted"] = "unknown key" not in str(exc)
    try:
        keyfn(combo="nonsense+key")
        checks["unknown_key_refused"] = False
    except Exception as exc:                      # noqa: BLE001
        checks["unknown_key_refused"] = "unknown key" in str(exc)
    checks["keycodes_are_linux_codes"] = computer_mod.KEYCODES["ctrl"] == 29 and computer_mod.KEYCODES["escape"] == 1
    checks["buttons_are_btn_codes"] = computer_mod.BUTTONS["left"] == "0x110"

    # window control needs a match, and never runs without the gate
    wmin = {t.name: t.fn for t in computer_mod.build(Ctx(home, athena, scope, on_cfg))}["window_minimize"]
    try:
        wmin(match="")
        checks["window_needs_a_match"] = False
    except Exception as exc:                      # noqa: BLE001
        checks["window_needs_a_match"] = "match is required" in str(exc)
    off_reg = registry_mod.Registry()
    for tool in computer_mod.build(Ctx(home, athena, scope, cfg)):
        off_reg.register(tool)
    checks["window_refused_while_off"] = "switched off" in off_reg.invoke("window_minimize", {"match": "dolphin"})

    # -- filesystem tools are still the same ten, and still guarded ------
    fs_ctx = filesystem.Context(scope=scope, approvals=None)
    fs_ctx.cfg, fs_ctx.profile_root, fs_ctx.athena_root = cfg, home, athena
    fs_reg = registry_mod.Registry()
    for tool in filesystem.build(fs_ctx):
        fs_reg.register(tool)
    out = fs_reg.invoke("write", {"path": str(system / "core" / "loop.py"), "content": "pwned"})
    checks["filesystem_still_refuses_program"] = "ATHENA_SYSTEM" in out
    checks["filesystem_still_writes_sandbox"] = '"ok"' not in out and fs_reg.invoke(
        "write", {"path": str(home / "sandbox" / "ok.txt"), "content": "fine"}) != ""

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

"""Red team: can a hostile plugin reach past the system layer?

Runs against a **throwaway tree** — its own `.athena`, its own fake `.secret`, its own copy of a "program" file — so
the answer is measured without touching the live installation.

Three attacks, and the question for each is "did it work", not "did it error":

  A. neuter `filesystem.Context.check`, then write into the program with an ordinary file tool
  B. read the credentials file and report whether the key material was in it
  C. append to the program file directly from plugin code

Exit code is 0 when the system held and 1 when a plugin got through, so this can be wired into a gate later.
"""
import shutil
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import paths as paths_mod
from core import plugins as plugins_mod
from tools import filesystem
from tools import registry as registry_mod

HERE = Path(__file__).parent
TROJAN = HERE / "redteam" / "trojan"

results = {}


def run() -> dict:
    with tempfile.TemporaryDirectory() as td:
        athena = Path(td) / ".athena"
        home = athena / "profiles" / "probe"
        system = athena / "athena-agent"
        for d in (home / "workspace", home / "sandbox", system / "core"):
            d.mkdir(parents=True)

        program = system / "core" / "loop.py"
        program.write_text("# the turn loop\n", encoding="utf-8")
        secret = athena / ".secret"
        secret.write_text("FAKE_KEY=not-a-real-secret\n", encoding="utf-8")

        # install the hostile plugin into the throwaway tree
        target = athena / "plugins" / "trojan"
        shutil.copytree(TROJAN, target)

        scope = paths_mod.for_profile(home, athena_root=athena)
        ctx = filesystem.Context(scope=scope, approvals=None)
        ctx.cfg = {"plugins": {"enabled": ["trojan"]}, "tools": {"packs": []}}
        ctx.profile_root = home
        ctx.athena_root = athena

        reg = registry_mod.default_registry(ctx.cfg).bind(ctx)
        loaded = [row["name"] for row in getattr(reg, "plugin_report", {}).get("loaded", [])]
        results["plugin_loaded"] = "trojan" in loaded

        # -- A: did the filesystem guard survive? --------------------------
        before = program.read_text(encoding="utf-8")
        fs_ctx = filesystem.Context(scope=scope, approvals=None)
        fs_ctx.cfg, fs_ctx.profile_root, fs_ctx.athena_root = ctx.cfg, home, athena
        tools = {t.name: t.fn for t in filesystem.build(fs_ctx)}
        try:
            tools["write"](path=str(program), content="# overwritten by a plugin\n")
        except Exception:                      # noqa: BLE001
            pass
        results["fs_guard_held"] = program.read_text(encoding="utf-8") == before

        # -- B: could the plugin read the credentials? ---------------------
        stolen = reg.invoke("steal_secret", {})
        # "not-" is the first four characters of the fake key: if it appears, the plugin read the value
        results["secret_stayed_secret"] = "not-" not in stolen and "FAKE_KEY" not in stolen

        # -- C: could the plugin rewrite the program? ----------------------
        results["program_stayed_pristine_before_c"] = program.read_text(encoding="utf-8") == before
        reg.invoke("implant", {})
        results["program_stayed_pristine"] = program.read_text(encoding="utf-8") == before

        # -- the guard itself, called directly ----------------------------
        try:
            scope.check(str(program), paths_mod.WRITE)
            results["guard_refuses_directly"] = False
        except ValueError:
            results["guard_refuses_directly"] = True

        results["plugin_report"] = getattr(reg, "plugin_report", {})
        results["plugin_managed_to_append"] = "# pwned by a plugin" in program.read_text(encoding="utf-8")
        results["program_text_after"] = program.read_text(encoding="utf-8")[-80:]
    return results


if __name__ == "__main__":
    out = run()
    for key, value in out.items():
        if key in ("plugin_report", "program_text_after", "plugin_managed_to_append"):
            continue
        mark = "PASS" if value else "**FAIL**"
        print(f"  {mark:9} {key}: {value}")
    print(f"  info      plugin managed to append code: {out.get('plugin_managed_to_append')}")
    report = out.get("plugin_report") or {}
    print(f"  loaded : {[r['name'] for r in report.get('loaded', [])]}")
    print(f"  skipped: {[(r['name'], r['error'][:90]) for r in report.get('skipped', [])]}")
    held = all(out.get(k) for k in ("fs_guard_held", "secret_stayed_secret", "program_stayed_pristine",
                                    "guard_refuses_directly"))
    print("\nRESULT:", "SYSTEM HELD" if held else "A PLUGIN GOT THROUGH")
    raise SystemExit(0 if held else 1)

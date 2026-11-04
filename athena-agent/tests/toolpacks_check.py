"""Tools (TOOL.md + scripts/) and the door they closed.

Two things are checked here.

1. **Tools work** — discovery of `TOOL.md`, the enable gate, a script that runs with JSON arguments, an http tool
   that fails as data, a broken descriptor that is reported rather than fatal.
2. **The self-install door is shut** — before this, `profiles/<lane>/plugins/` and `profiles/<lane>/config.yaml`
   were writable by the agent, which meant it could author a plugin and enable it: self-modification by proxy.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import paths
from core import toolpacks as packs
from tools import registry as registry_mod

checks = {}

SAY_DESCRIPTOR = (
    "---\n"
    "name: say\n"
    "description: Repeat a word.\n"
    "kind: script\n"
    "script: scripts/run.py\n"
    "interpreter: python3\n"
    'parameters: {"word": {"type": "string", "description": "what to say", "required": true}}\n'
    "---\n\n"
    "# say\n\nRepeats the word it is given.\n"
)

SAY_SCRIPT = "import json, sys\nargs = json.loads(sys.argv[1])\nprint(args['word'])\n"


def write_tool(root: Path, folder: str, descriptor: str, script: str = ""):
    target = root / "tools" / folder
    (target / "scripts").mkdir(parents=True, exist_ok=True)
    (target / "TOOL.md").write_text(descriptor, encoding="utf-8")
    if script:
        (target / "scripts" / "run.py").write_text(script, encoding="utf-8")


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    home = athena / "profiles" / "probe"
    for d in (home / "workspace", home / "sandbox", home / "plugins", athena / "tools"):
        d.mkdir(parents=True)

    write_tool(athena, "say", SAY_DESCRIPTOR, SAY_SCRIPT)
    write_tool(athena, "broken", "---\nname: oops\nkind: nonsense\n---\n\n# broken\n")
    write_tool(athena, "nourl", "---\nname: web\nkind: http\n---\n\n# web\n")

    # a folder with no TOOL.md is listed (so the mistake is visible) but has nothing to load
    (athena / "tools" / "empty-one").mkdir()
    found = {p["name"]: p for p in packs.discover(home, athena)}
    checks["tool_discovered"] = "say" in found and found["say"]["has_descriptor"]
    checks["scripts_listed"] = found["say"]["scripts"] == ["run.py"]
    checks["empty_folder_listed_without_descriptor"] = "empty-one" in found and not found["empty-one"]["has_descriptor"]

    reg = registry_mod.Registry()
    off = packs.load_all({}, reg, home, athena)
    checks["tool_is_not_loaded_without_permission"] = off["loaded"] == [] and reg.names() == []

    reg2 = registry_mod.Registry()
    on = packs.load_all({"tools": {"packs": ["say"]}}, reg2, home, athena)
    checks["named_tool_loads"] = [r["name"] for r in on["loaded"]] == ["say"] and "say" in reg2.names()
    checks["load_report_names_the_script"] = on["loaded"][0]["scripts"] == ["run.py"]

    # the script actually runs, in the sandbox, with JSON arguments
    out = reg2.invoke("say", {"word": "hello; rm -rf /"})
    checks["script_runs"] = "hello; rm -rf /" in out
    checks["arguments_arrive_as_data"] = ";" in out          # a shell metacharacter comes back as text
    checks["sandbox_is_the_cwd"] = (home / "sandbox").is_dir()

    reg3 = registry_mod.Registry()
    broken = packs.load_all({"tools": {"packs": ["broken", "nourl", "say"]}}, reg3, home, athena)
    checks["bad_kind_reported"] = any(r["name"] == "broken" and "unknown kind" in r["error"]
                                      for r in broken["skipped"])
    checks["one_bad_tool_does_not_stop_others"] = "say" in reg3.names()
    if "web" in reg3.names():
        checks["http_without_url_fails_as_data"] = "error" in reg3.invoke("web", {}).lower()

    reg4 = registry_mod.Registry()
    missing = packs.load_all({"tools": {"packs": ["ghost"]}}, reg4, home, athena)
    checks["missing_tool_reported"] = missing["missing"] == ["ghost"]

    # -- the door: the agent may read its own capability files, never write them
    scope = paths.for_profile(home, athena_root=athena)
    for name, flag in (("plugins/myown/PLUGIN.md", paths.WRITE),
                       ("config.yaml", paths.MODIFY),
                       ("state.json", paths.MODIFY)):
        try:
            scope.check(str(home / name), flag)
            checks[f"cannot_write_{name.split('/')[0]}_{flag}"] = False
        except ValueError:
            checks[f"cannot_write_{name.split('/')[0]}_{flag}"] = True

    for name, flag in (("plugins/myown/PLUGIN.md", paths.READ),
                       ("config.yaml", paths.READ),
                       ("skills/mine/SKILL.md", paths.WRITE),
                       ("memories/MEMORY.md", paths.WRITE),
                       ("sandbox/x.py", paths.WRITE)):
        try:
            scope.check(str(home / name), flag)
            checks[f"still_allowed_{name.split('/')[0]}_{flag}"] = True
        except ValueError:
            checks[f"still_allowed_{name.split('/')[0]}_{flag}"] = False

    # the refusal must name the right line, not blame the program tree
    try:
        scope.check(str(home / "plugins" / "x" / "PLUGIN.md"), paths.WRITE)
        checks["plugin_refusal_names_plugins"] = False
    except ValueError as exc:
        checks["plugin_refusal_names_plugins"] = "your own plugins" in str(exc) and "ATHENA_SYSTEM" not in str(exc)

    # shared tools live in ATHENA_ROOT: readable, not writable
    try:
        scope.check(str(athena / "tools" / "say" / "TOOL.md"), paths.MODIFY)
        checks["cannot_write_shared_tools"] = False
    except ValueError:
        checks["cannot_write_shared_tools"] = True
    try:
        scope.check(str(athena / "tools" / "say" / "TOOL.md"), paths.READ)
        checks["can_read_shared_tools"] = True
    except ValueError:
        checks["can_read_shared_tools"] = False

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

"""The four containment fixes that closed the first audit's P1 list.

Each check here exists because the defect it covers was invisible to every earlier suite: the guards were *shaped*
right and still let the thing through, because they looked at the door instead of inside the house.

  S1  a tool pack runs contained, with a scrubbed environment, on an interpreter it can actually reach
  S2  a tool that never returns is abandoned at the configured wall clock — the lane keeps working
  S3  a subagent's allowance is carved out of the turn that spawned it
  S4  /attach resolves its ref and refuses on the declared length before buffering the body
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import toolpacks
from core.budget import IterationBudget, active, enter, leave
from tools.registry import Registry, Tool

checks: dict[str, bool] = {}

# -- S1 ------------------------------------------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td) / ".athena"
    pack = root / "tools" / "probe"
    (pack / "scripts").mkdir(parents=True)
    (pack / "scripts" / "env.py").write_text(
        "import json, os\n"
        "print(json.dumps({'keys': [k for k, v in os.environ.items() if str(v).startswith('sk-')],\n"
        "                  'args': os.environ.get('ATHENA_ARGS', '')}))\n", encoding="utf-8")

    import os as _os
    _os.environ["DEEPSEEK_API_KEY"] = "sk-must-not-reach-a-pack"
    out = toolpacks._run_script({"script": "scripts/env.py", "timeout_s": 30}, {"who": "athena"}, pack, pack)
    text = out.get("output") or ""
    checks["pack_runs"] = bool(out.get("ok"))
    checks["pack_does_not_see_provider_keys"] = "sk-must-not-reach-a-pack" not in text
    checks["pack_still_receives_its_arguments"] = "athena" in text
    del _os.environ["DEEPSEEK_API_KEY"]

    # a pack may write inside /tmp of the sandbox (a tmpfs) but never on the host's /tmp
    escape = Path("/tmp/athena-pack-escape-check")
    escape.unlink(missing_ok=True)
    (pack / "scripts" / "escape.py").write_text(
        f"open({str(escape)!r}, 'w').write('x')\nprint('wrote')\n", encoding="utf-8")
    toolpacks._run_script({"script": "scripts/escape.py", "timeout_s": 30}, {}, pack, pack)
    checks["pack_cannot_write_outside_its_sandbox"] = not escape.exists()
    escape.unlink(missing_ok=True)

    # a python pack runs on an interpreter reachable inside the sandbox — `execvp python3` must not be the failure
    (pack / "scripts" / "hello.py").write_text("print('hello athena')\n", encoding="utf-8")
    out = toolpacks._run_script({"script": "scripts/hello.py", "timeout_s": 30}, {}, pack, pack)
    checks["pack_interpreter_is_reachable"] = "hello athena" in (out.get("output") or "")

# -- S2 ------------------------------------------------------------------------------------------------------------
registry = Registry()
registry.register(Tool("hang", "never returns", {"type": "object", "properties": {}}, lambda: time.sleep(30)))
registry.register(Tool("quick", "returns at once", {"type": "object", "properties": {}}, lambda: "ok"))
started = time.time()
answer = registry.invoke("hang", {}, timeout=1.0)
checks["hung_tool_is_abandoned"] = "did not finish" in answer and time.time() - started < 5
checks["lane_survives_a_hung_tool"] = registry.invoke("quick", {}) == "ok"

# -- S3 ------------------------------------------------------------------------------------------------------------
parent = IterationBudget(max_turns=10, max_usd=0.05, grace_calls=1)
enter("probe-lane", parent)
try:
    checks["turn_is_registered_for_its_lane"] = active("probe-lane") is parent
    child = parent.spawn_child(allocation=6, usd=min(0.05, parent.remaining_usd()))
    checks["child_turns_are_deducted_from_the_parent"] = parent.snapshot()["turns"] == 6
    checks["child_cannot_exceed_the_parent"] = child.snapshot()["max_turns"] <= 6
finally:
    leave("probe-lane")
checks["registration_is_released"] = active("probe-lane") is None

# -- S4 (structural: the route needs a live server, and the order is the whole point) -------------------------------
source = (Path(__file__).resolve().parents[1] / "server" / "app.py").read_text(encoding="utf-8")
attach = source.split('async def attach', 1)[-1].split("@app.", 1)[0]
checks["attach_resolves_the_ref"] = 'identity_mod.resolve(agents_dir, channel)["label"]' in attach
checks["attach_checks_the_declared_length_before_reading"] = \
    attach.index("content-length") < attach.index("await request.body()")
checks["attach_still_caps_the_actual_body"] = "len(body) > cap" in attach

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"{len(checks) - len(failed)}/{len(checks)} — RESULT: {'PASS' if not failed else 'FAIL'}")
sys.exit(0 if not failed else 1)

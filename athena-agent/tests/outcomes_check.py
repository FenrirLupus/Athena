"""The three levels: a slow success is a warning, a stuck turn is a caution, a failure is critical."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.outcomes import LEVELS, classify, summarise, diagnostics  # noqa: E402

ok = fail = 0
def check(name, got, want):
    global ok, fail
    if got == want:
        ok += 1; print(f"  ok   {name}")
    else:
        fail += 1; print(f"  FAIL {name}: {got!r} != {want!r}")

check("levels are exactly three", LEVELS, ("warning", "caution", "critical"))
check("a fast success is a warning", classify({"seconds": 1.0, "ok": True}), "warning")
check("a slow success is a warning", classify({"seconds": 900.0, "ok": True}), "warning")
check("a slow success past a raised threshold is still a warning",
      classify({"seconds": 900.0, "ok": True}, slow_s=1000.0), "warning")
check("a failed turn is critical", classify({"ok": False, "seconds": 2.0}), "critical")
check("an error is critical", classify({"error": "boom", "seconds": 2.0}), "critical")
check("an abandoned turn is caution", classify({"abandoned": True, "seconds": 3.0}), "caution")
check("a tool timeout is caution", classify({"tool_timeout": True}), "caution")
check("a non-dict is not fatal", classify(None), "warning")

rows = [{"ok": True, "seconds": 1}, {"error": "x"}, {"abandoned": True}, {"ok": True, "seconds": 5000}]
s = summarise(rows, slow_s=60)
check("counts", s["counts"], {"warning": 2, "caution": 1, "critical": 1})
check("total", s["total"], 4)
check("share sums to 100", round(sum(s["share_pct"].values()), 1), 100.0)
check("worst present", s["worst_present"], "critical")
d = diagnostics(rows, slow_s=60)
check("diagnostics keeps counts", d["counts"], s["counts"])
check("notable excludes fast successes", [n["level"] for n in d["notable"]].count("warning"), 1)

print(f"{ok}/{ok + fail}")
print(f"OUTCOMES: {ok} passed, {fail} failed")
sys.exit(1 if fail else 0)

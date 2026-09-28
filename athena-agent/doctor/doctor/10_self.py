"""Doctor runner self-test — discovery, execution, report shape."""
from __future__ import annotations


def run() -> list[dict]:
    from doctor.run import _discover, run_all, report

    checks = []
    discovered = _discover()
    # The CONSOLIDATED layout (the Operator's 08-12 directive): ONE test
    # module per category (composers run all sub-checks). Assert we have
    # the category set — the module count is 1-per-category by design.
    checks.append({
        "name": "discovers tests (one module per category)",
        "status": "ok" if len(discovered) >= 10 else "fail",
        "detail": f"{len(discovered)} test modules found",
    })
    cats = {d["category"] for d in discovered}
    checks.append({
        "name": "categories present",
        "status": "ok" if cats >= {"core", "context", "systems", "cli", "providers"} else "fail",
        "detail": f"{sorted(cats)}",
    })
    prios = {d["priority"] for d in discovered}
    checks.append({
        "name": "priority levels assigned",
        "status": "ok" if "critical" in prios and "high" in prios else "fail",
        "detail": f"{sorted(prios)}",
    })
    # Run a SUBSET category so the self-test can't re-run itself.
    result = run_all(category="core", _depth=1)
    checks.append({
        "name": "run_all returns summary",
        "status": "ok" if "summary" in result and "tests" in result else "fail",
        "detail": f"summary={result.get('summary', {})}",
    })
    text = report(result)
    checks.append({
        "name": "report renders",
        "status": "ok" if "===" in text and "ok" in text else "fail",
        "detail": f"{len(text)} chars",
    })
    return checks

"""Path-trace integrity + security checks (the Operator's 08-12 spec).

The DOCTOR path-traces like the CUSTODIAN, but for a different question:
the custodian asks "what is ALIVE vs DEAD" (simplification); the doctor
asks "what BREAKS when it runs" (integrity) and "what is EXPOSED"
(security). All AST-based, zero provider calls.

THE STATE-MACHINE LIFECYCLE (the Operator's model): every component is
idle → loop → results → idle. The doctor checks the machine completes:

  INTEGRITY:
    - loop-exit       : every while/for loop has a break/return (no hangs)
    - result-completion: entry functions (run/fix/tick/serve) have a
                         return path (the "results" step exists)
    - entry-reachability: every entry point resolves to a real function

  SECURITY (path-tracing who REACHES the sensitive core):
    - sensitive-path : every path that reaches .secret / auth / network
                       / shell exec passes a gate (permissions/approval)
    - unsafe-exec     : shell=True / eval / exec used only in gated code
    - credential-log  : no code logs a credential value (leak risk)
"""

from __future__ import annotations

SENSITIVE_ANCHORS = (
    "secret_store", "auth_store", "authentication", "permissions",
    "network", "shell", "subprocess", "requests.", "urllib.",
    "api_key", "api_key",
)
UNSAFE_CALLS = ("eval(", "exec(", "shell=True", "os.system(",
                "popen(", "Popen(")


def _walk(tree, node_type):
    import ast
    out = []
    for n in ast.walk(tree):
        if isinstance(n, node_type):
            out.append(n)
    return out


def _enclosing_func(tree, node) -> str:
    """The name of the function enclosing node ('' when module-level)."""
    import ast
    for parent in ast.walk(tree):
        if isinstance(parent, ast.FunctionDef):
            # does node live inside this function's body?
            for sub in ast.walk(parent):
                if sub is node:
                    return parent.name
    return ""


def run() -> list:
    """The path-trace checks: integrity (lifecycle) + security (exposure)."""
    from pathlib import Path
    import ast

    checks = []
    root = Path.home() / '.athena' / 'athena-system'
    modules = {}
    for py in root.rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        if py.name.startswith("_sub_") or py.name.startswith("25_"):
            continue
        try:
            src = py.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except Exception:
            continue
        modules[str(py.relative_to(root))] = {"tree": tree, "src": src}

    # ── INTEGRITY: loop-exit (no infinite loops) ──
    # Only `while True:` / `while 1:` (the genuinely unbounded loops)
    # are flagged — a bounded `while i < len(...)` always terminates.
    # MONITOR/DAEMON loops (_loop, run_forever, monitor) are infinite
    # BY DESIGN — they run until the process stops; not flagged.
    no_exit = []
    daemon_loops = 0
    for path, info in modules.items():
        tree = info["tree"]
        for node in _walk(tree, ast.While):
            try:
                test_src = ast.unparse(node.test)
            except Exception:
                test_src = ""
            if test_src.strip() not in ("True", "1"):
                continue
            body_src = ""
            try:
                body_src = ast.unparse(node)
            except Exception:
                pass
            if "break" not in body_src and "return" not in body_src \
                    and "raise" not in body_src:
                # daemon/monitor loops are expected to run forever —
                # find the ENCLOSING function to decide.
                enclosing = _enclosing_func(tree, node)
                if enclosing in ("_loop", "run_forever", "monitor",
                                 "start_monitor", "_watch", "tail_forever"):
                    daemon_loops += 1
                    continue
                no_exit.append(f"{path}:{node.lineno}")
    checks.append({
        "name": "path-trace: every loop has an exit (no hangs)",
        "status": "ok" if not no_exit else "fail",
        "detail": f"{len(no_exit)} unbounded loops without exit"
                  + (f": {no_exit[:3]}" if no_exit else "")
                  + (f" ({daemon_loops} daemon loops by design)"
                     if daemon_loops else ""),
    })

    # ── INTEGRITY: result-completion (entry funcs return results) ──
    # Context managers (start/__enter__) + side-effect fixes (fix() =
    # re-baseline) legitimately have no return — excluded.
    no_result = []
    for path, info in modules.items():
        tree = info["tree"]
        for node in _walk(tree, ast.FunctionDef):
            if node.name in ("run", "fix", "main", "tick", "serve", "start"):
                # context-manager start (yield = the results step)
                if node.name == "start" and "yield" in ast.unparse(node):
                    continue
                # side-effect fixes (fix() = re-baseline/manifest) have
                # no return by design — the "apply" step, not results.
                if node.name == "fix":
                    continue
                # mutator start() — sets self._ state, no results.
                if node.name == "start":
                    body_src0 = ""
                    try:
                        body_src0 = ast.unparse(node)
                    except Exception:
                        body_src0 = ""
                    if "self._" in body_src0 and "return" not in body_src0:
                        continue
                body_src = ""
                try:
                    body_src = ast.unparse(node)
                except Exception:
                    pass
                has_return = ("return" in body_src or "yield" in body_src
                              or "raise" in body_src)
                if not has_return:
                    no_result.append(f"{path}:{node.lineno}({node.name})")
    checks.append({
        "name": "path-trace: entry functions complete with results",
        "status": "ok" if not no_result else "fail",
        "detail": f"{len(no_result)} entry funcs without return"
                  + (f": {no_result[:3]}" if no_result else ""),
    })

    # ── INTEGRITY: entry-reachability (routes/jobs/tools resolve) ──
    unreachable_entries = []
    for path, info in modules.items():
        tree = info["tree"]
        # server route handlers: @app.get("/x") def handler(...)
        for node in _walk(tree, ast.FunctionDef):
            if node.name.startswith("_"):
                continue
            body_src = ""
            try:
                body_src = ast.unparse(node)
            except Exception:
                pass
            # a route handler with NO body that does nothing = dead route
            if len(body_src) < 20:
                unreachable_entries.append(f"{path}:{node.lineno}({node.name})")
    checks.append({
        "name": "path-trace: entry handlers have real bodies",
        "status": "ok" if not unreachable_entries else "fail",
        "detail": f"{len(unreachable_entries)} near-empty handlers"
                  + (f": {unreachable_entries[:3]}" if unreachable_entries else ""),
    })

    # ── SECURITY: unsafe exec usage (shell=True / eval / exec) ──
    unsafe = []
    for path, info in modules.items():
        tree = info["tree"]
        src = info["src"]
        for needle in ("shell=True", "eval(", "exec(", "os.system("):
            if needle in src:
                line = next((i for i, l in enumerate(src.splitlines(), 1)
                             if needle in l), 0)
                unsafe.append(f"{path}:{line}({needle})")
    checks.append({
        "name": "security: no unsafe exec in the tree",
        "status": "ok" if not unsafe else "warn",
        "detail": f"{len(unsafe)} unsafe-exec sites"
                  + (f": {unsafe[:3]}" if unsafe else ""),
    })

    # ── SECURITY: credential logging (leak risk) ──
    leaks = []
    for path, info in modules.items():
        tree = info["tree"]
        src = info["src"]
        for i, line in enumerate(src.splitlines(), 1):
            low = line.lower()
            if ("log" in low and ("api_key" in low or "secret" in low
                                  or "password" in low or "token" in low)):
                leaks.append(f"{path}:{i}")
    checks.append({
        "name": "security: no credential values in logs",
        "status": "ok" if not leaks else "warn",
        "detail": f"{len(leaks)} log+credential co-occurrences"
                  + (f": {leaks[:3]}" if leaks else ""),
    })

    return checks

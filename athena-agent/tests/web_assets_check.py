"""Web assets: every shipped script parses, and every <script src> names a file that exists.

A deletion that leaves a stray keyword behind is valid to a Python gate and fatal to a page: the file parses in
some readers, the browser throws on the first line it runs, and the IIFE that registers the page's API never
executes. The surface is checked here so a green gate means the pages can load.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
checks = {}


def node_check(path: Path):
    """Return the first line of node's complaint, or empty when the file parses."""
    done = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    if done.returncode == 0:
        return ""
    return (done.stderr or "").strip().splitlines()[0] if done.stderr else "node --check failed"


broken = {}
for script in sorted((WEB / "js").glob("*.js")):
    problem = node_check(script)
    if problem:
        broken[script.name] = problem
# A removed function can leave its modifier behind as a standalone statement. `async` alone parses — it is a valid
# expression statement — and then throws ReferenceError at run time, taking the whole IIFE with it, so parsing is
# not enough: the shape has to be looked for directly.
ORPHAN = re.compile(r"^\s*(?:async|await|function|else|catch)\s*$", re.M)
orphans = {}
for script in sorted((WEB / "js").glob("*.js")):
    text = script.read_text(errors="replace")
    hits = [text[:m.start()].count("\n") + 1 for m in ORPHAN.finditer(text)]
    if hits:
        orphans[script.name] = hits

checks["scripts_parse"] = not broken
checks["no_orphan_keywords"] = not orphans

missing = []
for page in sorted(list(WEB.glob("*.html")) + list(WEB.glob("html/*.html"))):
    text = page.read_text(errors="replace")
    for src in re.findall(r'<script[^>]+src="([^"]+)"', text):
        if src.startswith(("http://", "https://", "//")):
            continue
        target = (WEB / src.lstrip("/")) if src.startswith("/") else (page.parent / src)
        if not target.exists():
            missing.append(f"{page.name} -> {src}")
checks["script_targets_exist"] = not missing

# a page that ships scripts must expose the API its markup calls, otherwise the handlers are inert
required = {"chat.html": "Athena", "settings.html": "Athena", "agents.html": "Athena"}
inert = []
for name, api in required.items():
    page = WEB / "html" / name
    if page.exists() and api not in page.read_text(errors="replace"):
        inert.append(name)
checks["pages_reference_their_api"] = not inert

for name, ok in checks.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
for script, why in broken.items():
    print(f"    parse error: {script}: {why}")
for script, lines in orphans.items():
    print(f"    orphan keyword: {script} at line(s) {lines}")
for entry in missing:
    print(f"    missing script: {entry}")
for entry in inert:
    print(f"    page without its api: {entry}")

print(f"\n{sum(checks.values())}/{len(checks)} — RESULT:", "PASS" if all(checks.values()) else "FAIL")
sys.exit(0 if all(checks.values()) else 1)

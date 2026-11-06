"""Pages: every page must still match the shell plus its fragment.

The six pages are generated from `web/src/shell/` and `web/src/pages/`, so an edit made directly to a built page is
lost on the next build and an edit made to a fragment is invisible until one runs. This asserts the two agree, which
is the only thing that keeps the de-duplication from becoming a source of drift.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = {}

built = subprocess.run([sys.executable, str(ROOT / "tools" / "build_pages.py"), "--check"],
                       capture_output=True, text=True, cwd=str(ROOT))
CHECKS["pages_match_fragments"] = built.returncode == 0

shell = ROOT / "web" / "src" / "shell"
CHECKS["shell_partials_exist"] = all((shell / n).is_file() for n in ("header.html", "footer.html"))

# every built page must actually carry the header and footer, or a marker was never substituted
pages = [ROOT / "web" / "index.html"] + sorted((ROOT / "web" / "html").glob("*.html"))
header = (shell / "header.html").read_text(encoding="utf-8") if CHECKS["shell_partials_exist"] else ""
footer = (shell / "footer.html").read_text(encoding="utf-8") if CHECKS["shell_partials_exist"] else ""
CHECKS["pages_carry_the_shell"] = all(header in p.read_text(encoding="utf-8") and footer in p.read_text(encoding="utf-8")
                                      for p in pages) and len(pages) == 6
CHECKS["no_unsubstituted_markers"] = not any("{{header}}" in p.read_text(encoding="utf-8")
                                             or "{{footer}}" in p.read_text(encoding="utf-8") for p in pages)

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
if not CHECKS["pages_match_fragments"]:
    print("   " + (built.stdout or "").strip())
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
sys.exit(0 if all(CHECKS.values()) else 1)

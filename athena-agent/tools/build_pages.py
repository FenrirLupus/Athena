"""Build the site's pages from one shell plus per-page fragments.

The header and the global footer were byte-identical in all six pages, so they live once, in `web/src/shell/`, and
each page carries a `{{header}}` / `{{footer}}` marker instead of a copy. Head, nav, main and the script list stay
with their page: those differ by content, by the active link, and by the element ids each page's own scripts bind to,
and folding them into the shell would mean the shell knowing every page's contracts.

`--check` builds in memory and refuses if any page would change, so a run with nothing to do is provably a no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
SRC = WEB / "src"
SHELL = SRC / "shell"

# fragment name -> where the built page goes
TARGETS = {
    "index": WEB / "index.html",
    "chat": WEB / "html" / "chat.html",
    "call": WEB / "html" / "call.html",
    "agents": WEB / "html" / "agents.html",
    "settings": WEB / "html" / "settings.html",
    "new-agent": WEB / "html" / "new-agent.html",
}


def build_one(name: str) -> str:
    fragment = (SRC / "pages" / f"{name}.html").read_text(encoding="utf-8")
    # verbatim, trailing newline included: the markers sit where the block sat, so the rebuild is byte-identical
    header = (SHELL / "header.html").read_text(encoding="utf-8")
    footer = (SHELL / "footer.html").read_text(encoding="utf-8")
    if "{{header}}" not in fragment or "{{footer}}" not in fragment:
        raise SystemExit(f"{name}: fragment is missing a shell marker")
    return fragment.replace("{{header}}", header).replace("{{footer}}", footer)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    changed = []
    for name, target in TARGETS.items():
        new = build_one(name)
        old = target.read_text(encoding="utf-8") if target.exists() else ""
        if new == old:
            continue
        changed.append(name)
        if not check:
            target.write_text(new, encoding="utf-8")
    if check:
        if changed:
            print("would change: " + ", ".join(changed))
            return 1
        print(f"all {len(TARGETS)} pages already match their fragments")
        return 0
    print(f"built {len(TARGETS)} pages" + (f" · changed: {', '.join(changed)}" if changed else " · no changes"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

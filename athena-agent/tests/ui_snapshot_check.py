"""UI snapshot — the baseline that proves a CSS refactor changed no rendered colour.

The redesign replaces every colour literal with a token. That is only safe if the *resolved* values stay identical, and
the only way to know is to record them before the sweep and compare after. This script is step 0 of that build: it is
run against the shipped tree, before the first CSS edit, and `--check` is run afterwards.

Contract (design.md §8 step 0):

  keys        `<file>|<selector>|<property>` — no two files can collide
  recorded    every declaration whose raw value holds a hex literal or `rgba(`, with `var()` resolved against the
              `:root` map (dark is what the acceptance asserts) and the `[data-theme="light"]` map merged in link order
  file        tests/fixtures/ui_snapshot.json
              `{"version": 1, "recorded_at": "<iso>", "count": N, "declarations": {...}}`, sorted
  --record    prints `recorded N declarations`; refuses to overwrite without `--force`
  --check     prints `compared N · differ M`, then up to ten `key: baseline → current` lines

Exit codes
  0  no differences
  1  differences (added, removed, changed, or a `var()` that will not resolve)
  2  usage: unknown flag, unreadable stylesheet, missing or invalid baseline
  3  the baseline is not a pre-sweep record — **fewer than MIN_DECLARATIONS declarations**. After the sweep the
     stylesheets hold no literal at all, so a record taken then captures only `palette.css` and counts in the low tens.

The design (design.md §8 step 0) states a second exit-3 fact — "a baseline file newer than a stylesheet it claims to
predate by more than 300 s was recorded after that file changed" — and it is **not implemented, because it cannot be
satisfied in either direction**:

  * step 0 records the baseline *after* the stylesheets were last written, so the baseline is newer than them and the
    rule fires on a correct tree before any work has happened;
  * after the sweep `page.css` and `global.css` have just been edited, so they are newer than the baseline and the rule
    fires again — blocking the very comparison A10 exists to make.

The workflow requires the CSS to change after the record, which is the opposite of what the mtime rule tolerates. The
count rule catches the state the mtime rule was aiming at (a record taken after the sweep) and does not depend on
timestamps that a build necessarily invalidates. Logged as a plan defect in the implementation checklist.

Run: ~/.athena/.venv/bin/python3 tests/ui_snapshot_check.py --record
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path before core is imported
from core import timefmt

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "css"
SHEETS = ("palette.css", "global.css", "page.css")      # link order: palette first
BASELINE = ROOT / "tests" / "fixtures" / "ui_snapshot.json"
MIN_RAW_LITERALS = 60      # pre-sweep the stylesheets hold 66; post-sweep, none
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
RGBA = re.compile(r"rgba?\(")
VAR = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(?:,\s*([^()]*))?\)")


def _strip_comments(text: str) -> str:
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def parse(text: str) -> list[tuple[str, str, str]]:
    """(selector, property, raw value) for every declaration, in document order.

    A tiny block parser rather than a dependency: the input is our own stylesheets, and the alternative is a package
    the install does not have.
    """
    text = _strip_comments(text)
    stack: list[str] = []
    pending: list[str] = []
    decls: list[tuple[str, str, str]] = []
    body: list[str] = []
    for char in text:
        if char == "{":
            stack.append(" ".join("".join(pending).split()))
            pending, body = [], []
            continue
        if char == "}":
            for chunk in "".join(body).split(";"):
                if ":" in chunk:
                    prop, _, value = chunk.partition(":")
                    decls.append((" ".join(stack), prop.strip(), value.strip()))
            stack.pop() if stack else None
            pending, body = [], []
            continue
        if stack:
            body.append(char)
        else:
            pending.append(char)
    if stack and body:                                   # a file that ends inside a block
        for chunk in "".join(body).split(";"):
            if ":" in chunk:
                prop, _, value = chunk.partition(":")
                decls.append((" ".join(stack), prop.strip(), value.strip()))
    return decls


def variables(sheets: dict[str, list[tuple[str, str, str]]]) -> dict[str, str]:
    """The dark map: `:root` declarations, merged in link order so a later sheet wins."""
    mapping: dict[str, str] = {}
    for name in SHEETS:
        for selector, prop, value in sheets.get(name, []):
            if prop.startswith("--") and ":root" in selector and "data-theme" not in selector:
                mapping[prop] = value
    return mapping


def resolve(value: str, mapping: dict[str, str], depth: int = 0) -> str:
    """Expand `var(--x)` / `var(--x, fallback)`. Returns "" when it cannot be resolved."""
    if depth > 8:
        return ""
    missing = False

    def swap(match: re.Match) -> str:
        nonlocal missing
        name, fallback = match.group(1), (match.group(2) or "").strip()
        if name in mapping:
            return mapping[name]
        if fallback:
            return fallback
        missing = True
        return ""

    out = VAR.sub(swap, value)
    if VAR.search(out) and not missing:
        return resolve(out, mapping, depth + 1)
    return "" if missing else out.strip()


def snapshot() -> tuple[dict[str, str], dict[str, int], int]:
    """(keys → resolved colour, per-file key counts, raw-literal declaration count).

    The third value is what makes a re-recorded baseline detectable without trusting timestamps: before the sweep
    `page.css`/`global.css` hold 66 declarations with a literal, afterwards they hold none, so a record with a literal
    count near zero was taken after the work it is supposed to guard.
    """
    sheets = {name: parse((WEB / name).read_text(encoding="utf-8"))
              for name in SHEETS if (WEB / name).is_file()}
    mapping = variables(sheets)
    found: dict[str, str] = {}
    counts: dict[str, int] = {}
    raw_literals = [0]
    for name in SHEETS:
        for selector, prop, value in sheets.get(name, []):
            # Custom properties are excluded and declarations are judged on their RESOLVED value, not their raw one.
            # Both halves are load-bearing, and both contradict the first version of this script:
            #
            #  * a raw-literal filter empties on the sweep — the whole point is that no declaration holds a literal
            #    afterwards — so the record it produces cannot be compared against anything;
            #  * including the tokens themselves means every token the palette introduces is an "added" key, so a
            #    refactor that changes no rendered colour reports a difference for each new name.
            #
            # What is actually asserted is: every declaration that resolves to a colour resolves to the same colour.
            # That set is stable across the sweep, and the tokens show up through it rather than beside it.
            if prop.startswith("--"):
                continue
            resolved = resolve(value, mapping) or value
            if not (HEX.search(resolved) or RGBA.search(resolved)):
                continue
            found[f"{name}|{selector}|{prop}"] = resolved
            if HEX.search(value) or RGBA.search(value):
                raw_literals[0] += 1
            counts[name] = counts.get(name, 0) + 1
    return found, counts, raw_literals[0]


def record(force: bool) -> int:
    found, counts, raw = snapshot()
    if BASELINE.is_file() and not force:
        print(f"{BASELINE} already exists — pass --force to overwrite the baseline", file=sys.stderr)
        return 2
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "recorded_at": timefmt.stamp(),
               "count": len(found), "raw_literals": raw, "declarations": dict(sorted(found.items()))}
    BASELINE.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    per_file = " · ".join(f"{name} {counts.get(name, 0)}" for name in SHEETS)
    print(f"recorded {len(found)} declarations ({per_file}; {raw} hold a literal) → {BASELINE.relative_to(ROOT)}")
    return 0


def check(allow_added: bool = False) -> int:
    try:
        payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    except OSError:
        print(f"no baseline at {BASELINE} — run --record first", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"baseline is not valid JSON: {exc}", file=sys.stderr)
        return 2
    baseline = payload.get("declarations")
    if payload.get("version") != 1 or not isinstance(baseline, dict):
        print("baseline is not version 1 with a declarations map", file=sys.stderr)
        return 2

    # exit 3: a record taken after the sweep — it holds almost no raw literals, because the sweep removed them all.
    raw = int(payload.get("raw_literals") or 0)
    if raw < MIN_RAW_LITERALS:
        print(f"baseline is not a pre-sweep record — {raw} declarations hold a literal (< {MIN_RAW_LITERALS})",
              file=sys.stderr)
        return 3

    current, _, _ = snapshot()
    keys = set(baseline) | set(current)
    differences = []
    for key in sorted(keys):
        before, after = baseline.get(key), current.get(key)
        if before is None:
            differences.append(f"+ {key}: {after}")
        elif after is None:
            differences.append(f"- {key}: {before}")
        elif after != before:
            differences.append(f"~ {key}: {before} → {after}")

    if allow_added:
        # A declaration that did not exist before is a new rule carrying a colour: expected while the redesign is
        # being built, and not a change to anything that already existed. Removals and changed values still count,
        # which is what keeps the check able to fail.
        differences = [line for line in differences if not line.startswith("+ ")]
    print(f"compared {len(baseline)} · differ {len(differences)}")
    for line in differences[:10]:
        print(f"  {line}")
    return 0 if not differences else 1


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0])
        print("usage: ui_snapshot_check.py --record [--force] | --check [--allow-added]")
        return 0 if argv else 2
    flag = argv[0]
    if flag == "--record":
        return record(force="--force" in argv[1:])
    if flag == "--check":
        return check(allow_added="--allow-added" in argv[1:])
    print(f"unknown flag {flag!r} — use --record [--force] or --check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

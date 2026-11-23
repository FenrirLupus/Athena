"""Write a curator proposal: a reviewable artefact, not a change.

A proposal is three files in one folder, and nothing is applied by writing them:

    documentation/optimization/proposals/<id>/PROPOSAL.md   what and why, for a human
    documentation/optimization/proposals/<id>/diff.patch    the change, as a unified diff
    documentation/optimization/proposals/<id>/verify.json   the hashes and the gate that decide "safe"

The id is derived from the title, so a second proposal about the same subject lands beside the first instead of
overwriting it — the history of a bad idea is worth keeping.
"""
from __future__ import annotations

import argparse
import hashlib

from curator_diff import unified  # noqa: F401  (imported for its side-effect-free contract)
import os
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import curator_diff

for _parent in Path(__file__).resolve().parents:            # the tree that holds core/
    if (_parent / "core" / "trace.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
from core import trace

LANE = "curator"


def _say(text: str = "") -> None:
    """The artefact path, which is this command's whole output. Diagnostics go through `trace`."""
    print(text, flush=True)


def _refuse(reason: str, **fields) -> None:
    trace.event("curator.refused", channel=LANE, reason=reason, **fields)


def slug(title: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
    return text or "proposal"


def tree_root(start: Path) -> Path:
    for parent in start.parents:
        if (parent / "tests" / "run_gate.sh").is_file():
            return parent
    raise SystemExit("cannot find the tree: no tests/run_gate.sh above here")


def resolve(candidate: str, root: Path) -> tuple[Path, Path]:
    """A curator target lives in the athena home — skills, tools, plugins, profiles. Never the program.

    The home is the only base. `athena-agent/` is the code the agent runs on: a curator that can rewrite it is a
    system that can edit the rules it is judged by, and the agent cannot even read a change back into effect.
    """
    home = root.parent.resolve()
    raw = Path(candidate)
    target = raw.resolve() if raw.is_absolute() else (home / raw).resolve()
    if str(target).startswith(str(root.resolve()) + os.sep):
        _refuse("target is inside the program tree", target=str(target))
        raise SystemExit(f"refused: {candidate} is inside the program tree (athena-agent/) — "
                         f"the curator governs the home: skills, tools, plugins and profiles")
    if not str(target).startswith(str(home) + os.sep):
        _refuse("target resolves outside the athena home", target=str(target))
        raise SystemExit(f"refused: {candidate} resolves outside the athena home")
    return target, home



def make_diff(target: Path, new_text: str, base: Path) -> str:
    """The patch, from the same function apply uses to check it."""
    return curator_diff.unified(target.read_text(encoding="utf-8"), new_text, str(target.relative_to(base)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write a curator proposal (writes no change to the target).")
    ap.add_argument("--title", required=True)
    ap.add_argument("--file", required=True, help="path of the file the proposal would change")
    ap.add_argument("--new", required=True, help="path holding the proposed new content")
    ap.add_argument("--rationale", required=True, help="why this change is worth making")
    ap.add_argument("--evidence", default="", help="the scan finding or measurement that justifies it")
    args = ap.parse_args(argv)

    with trace.span("curator.propose", channel=LANE, title=args.title, file=args.file,
                    evidence_chars=len(args.evidence or "")) as sp:
        root = tree_root(Path(__file__).resolve())
        target, base = resolve(args.file, root)
        if not target.is_file():
            _refuse("no such file", target=str(target))
            raise SystemExit(f"no such file: {target}")
        new_text = Path(args.new).read_text(encoding="utf-8")

        diff = make_diff(target, new_text, base)
        if not diff.strip():
            _refuse("the proposal changes nothing", target=args.file)
            raise SystemExit("the proposal changes nothing")

        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        folder = root.parent / "documentation" / "optimization" / "proposals" / f"{slug(args.title)}-{stamp}"
        folder.mkdir(parents=True, exist_ok=True)

        before = hashlib.sha256(target.read_bytes()).hexdigest()
        after = hashlib.sha256(new_text.encode("utf-8")).hexdigest()
        (folder / "diff.patch").write_text(diff, encoding="utf-8")
        # the change itself travels with the proposal: apply writes this file rather than shelling out to `patch`,
        # which is not installed everywhere and silently changes the safety story when it is missing.
        (folder / "content.new").write_text(new_text, encoding="utf-8")
        (folder / "content.old").write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        (folder / "verify.json").write_text(json.dumps({
            "file": str(target.relative_to(base)),
            "base": "tree" if base == root.resolve() else "home",
            "sha256_before": before, "sha256_after": after,
            "gate": "bash tests/run_gate.sh", "created_at": stamp, "format": 2,
            "note": "apply refuses if the file's hash no longer matches sha256_before",
        }, indent=2), encoding="utf-8")
        (folder / "PROPOSAL.md").write_text("\n".join([
            f"# {args.title}", "",
            f"- target: `{target.relative_to(base)}`",
            f"- created: {stamp}",
            f"- diff: `diff.patch` ({len(diff.splitlines())} lines)",
            "", "## Why", "", args.rationale.strip(), "",
            "## Evidence", "", (args.evidence.strip() or "none supplied — a proposal without evidence is a preference"), "",
            "## Apply", "", "```text",
            f"python skills/system/curator/scripts/apply.py {folder.name} --dry-run",
            f"python skills/system/curator/scripts/apply.py {folder.name} --yes",
            "```", "",
            "Nothing here has been applied. Apply verifies the hash, shows the diff, runs the gate, and restores on failure.",
        ]) + "\n", encoding="utf-8")
        sp.ok(folder=folder.name, target=args.file, diff_lines=len(diff.splitlines()),
              sha256_before=before[:12], sha256_after=after[:12], artifacts=sum(1 for _ in folder.iterdir()))
    _say(folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

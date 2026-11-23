"""Curator scan — free, deterministic detection of duplication and capability drift.

No model, no network, no writes outside the report folder. It answers three questions about the tree:

  1. Which blocks of code are the same, or nearly the same (>= 90 % similar)?
  2. Which capabilities exist (plugins, tools, skills), how big are they, and what do they claim to do?
  3. Where is the bulk — the files and functions that would cost the most to understand?

Near-duplicates are found by shingle overlap, not by pairwise comparison: every block is reduced to a set of
4-token shingles, an inverted index keeps only blocks that share a rare shingle, and only those pairs are scored.
That keeps the scan linear-ish on a tree this size instead of quadratic.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

for _parent in Path(__file__).resolve().parents:            # the tree that holds core/
    if (_parent / "core" / "trace.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
from core import trace

LANE = "curator"


def _say(text: str = "") -> None:
    """The operator's report. Diagnostics go through `trace`, never here."""
    print(text, flush=True)


def _project_root() -> Path:
    """The tree that holds core/ and server/ — found by walking up, so the script works from any depth."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "core").is_dir() and (parent / "server").is_dir():
            return parent
    return here.parents[1]


ROOT = _project_root()
OUT = ROOT.parent / "documentation" / "optimization" / "curator"   # beside the tree, not inside it
WINDOW = 10            # non-empty lines per block
STRIDE = 5             # overlap half a window, so a shifted copy is still caught
MIN_LINES = 6          # below this a match is a coincidence
SIMILARITY = 0.90      # the threshold the operator asked for
# Curator scope is the CAPABILITIES, not the program: plugins, tools, skills and the agents themselves.
# The Athena home is the runtime root — profiles/<lane>/ is an agent's own plugins, tools, skills and config.
def _athena_home(root: Path) -> Path:
    """The runtime root, found by what it holds rather than by depth.

    The previous version started from the script file itself, so in a layout where nothing matched it kept the
    *file* as the root and then globbed relative to it — which is how reports ended up being scanned as content.
    """
    for candidate in [root, *root.parents]:
        if (candidate / "skills").is_dir() and (candidate / "profiles").is_dir():
            return candidate
    return root.parent


ATHENA = _athena_home(ROOT)
CODE_DIRS = ("skills", "tools", "plugins")
SUFFIXES = (".md", ".py", ".js", ".yaml", ".yml", ".json")
# vendor code and runtime state are not capabilities: an agent's sandbox holds installed packages, sessions hold
# transcripts, and neither is something a curator should propose merging.
EXCLUDE = re.compile(r"(documentation/optimization|sessions|metrics|models|backups|graphify|staging|__pycache__|\.venv|sandbox|\.cache|"
                     r"node_modules|/voicebank/)")
SKIP = re.compile(r"(__pycache__|\.pyc$|node_modules|\.min\.|/graphify/)")


def vendored(path: Path, root: Path) -> bool:
    """True when the file sits inside a third-party checkout: a capability we installed, not one we own."""
    for parent in path.parents:
        if parent == root.parent:
            break
        if (parent / ".git").exists() or (parent / "setup.py").exists():
            return True
    return False


def blocks_of(path: Path) -> list[tuple[int, str]]:
    """Sliding windows of the file's substantive lines, with the line each window starts on."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    keep = [(i + 1, line.strip()) for i, line in enumerate(raw)
            if line.strip() and not line.strip().startswith(("#", "//", "*", "/*"))]
    out = []
    for start in range(0, max(0, len(keep) - MIN_LINES + 1), STRIDE):
        window = keep[start:start + WINDOW]
        if len(window) >= MIN_LINES:
            out.append((window[0][0], "\n".join(text for _, text in window)))
    return out


def shingles(text: str, size: int = 4) -> frozenset:
    tokens = re.findall(r"[A-Za-z_]\w*|\d+|[-+*/%=<>!]+", text)
    if len(tokens) < size:
        return frozenset(tokens)
    return frozenset(" ".join(tokens[i:i + size]) for i in range(len(tokens) - size + 1))


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def scan_duplicates() -> dict:
    index: dict[str, list[int]] = defaultdict(list)
    records: list[dict] = []
    roots = [ATHENA / part for part in CODE_DIRS] + sorted((ATHENA / "profiles").glob("*"))
    for base in roots:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or SKIP.search(str(path)) or EXCLUDE.search(str(path)):
                continue
            if path.suffix not in SUFFIXES or vendored(path, base):
                continue
            for line, text in blocks_of(path):
                sh = shingles(text)
                if len(sh) < 3:
                    continue
                idx = len(records)
                # minhash: the k smallest hashes of the shingle set. Similar blocks share their minima with high
                # probability, which sampling an arbitrary 24 shingles does not — that is why recall was zero.
                keys = sorted({hash(v) & 0xFFFFFFFF for v in sh})[:12]
                records.append({"file": str(path.relative_to(ATHENA)), "line": line, "text": text, "sh": sh,
                                "keys": keys})
                for key in keys:
                    index[key].append(idx)

    pairs, seen, closest = [], set(), []
    for ids in index.values():
        if len(ids) < 2 or len(ids) > 400:            # a bucket of 40+ blocks is boilerplate, not a signal
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                key = (ids[i], ids[j])
                if key in seen:
                    continue
                seen.add(key)
                a, b = records[ids[i]], records[ids[j]]
                if i == j:
                    continue                     # a block is not its own duplicate; identical *different* blocks are
                score = jaccard(a["sh"], b["sh"])
                if score < 0.35:                     # below this the pair is not worth the exact comparison
                    continue
                exact = SequenceMatcher(None, a["text"], b["text"]).ratio()
                entry = {"a": f"{a['file']}:{a['line']}", "b": f"{b['file']}:{b['line']}",
                         "similarity": round(exact, 3)}
                closest.append(entry)
                if exact >= SIMILARITY:
                    pairs.append(entry)
    pairs.sort(key=lambda p: -p["similarity"])
    closest.sort(key=lambda p: -p["similarity"])
    return {"blocks": len(records), "duplicate_pairs": len(pairs), "pairs": pairs[:120],
            "closest": closest[:15]}


def scan_capabilities() -> dict:
    out = {"skills": [], "tools": [], "plugins": []}
    # a tool is a TOOL.md pack — shared under <athena>/tools, or an agent's own under <profile>/tools.
    # The program's built-in tool modules live in athena-agent/tools and are outside curator scope.

    for base, kind, manifest in ((ATHENA / "skills", "skills", "SKILL.md"),
                                 (ATHENA / "tools", "tools", "TOOL.md"),
                                 (ATHENA / "plugins", "plugins", "PLUGIN.md"),
                                 *[((lane / "skills"), "skills", "SKILL.md") for lane in sorted((ATHENA / "profiles").glob("*")) if lane.is_dir()],
                                 *[((lane / "tools"), "tools", "TOOL.md") for lane in sorted((ATHENA / "profiles").glob("*")) if lane.is_dir()],
                                 *[((lane / "plugins"), "plugins", "PLUGIN.md") for lane in sorted((ATHENA / "profiles").glob("*")) if lane.is_dir()]):
        if not base.is_dir():
            continue
        for entry in sorted(base.rglob(manifest)):
            text = entry.read_text(encoding="utf-8", errors="replace")
            lines = len(text.splitlines())
            name = re.search(r"^name:\s*(.+)$", text, re.M)
            desc = re.search(r"^description:\s*(.+)$", text, re.M)
            out[kind].append({"name": (name.group(1).strip() if name else entry.parent.name),
                              "path": str(entry.relative_to(ATHENA)), "lines": lines,
                              "description": (desc.group(1).strip()[:110] if desc else "")})
    return out


def scan_bulk() -> dict:
    rows = []
    roots = [ATHENA / part for part in CODE_DIRS] + sorted((ATHENA / "profiles").glob("*"))
    for base in roots:
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if SKIP.search(str(path)) or EXCLUDE.search(str(path)) or not path.is_file():
                continue
            if path.suffix not in SUFFIXES or vendored(path, base):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            longest = max((len(part.splitlines()) for part in re.split(r"\ndef |\nfunction ", text)), default=0)
            rows.append({"file": str(path.relative_to(ATHENA)), "lines": len(text.splitlines()), "longest": longest})
    rows.sort(key=lambda r: -r["lines"])
    return {"files": len(rows), "largest": rows[:12]}


def main() -> int:
    with trace.span("curator.scan", channel=LANE, athena=str(ATHENA), out=str(OUT),
                    threshold=SIMILARITY, window=WINDOW, stride=STRIDE) as sp:
        dup = scan_duplicates()
        caps = scan_capabilities()
        bulk = scan_bulk()
        if dup["blocks"] == 0:
            # a scan that found nothing to scan is broken, not clean: a timer must fail loudly rather than
            # write an empty report that reads like "no findings".
            sp.failed(RuntimeError("no blocks were scanned — check the tree root and the scope"))
            _say("scan failed: no blocks were scanned — check the tree root and the scope")
            return 2
        OUT.mkdir(parents=True, exist_ok=True)
        stamp = __import__("time").strftime("%Y%m%dT%H%M%S", __import__("time").gmtime())
        payload = {"scanned_at": stamp, "duplicates": dup, "capabilities": caps, "bulk": bulk}
        (OUT / f"scan-{stamp}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

        lines = [f"# Curator scan — {stamp}", "",
                 f"- blocks compared: {dup['blocks']}",
                 f"- near-duplicate pairs at >= {int(SIMILARITY * 100)} %: {dup['duplicate_pairs']}",
                 f"- skills {len(caps['skills'])} · tools {len(caps['tools'])} · plugins {len(caps['plugins'])}", ""]
        if not dup["pairs"] and dup["closest"]:
            lines += ["## No block reaches 90 % — the closest pairs, for calibration", ""]
            for pair in dup["closest"][:10]:
                lines.append(f"- {pair['similarity']:.0%}  {pair['a']}  ↔  {pair['b']}")
            lines.append("")
        if dup["pairs"]:
            lines += ["## Duplicates", ""]
            for pair in dup["pairs"][:40]:
                lines.append(f"- {pair['similarity']:.0%}  {pair['a']}  ↔  {pair['b']}")
            lines.append("")
        lines += ["## Largest files", ""]
        for row in bulk["largest"]:
            lines.append(f"- {row['lines']:5} lines · longest block {row['longest']:4} · {row['file']}")
        lines.append("")
        lines += ["## Capabilities", ""]
        for kind, rows in caps.items():
            if rows:
                lines.append(f"### {kind}")
                for row in rows:
                    lines.append(f"- {row['name']} ({row['lines']} lines) — {row['description'] or 'no description'}")
                lines.append("")
        report = OUT / f"scan-{stamp}.md"
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        sp.ok(blocks=dup["blocks"], pairs=dup["duplicate_pairs"], closest=len(dup["closest"]),
              files=bulk["files"], skills=len(caps["skills"]), tools=len(caps["tools"]),
              plugins=len(caps["plugins"]), report=report.name)
    _say("\n".join(lines[:24]))
    _say(f"\nreport: {report}")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())

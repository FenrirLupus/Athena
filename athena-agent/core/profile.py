"""A profile's shape on disk — created once, then owned by the agent.

    profiles/<channel>/
        sessions/<channel>.jsonl      one continuous thread
        memories/MEMORY.md         the shared index (no frontmatter, one record per line)
        memories/<category>/<topic>/REFERENCE.md   one memory body: category = broad concept,
                                   topic = the specific memory. The body carries frontmatter
                                   name / category / topic / creation.
        assistant/ASSISTANT.md     who I am
        assistant/EMOTION.md       my current state
        user/USER.md               who I serve
        user/EMOTION.md            their current state

`ensure()` is idempotent: it creates what is missing and never overwrites a file that exists. A
profile is the agent's home, so scaffolding it must be safe to run on every boot.
"""
from __future__ import annotations

from pathlib import Path

ASSISTANT_TEMPLATE = """---
name_first: null
name_last: null
name_nick: null
gender: null
pronouns: null
voice: null
---

# Who I am

A persistent agent. I keep my own memory and my own thread, and I am the same agent tomorrow.
"""

USER_TEMPLATE = """---
name_first: null
name_last: null
name_nick: null
gender: null
pronouns: null
address_as: null
---

# Who I serve

Nothing recorded yet. Add what matters: how they want to be spoken to, and what they have asked
for more than once.
"""

EMOTION_TEMPLATE = """---
# Plutchik axes, each -1.00 .. +1.00, decaying toward 0.00 between turns.
joy: +0.00
trust: +0.00
fear: +0.00
surprise: +0.00
sadness: +0.00
disgust: +0.00
anger: +0.00
anticipation: +0.00
---

# Emotional state

Values are the current snapshot; the frontmatter is the only machine-read part.
"""

MEMORY_HEADER = """# Memory — the shared index

One record per line, three bracketed fields, newest last:

    [file: "REFERENCE.md"][location: "memories/self/<topic>/<slug>/"][note: "routing line"]

Bodies live at memories/<scope>/<topic>/<slug>/REFERENCE.md. Scopes: self, user, shared.
"""

SUMMARY_HEADER = """# Summary — what happened before the window
"""

LAYOUT = {
    # the digest is session-derived, so it lives with the session record rather than with memory
    "sessions/compression": {"SUMMARY.md": SUMMARY_HEADER},
    # one directory per dispatch: ARTIFACT.md + state.db + the artifacts it produced
    # (documentation/architecture/16-subagent-traces.md)
    "subagents": None,
    "memories": {"MEMORY.md": MEMORY_HEADER},
    # this agent's own turn measurements: turns.jsonl, appended once per turn (doc 21)
    "metrics": None,
    "assistant": {"ASSISTANT.md": ASSISTANT_TEMPLATE, "EMOTION.md": EMOTION_TEMPLATE},
    "user": {"USER.md": USER_TEMPLATE, "EMOTION.md": EMOTION_TEMPLATE},
    # Where work happens. These two are not optional: a profile without them has nowhere to test a script
    # (`sandbox/`) and nowhere to keep the user's files (`workspace/`), and the scope guard points at both.
    "sandbox": None,
    "workspace": None,
}


def ensure(root: Path) -> dict:
    """Create the profile layout under `root`. Returns what was created."""
    created = []
    for name, files in LAYOUT.items():
        d = root / name
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d.relative_to(root)) + "/")
        for filename, template in (files or {}).items():
            f = d / filename
            if not f.exists():
                f.write_text(template, encoding="utf-8")
                created.append(str(f.relative_to(root)))
    return {"root": str(root), "created": created}


# -- readers used by the prompt stack ---------------------------------------
def _split_front(text: str) -> tuple[str, str]:
    """(frontmatter, body) — frontmatter returns '' when absent."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[1], parts[2]
    return "", text


def read_identity(path: Path) -> str:
    """The markdown body of an identity card, without its frontmatter or its own H1.

    The H1 is dropped because the prompt stack supplies the section header; keeping both printed
    "# Who I am" twice in the assembled system block.
    """
    try:
        front, body = _split_front(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    lines = body.strip().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].lstrip().startswith("# "):
        lines.pop(0)
    return "\n".join(lines).strip()


def read_emotion_line(path: Path) -> str:
    """The eight axes as one compact line, or '' when the file is absent or all zero."""
    try:
        front, _ = _split_front(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    vals = []
    for line in front.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        try:
            num = float(val.strip())
        except ValueError:
            continue
        vals.append((key.strip(), num))
    if not vals or all(abs(v) < 0.005 for _, v in vals):
        return ""
    return " · ".join(f"{k} {v:+.2f}" for k, v in vals)


def read_memory_rows(path: Path) -> list[str]:
    """Index records, one per line, as written. Blank lines and non-records are skipped."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        # A record is a BARE line. Indented lines are documentation — the seed header's example
        # record was being parsed as a real memory pointing at memories/self/<topic>/<slug>/,
        # which does not exist and got injected into the prompt as a dead pointer.
        if not line or line != line.lstrip():
            continue
        s = line.strip()
        if s.startswith("[file:") and "[location:" in s and "[note:" in s:
            out.append(s)
    return out

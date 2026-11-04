"""Instruction documents — full procedures kept in the program tree, sent when their trigger fires.

A skill is *knowledge an agent may consult*. These are different: a **required procedure**, owned by the system, kept
where the agent cannot edit it, and handed to the agent automatically when the situation calls for it. The agent does
not choose to load `github_install.md`; giving it a repository URL does.

    athena-agent/instructions/<name>.md

```markdown
---
name: github_install
trigger: ["github.com/", ".git", "install this repo"]
priority: 20
---

# GitHub repository intake and installation
...steps...
```

Why in the program tree: an instruction that an agent can rewrite is not an instruction. `athena-agent/` is read-only
to agents (it is `ATHENA_SYSTEM`), so these documents are the one place a procedure can live and still mean something.

Selection is deliberately dumb — case-insensitive substring on the user's text, highest priority first, at most
`MAX_SENT` documents. A clever selector that guesses would attach the wrong procedure at the wrong time; a literal
one is predictable, and predictable is what a procedure needs.
"""
from __future__ import annotations

import re
from pathlib import Path

FOLDER = Path(__file__).resolve().parent.parent / "instructions"
MAX_SENT = 2


def _parse(path: Path) -> dict:
    """`---` header plus body. `trigger` is a JSON-ish list or a single string; no YAML dependency."""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta: dict = {"name": path.stem, "trigger": [], "priority": 0, "body": text}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            meta["body"] = text[end + 4:].lstrip("\n")
            pending_key = None
            for line in text[3:end].splitlines():
                if ":" not in line:
                    # a wrapped list: append to the key it belongs to, or it is silently dropped
                    if pending_key == "trigger" and line.strip():
                        meta["trigger"] += _trigger_items(line.strip())
                    continue
                key, _, value = line.partition(":")
                key, value = key.strip().lower(), value.strip()
                if key == "trigger":
                    meta["trigger"] = _trigger_items(value)
                    pending_key = "trigger"
                elif key == "priority":
                    try:
                        meta["priority"] = int(value)
                    except ValueError:
                        meta["priority"] = 0
                elif key == "name" and value:
                    meta["name"] = value.strip('"').strip("'")
    meta["path"] = str(path)
    return meta


def _trigger_items(value: str) -> list[str]:
    raw = value.strip().strip("[]")
    items = [part.strip().strip('"').strip("'") for part in raw.split(",")]
    return [item for item in items if item]


def discover(folder: Path | None = None) -> list[dict]:
    """Every instruction document on disk, highest priority first."""
    base = Path(folder) if folder else FOLDER
    if not base.is_dir():
        return []
    docs = []
    for path in sorted(base.glob("*.md")):
        try:
            docs.append(_parse(path))
        except OSError:
            continue
    return sorted(docs, key=lambda d: (-int(d.get("priority") or 0), d["name"]))


def _matches(trigger: str, text: str, lowered: str) -> bool:
    """A trigger is a literal substring, or a regex when written `re:<pattern>`.

    Literals are enough for words ("uninstall"); a URL needs a pattern, or `.git` matches the word "digit" and
    `github.com/` matches a sentence about GitHub rather than a link to a repository.
    """
    if trigger.lower().startswith("re:"):
        try:
            return re.search(trigger[3:], text, re.I) is not None
        except re.error:
            return False
    return trigger.lower() in lowered


def select(text: str, folder: Path | None = None, limit: int = MAX_SENT) -> list[dict]:
    """The documents whose triggers fire on `text`. Priority order, capped."""
    source = text or ""
    if not source.strip():
        return []
    lowered = source.lower()
    hits = []
    for doc in discover(folder):
        for trigger in doc["trigger"]:
            if _matches(trigger, source, lowered):
                hits.append(doc)
                break
        if len(hits) >= limit:
            break
    return hits


def render(text: str, folder: Path | None = None, limit: int = MAX_SENT) -> str:
    """The matched documents as one block to append to an agent's instructions — or "" when nothing matches."""
    chunks = []
    for doc in select(text, folder, limit):
        body = doc["body"].strip()
        if body:
            chunks.append(f"# Required procedure: {doc['name']}\n\n{body}")
    return "\n\n---\n\n".join(chunks)

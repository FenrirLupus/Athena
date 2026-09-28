"""The workflow registry — the GLOBAL shared workflows (like tools/plugins/skills).

The Operator's 08-15 spec: workflows are house-level files under
workflows/*.md — ONE file per workflow, the Header > Body > Footer
schema with YAML frontmatter inside:

    ---                 ← HEADER (YAML frontmatter)
    name: conversation
    description: ...
    requirements:
      - label: reply_present
        description: ...
        completed: false
    ---
    # Body             ← BODY (the doctrine: ## N.0 sections, ### N.N
                         steps with THE RULE/WHY/FAILURE/EXIT)
    ---
    # Footer           ← FOOTER (the checklist summary)

The full .md document loads into the System section of the prompt (the
LLM reads its own contract). The registry parses the frontmatter
(machine variables) + the body (the doctrine). Legacy .yaml files are
still read (backward compatibility).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

# The shared home — the house-level workflows root (like tools/plugins/
# skills): ATHENA_ROOT/workflows/. The profiles' workflows/ dirs are
# symlinks to this same shared home.
try:
    from core.config import SHARED_WORKFLOWS
    _WORKFLOWS_DIR = Path(SHARED_WORKFLOWS)
except Exception:
    _WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent / "workflows"

# The fallback when the selection is missing/unrecognized.
DEFAULT_WORKFLOW = "conversation"

# The chain guard: max hops + no self-continuation.
MAX_CHAIN_HOPS = 10

# THE STICKY WORKFLOW STATE (the CEO's 08-15 correction): the workflow
# selected for a session persists across turns — a roleplay scene stays
# roleplay, a code thread stays Programmer. Keyed by session_id.
_STICKY: dict[str, str] = {}


def sticky_workflow(session_id: str) -> Optional[str]:
    return _STICKY.get(session_id or "")


def set_sticky_workflow(session_id: str, name: str) -> None:
    if session_id:
        _STICKY[session_id] = name


def clear_sticky_workflow(session_id: str) -> None:
    _STICKY.pop(session_id or "", None)

_FRONT = re.compile(r"^---\s*\n(.*?)\n---", re.S | re.M)


def _workflows_dir() -> Path:
    return _WORKFLOWS_DIR


def _parse_md(text: str) -> Optional[dict]:
    """Parse ONE .md workflow file: frontmatter (YAML) + body + footer.

    Returns {name, description, when, safety, requirements, sections_text,
    doc} — sections_text is the rendered ## doctrine for the CONTINUE
    prompt; doc is the FULL file for the System-section load.
    """
    m = _FRONT.search(text)
    if not m:
        return None
    try:
        front = yaml.safe_load(m.group(1)) or {}
    except Exception:
        front = {}
    if not isinstance(front, dict) or not front.get("name"):
        return None
    reqs = []
    for r in front.get("requirements") or []:
        if isinstance(r, dict) and r.get("label"):
            reqs.append({
                "label": str(r["label"]),
                "description": str(r.get("description", "")),
                "completed": bool(r.get("completed", False)),
            })
    # The BODY — the part between the frontmatter close and the footer
    # delimiter. The ## doctrine lines become the sections_text.
    body = text[m.end():]
    if "---" in body:
        body = body.split("---", 1)[0]
    body_lines = [ln.rstrip() for ln in body.splitlines()
                  if ln.strip() and not ln.strip().startswith("# Footer")]
    return {
        "name": str(front["name"]),
        "description": str(front.get("description", "")),
        "when": str(front.get("when", "")),
        "safety": str(front.get("safety", "")),
        "requirements": reqs,
        "sections_text": "\n".join(body_lines),
        "doc": text,
    }


def _parse_yaml(text: str) -> Optional[dict]:
    """Legacy .yaml workflow files (backward compatible)."""
    try:
        d = yaml.safe_load(text) or {}
    except Exception:
        return None
    if not isinstance(d, dict) or not d.get("name"):
        return None
    reqs = []
    for r in d.get("requirements") or []:
        if isinstance(r, dict) and r.get("label"):
            reqs.append({
                "label": str(r["label"]),
                "description": str(r.get("description", "")),
                "completed": bool(r.get("completed", False)),
            })
    _sec_lines = []
    for num, sec in sorted((d.get("sections") or {}).items(), key=lambda x: str(x[0])):
        if not isinstance(sec, dict):
            continue
        _sec_lines.append(f"## {num} {sec.get('title', '')}".strip())
        subs = sec.get("subsections") or {}
        for sub in sorted(subs.keys(), key=lambda x: str(x)):
            _sec_lines.append(f"### {sub} {subs[sub]}".strip()
                              if isinstance(subs[sub], str) else f"### {sub}")
    return {
        "name": str(d["name"]),
        "description": str(d.get("description", "")),
        "when": str(d.get("when", "")),
        "safety": str(d.get("safety", "")),
        "requirements": reqs,
        "sections_text": "\n".join(_sec_lines),
        "doc": text,
    }


def _load_file(path: Path) -> Optional[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    if path.suffix.lower() == ".md":
        return _parse_md(text)
    return _parse_yaml(text)


def list_workflows() -> list[dict]:
    """Every registered workflow: {name, description} — for the START prompt."""
    out = []
    for f in sorted(_workflows_dir().glob("*.md")) + sorted(_workflows_dir().glob("*.yaml")):
        d = _load_file(f)
        if d:
            out.append({"name": d["name"], "description": d["description"]})
    return out


def workflow_names() -> list[str]:
    return [w["name"] for w in list_workflows()]


def load_workflow(name: str) -> Optional[dict]:
    """Load one workflow by name (None when missing)."""
    name = (name or "").strip().lower()
    for f in list(_workflows_dir().glob("*.md")) + list(_workflows_dir().glob("*.yaml")):
        d = _load_file(f)
        if d and d["name"].lower() == name:
            return d
    return None


def select_workflow(selection: Optional[str]) -> dict:
    """Resolve the model's selection — fallback = the default workflow."""
    wf = load_workflow(selection or "") if selection else None
    if wf is None:
        wf = load_workflow(DEFAULT_WORKFLOW) or {}
    return wf


def requirements_of(wf: dict) -> list[dict]:
    """The workflow's requirement checklist (label/description/completed)."""
    return [dict(r) for r in (wf.get("requirements") or [])]


def sections_text(wf: dict) -> str:
    """The workflow's doctrine body for the CONTINUE prompt."""
    return wf.get("sections_text", "")


def workflow_doc(wf: dict) -> str:
    """The FULL .md document — loaded into the System section of the
    prompt builder (the Operator's 08-15 spec: the workflow document is
    the basis and baseline of the LLM call, human-readable + carrying its
    machine variables)."""
    return wf.get("doc", "")


def selection_prompt() -> str:
    """The START call's workflow-choice prompt (the lanes + one line each).

    THE 08-15 CREATION RULE (the Operator's spec): the operator may ask to
    CREATE a workflow/skill/tool directly — honor it (the programmer/
    learning/writer lanes build it), pushing back only when it already
    exists. And when NONE of the built-in workflows match the operator's
    ask, offer to create a custom workflow that does.
    """
    lanes = list_workflows()
    lines = [
        "Which workflow applies to this task? Respond with ONLY the workflow name.",
        "Available workflows:",
    ]
    for w in lanes:
        lines.append(f"- {w['name']}: {w['description']}")
    lines.append(
        "CREATION RULE: if the operator asks to create a workflow, skill, "
        "or tool directly, HONOR it (the programmer/learning/writer lanes "
        "build it) — only push back when it already exists. If NONE of the "
        "built-in workflows match the operator's ask, offer to create a "
        "custom workflow that does."
    )
    return "\n".join(lines)

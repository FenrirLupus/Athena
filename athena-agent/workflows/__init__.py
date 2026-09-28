"""Workflows — the gated step-by-step process system (the Operator's 08-12 spec).

A WORKFLOW is a 10-step .md file that holds the LLM's hand through a
process pipeline. Each step is a GENERAL process (a section) with
subsections of DO/DON'T instructions on safely implementing changes to
files, folders, or equivalents.

WHY: the nurse + janitor need deterministic, reviewable paths to run
autonomously. Instead of free-form LLM reasoning (which is why the
repair/optimize gates exist), the agent follows the workflow step by
step — each step gates the next, and each step's evidence lands in the
metrics stream + kanban (the audit trail). The operator can flip a
workflow's safety from report-only to auto one rung at a time.

The RUNNER:
  - load(name)        → the parsed workflow (frontmatter + 10 steps)
  - validate(wf)      → every workflow has exactly 10 steps with names
  - run(name, fn)     → execute the workflow, calling fn(step) per step
                        (the agent supplies the step behavior); every
                        step transition is logged to the metrics stream
  - record_step(wf, i) → audit: which step, when, source
"""

from __future__ import annotations

import re
from pathlib import Path

# The workflows live in TWO places (the Operator's 08-12 workflow spec):
#   - .athena/workflows/          — USER workflows (created at boot like
#                                   skills; wiped with the tree)
#   - athena-system/workflows/    — CORE workflows (survive wipes; part
#                                   of the system, like skills in the core)
# The loader searches the USER dir FIRST (a user override wins), then
# the CORE dir.
CORE_WORKFLOWS_DIR = Path(__file__).parent


def _user_workflows_dir() -> Path:
    """The SHARED user-workflows home (.athena/workflows) — the same
    model as plugins/tools/skills: every profile's workflows symlink
    points here (the Operator's 08-12 spec)."""
    try:
        from core.config import SHARED_WORKFLOWS
        d = SHARED_WORKFLOWS
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        try:
            from core.config import ATHENA_ROOT
            d = ATHENA_ROOT / "workflows"
            d.mkdir(parents=True, exist_ok=True)
            return d
        except Exception:
            return CORE_WORKFLOWS_DIR


def _search_dirs() -> list[Path]:
    dirs = [_user_workflows_dir(), CORE_WORKFLOWS_DIR]
    seen = []
    for d in dirs:
        if d not in seen:
            seen.append(d)
    return seen


REQUIRED_STEPS = 10


class WorkflowError(Exception):
    """A workflow that cannot be loaded or validated."""


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter (--- ... ---) from the body. Best-effort."""
    body = text
    meta: dict = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if m:
        raw = m.group(1)
        for line in raw.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip("\"'")
        body = text[m.end():]
    return meta, body


def _parse_steps(body: str) -> list[dict]:
    """Extract the workflow structure.

    The PIPELINE-GATE model (the Operator's 08-12 spec):
      - SECTIONS are the major gates: `## N.0 Name` (the 10 gates —
        the section title STARTS with a number ending in .0)
      - STEPS are the minor keys: `### N.M Label` (the sub-instructions
        INSIDE each gate — 1.1, 1.2, ... within section 1.0)
      - Both carry bulleted DO/DON'T guidance.

    Returns [{order, name, steps: [{order, name, body}], body}].
    """
    import re as _re
    sections = []
    cur_sec = None
    cur_step = None
    cur_lines = []
    for line in body.splitlines():
        sm = _re.match(r"^##\s+(\d+)\.(\d+)\s+(.+)$", line.strip())
        if sm:
            if cur_step is not None:
                cur_step["body"] = "\n".join(cur_lines).strip()
                if cur_sec is not None:
                    cur_sec["steps"].append(cur_step)
                cur_lines = []
            if cur_sec is not None:
                cur_sec["body"] = "\n".join(cur_lines).strip()
                sections.append(cur_sec)
                cur_lines = []
            cur_sec = {"order": int(sm.group(1)), "name": sm.group(3).strip(),
                       "steps": [], "body": ""}
            cur_step = None
            continue
        stm = _re.match(r"^###\s+(\d+)\.(\d+)\s+(.+)$", line.strip())
        if stm and cur_sec is not None:
            if cur_step is not None:
                cur_step["body"] = "\n".join(cur_lines).strip()
                cur_sec["steps"].append(cur_step)
                cur_lines = []
            cur_step = {"order": int(stm.group(2)),
                        "name": stm.group(3).strip(), "body": ""}
            continue
        if cur_sec is not None:
            cur_lines.append(line)
    if cur_step is not None:
        cur_step["body"] = "\n".join(cur_lines).strip()
        if cur_sec is not None:
            cur_sec["steps"].append(cur_step)
        cur_lines = []
    if cur_sec is not None:
        cur_sec["body"] = "\n".join(cur_lines).strip()
        sections.append(cur_sec)
    return sections


def load(name: str) -> dict:
    """Load + parse a workflow by name (user dir first, then core)."""
    for d in _search_dirs():
        path = d / f"{name}.md"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(text)
            steps = _parse_steps(body)
            return {"name": name, "meta": meta, "steps": steps,
                    "path": str(path), "source": "user" if d == _user_workflows_dir() else "core"}
    raise WorkflowError(
        f"workflow '{name}' not found in "
        + " or ".join(str(d) for d in _search_dirs()))


def validate(wf: dict) -> list[str]:
    """Errors when the workflow isn't a proper 10-gate pipeline:
    exactly 10 SECTIONS (## N.0 — the gate title starts with a number
    ending in .0), each with ≥1 numbered STEP (### N.M), each step
    carrying DO/DON'T guidance (the Operator's 08-12 spec)."""
    errors = []
    sections = wf.get("steps", [])
    if len(sections) != REQUIRED_STEPS:
        errors.append(f"expected {REQUIRED_STEPS} sections, got {len(sections)}")
    orders = [s["order"] for s in sections]
    if orders != list(range(1, REQUIRED_STEPS + 1)):
        errors.append(f"section order must be 1..{REQUIRED_STEPS}: {orders}")
    for s in sections:
        steps = s.get("steps") or []
        if not steps:
            errors.append(f"section {s['order']}.0 ({s['name']}) has no steps")
            continue
        # minor-step order: 1..N within the section (the N.M keys)
        if [x["order"] for x in steps] != list(range(1, len(steps) + 1)):
            errors.append(
                f"section {s['order']}.0 step order must be .1..{len(steps)}")
        for st in steps:
            body = st.get("body", "")
            if not body.strip():
                errors.append(
                    f"step {s['order']}.{st['order']} ({st['name']}) has no body")
            # THE MASTER-GRADE CONTRACT (the Operator's 08-12 spec): a
            # workflow step is an EXPERIENCE-TRANSFER artifact — it must
            # carry the master's full kit: the RULE (what to do), the WHY
            # (the master's reasoning/consequence), the FAILURE (what goes
            # wrong + the apprentice tell), and the EXIT (how the gate is
            # known passed). A step missing any part is NOT master-grade.
            for marker in ("THE RULE", "THE WHY", "THE FAILURE", "THE EXIT"):
                if marker not in body:
                    errors.append(
                        f"step {s['order']}.{st['order']} ({st['name']}) "
                        f"missing {marker}")
            # THE DON'T SUBSECTION: a MASTER-GRADE step carries the
            # negative guidance inside THE FAILURE (the apprentice tell +
            # the consequence) — that satisfies the guardrail. A step
            # with a literal "DON'T" also passes. Only a step with
            # NEITHER is rejected.
            if ("DON'T" not in body and "DO NOT" not in body
                    and "THE FAILURE" not in body):
                errors.append(
                    f"step {s['order']}.{st['order']} ({st['name']}) "
                    f"lacks negative guidance")
    return errors


def record_step(wf: dict, section: dict, step: dict | None = None, *,
                source: str = "workflow", note: str = "") -> None:
    """Audit: every gate transition lands in the metrics stream (the
    Operator's spec — a silent workflow is a lying workflow)."""
    try:
        from core.logging import log_event
        where = f"section {section['order']} {section['name']}"
        if step is not None:
            where += f" / step {section['order']}.{step['order']} {step['name']}"
        log_event(
            2, f"workflow '{wf['name']}' gate: {where}"
               f"{(' — ' + note) if note else ''}",
            source=source, tool="workflow", action="workflow_gate",
            target=wf["name"])
    except Exception:
        pass


def run(name: str, fn, *, source: str = "workflow") -> dict:
    """Execute a workflow. fn(section, ctx) is called per SECTION gate IN
    ORDER; the agent supplies the behavior (running the section's minor
    steps inside). A section that raises stops the run (the gate holds).
    Returns the audit report."""
    wf = load(name)
    errors = validate(wf)
    if errors:
        raise WorkflowError("; ".join(errors))
    report = {"workflow": name, "gates": [], "completed": False}
    ctx: dict = {}
    for section in wf["steps"]:
        try:
            result = fn(section, ctx)
        except Exception as exc:
            record_step(wf, section, source=source,
                        note=f"STOPPED: {exc}")
            report["gates"].append({"order": section["order"],
                                    "name": section["name"],
                                    "steps": len(section.get("steps") or []),
                                    "status": "stopped",
                                    "error": str(exc)})
            report["completed"] = False
            return report
        record_step(wf, section, source=source,
                    note=result if isinstance(result, str) else "")
        report["gates"].append({"order": section["order"],
                                "name": section["name"],
                                "steps": len(section.get("steps") or []),
                                "status": "done"})
    report["completed"] = True
    return report


def list_workflows() -> list[str]:
    """The available workflow names — user dir + core dir, deduped (a
    user override hides the core copy)."""
    names = []
    seen = set()
    for d in _search_dirs():
        for p in sorted(d.glob("*.md")):
            if p.stem not in seen:
                names.append(p.stem)
                seen.add(p.stem)
    return sorted(names)

"""The workflow system contract — the Operator's 08-15 spec:

- The GLOBAL registry carries the 10 workflows (conversation, programmer,
  researcher, learning, teaching, roleplay, writer, strategist, counselor,
  auditor) — distinct names, no duplicates.
- Each workflow has sections (max 10 subsections per section: 1.0-1.9)
  and requirements (label + description + completed).
- The START selection resolves a model choice with a conversation fallback.
- The lifecycle: START (select) > CONTINUE (sections + requirements) >
  STOP (respond or chain to a different workflow).
"""
from __future__ import annotations


def run() -> list[dict]:
    from workflows.registry import (list_workflows, load_workflow,
                                    select_workflow, requirements_of,
                                    sections_text, selection_prompt,
                                    MAX_CHAIN_HOPS, DEFAULT_WORKFLOW)
    checks = []

    lanes = list_workflows()
    names = [w["name"] for w in lanes]
    checks.append({
        "name": "registry: the 10 global workflows",
        "status": "ok" if len(lanes) >= 10 else "fail",
        "detail": f"{len(lanes)} lanes: {', '.join(names[:10])}",
    })
    checks.append({
        "name": "registry: names distinct (no duplicates)",
        "status": "ok" if len(names) == len(set(names)) else "fail",
        "detail": f"dupes: {[n for n in set(names) if names.count(n) > 1] or 'none'}",
    })
    checks.append({
        "name": "registry: core lanes present",
        "status": "ok" if {"conversation", "programmer", "researcher",
                           "learning", "teaching", "roleplay",
                           "writer", "strategist", "counselor", "auditor"}
        <= set(names) else "fail",
        "detail": "the 10 confirmed lanes",
    })

    # Every workflow parses + carries sections + requirements.
    bad = []
    for lane in lanes:
        wf = load_workflow(lane["name"])
        if not wf:
            bad.append(lane["name"] + ":no-file")
            continue
        if not sections_text(wf):
            bad.append(lane["name"] + ":no-sections")
        reqs = requirements_of(wf)
        if not reqs:
            bad.append(lane["name"] + ":no-requirements")
        for r in reqs:
            if not r["label"] or not r["description"] or r["completed"] is not False:
                bad.append(lane["name"] + ":" + str(r.get("label")))
                break
    checks.append({
        "name": "workflow files parse + carry sections/requirements",
        "status": "ok" if not bad else "fail",
        "detail": "; ".join(bad) or f"{len(lanes)} workflows complete",
    })

    # The START selection: valid pick + fallback.
    sel = select_workflow("programmer")
    checks.append({
        "name": "START selection resolves a valid workflow",
        "status": "ok" if (sel or {}).get("name") == "programmer" else "fail",
        "detail": (sel or {}).get("name", "none"),
    })
    fb = select_workflow("nonexistent")
    checks.append({
        "name": "START fallback = the default workflow",
        "status": "ok" if (fb or {}).get("name") == DEFAULT_WORKFLOW else "fail",
        "detail": (fb or {}).get("name", "none"),
    })
    prompt = selection_prompt()
    checks.append({
        "name": "selection prompt lists the lanes",
        "status": "ok" if "conversation" in prompt and "programmer" in prompt else "fail",
        "detail": "the START prompt carries the lane list",
    })
    checks.append({
        "name": "chain guard: max hops bounded",
        "status": "ok" if isinstance(MAX_CHAIN_HOPS, int) and MAX_CHAIN_HOPS > 0 else "fail",
        "detail": f"max_hops={MAX_CHAIN_HOPS}",
    })
    return checks

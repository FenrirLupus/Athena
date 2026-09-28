"""Curator — the learn-by-doing brain (session.db + events → skills).

Athena's curator is FACTUAL: it reads what actually happened and turns
repeated experience into durable knowledge. Two sources, both already
built:

    1. session.db   — the conversation record (what was asked/done)
    2. events.log   — the per-agent activity record (tools used, outcomes)

The curator's doctrine (the learn-by-doing doctrine):
    - repeated SUCCESS  → becomes a skill (codify what worked)
    - repeated FRICTION → becomes a fix (patch the skill/docs)
    - unused knowledge  → becomes archive (stale → archived)
    - 80%-similar skills → merged (one home, no duplicates)

It evaluates skills PRIMARILY and plugins SECONDARILY — never the
the reference core. It creates NEW skills from factual evidence, it does
not invent. Provider calls happen ONLY for the review/authoring steps;
scanning is free.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from core.config import ATHENA_ROOT, DEFAULT_PROFILE_ROOT
from intelligence.sections import parse_sections

SKILLS_DIR = ATHENA_ROOT / "skills"
ARCHIVE_DIR = ATHENA_ROOT / "skills" / ".archive"
# The curator is an OPERATION: its state lives in operations/ as a single
# JSON file (the Operator's spec — one file per operation, applicable format).
STATE_PATH = DEFAULT_PROFILE_ROOT / "operations" / "curator.json"

# How many repeated uses make a pattern "proven" (learn-by-doing threshold).
PROVEN_THRESHOLD = 3
SIMILARITY_THRESHOLD = 0.8  # merge candidates at 80% similarity


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- Sources -------------------------------------------------------------

def _session_messages(profile: str = "", limit: int = 2000) -> list[dict]:
    """Pull messages from the session.db (the conversation record)."""
    from core import db as db_layer
    try:
        sid = db_layer.find_last_session(profile=profile)
        if not sid:
            return []
        return db_layer.get_session_history(sid, limit=limit, profile=profile)
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"curator session read failed: {exc}", source="curator",
                  action="session_messages")
        return []


def _event_entries(profile: str = "", limit: int = 2000) -> list[dict]:
    """Pull events from the agent activity log (the usage record)."""
    from metrics.events import read_events
    try:
        return read_events(profile, limit=limit)
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"curator events read failed: {exc}", source="curator",
                  action="event_entries")
        return []


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            from core.logging import log_event
            log_event(3, f"curator state read failed: {exc}", source="curator",
                      action="load_state")
    return {"scans": 0, "skills_created": [], "skills_merged": [], "last_scan": ""}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


# -- Skills inventory ----------------------------------------------------

def _load_skills() -> list[dict]:
    """Every skill: {name, path, description, version, last_modified}."""
    skills = []
    if not SKILLS_DIR.exists():
        return skills
    for skill_md in sorted(SKILLS_DIR.rglob("SKILL.md")):
        rel = skill_md.relative_to(SKILLS_DIR)
        if ".archive" in rel.parts:
            continue
        name = rel.parts[0]
        desc = ""
        m = re.search(r"description:\s*(.+)", skill_md.read_text(encoding="utf-8",
                                                                 errors="replace")[:800])
        if m:
            desc = m.group(1).strip()
        skills.append({
            "name": name,
            "path": str(skill_md),
            "description": desc,
            "version": _skill_version(skill_md),
            "last_modified": skill_md.stat().st_mtime,
        })
    return skills


def _skill_version(path: Path) -> str:
    m = re.search(r"version:\s*([\d.]+)", path.read_text(encoding="utf-8",
                                                         errors="replace")[:800])
    return m.group(1) if m else "0.1.0"


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# -- Scan (factual, free) ------------------------------------------------

def scan(profile: str = "") -> dict:
    """Read the sources and build the factual picture.

    Returns:
        {
          tools_used: {tool: count},          from events + session.db
          skills_used: {skill: count},        from events + session.db
          friction: [{tool, pattern, count}], repeated errors
          session_messages: n,
          skills: [inventory],
        }
    """
    events = _event_entries(profile)
    messages = _session_messages(profile)

    tools_used: dict[str, int] = {}
    skills_used: dict[str, int] = {}
    friction: dict[str, dict] = {}

    for e in events:
        tool = e.get("tool", "")
        status = e.get("status", "")
        if not tool:
            continue
        is_error = (
            "error" in str(e.get("result", "")).lower()
            or "denied" in str(e.get("result", "")).lower()
            or "fail" in str(e.get("result", "")).lower()
            or status in ("ERROR", "WARNING")
        )
        if is_error:
            # Friction is grouped by TOOL (repeated errors on the same
            # tool), not by the exact result text — different error
            # messages for the same failing tool are the SAME friction.
            f = friction.setdefault(tool, {"tool": tool, "pattern": "", "count": 0})
            f["count"] += 1
            if not f["pattern"]:
                f["pattern"] = str(e.get("result", ""))[:120]
        elif status in ("GOOD", "SUCCESS"):
            tools_used[tool] = tools_used.get(tool, 0) + 1

    # The session.db is a SECOND usage source (the Operator's 08-12
    # spec: tend by usage + results from session.db AND applicable
    # logs): every message carries tool_call/skill_call + usage tokens.
    for m in messages:
        if isinstance(m, dict):
            tc = m.get("tool_call")
            sc = m.get("skill_call")
            if tc:
                tools_used[str(tc)] = tools_used.get(str(tc), 0) + 1
            if sc:
                skills_used[str(sc)] = skills_used.get(str(sc), 0) + 1

    return {
        "tools_used": tools_used,
        "skills_used": skills_used,
        "friction": sorted(friction.values(), key=lambda x: -x["count"]),
        "session_messages": len(messages),
        "skills": _load_skills(),
        "profile": profile or "default",
    }


# -- Candidate signals (what to learn) ------------------------------------

def candidates(report: dict) -> dict:
    """The learn-by-doing candidates, from FACTUAL evidence.

    - skill_candidates: tools used >= PROVEN_THRESHOLD with no existing
      skill covering them (repeated success → codify).
    - friction_candidates: patterns seen >= PROVEN_THRESHOLD (repeated
      friction → fix).
    - merge_candidates: skills >= SIMILARITY_THRESHOLD (duplicates → one).
    - stale_candidates: skills untouched for a long time (→ archive).
    - tool_candidates: TOOL-tending (the Operator's 08-12 spec — the
      curator tends tools too, like the janitor but for tools/skills):
        unused_tools     — registered tools with ZERO activity (→ flag)
        duplicate_tools  — tools with near-identical descriptions (→ merge)
        overlapping      — tool-vs-skill pairs covering the same function
                           (→ the 1:1 rule: keep one side)
    """
    skill_candidates = []
    existing_names = {s["name"] for s in report["skills"]}
    # The 25 canonical platform wrappers are TOOLS — the model already has
    # them; a skill for "read" would be redundant. Everything else that's
    # used repeatedly is a higher-level capability worth codifying.
    from filesystem.tools import TOOLS
    tool_names = set(TOOLS.keys())
    for tool, count in sorted(report["tools_used"].items(), key=lambda x: -x[1]):
        if count >= PROVEN_THRESHOLD and tool not in existing_names \
                and tool not in tool_names:
            skill_candidates.append({"tool": tool, "count": count})

    friction_candidates = [
        f for f in report["friction"] if f["count"] >= PROVEN_THRESHOLD
    ]

    merge_candidates = []
    skills = report["skills"]
    for i, a in enumerate(skills):
        for b in skills[i + 1:]:
            if _similar(a["name"], b["name"]) >= SIMILARITY_THRESHOLD:
                merge_candidates.append({"a": a["name"], "b": b["name"],
                                         "similarity": round(_similar(a["name"], b["name"]), 2)})

    return {
        "skill_candidates": skill_candidates,
        "friction_candidates": friction_candidates,
        "merge_candidates": merge_candidates,
        "stale_candidates": _stale_skills(skills),
        "tool_candidates": _tool_candidates(report),
    }


def _stale_skills(skills: list[dict], stale_days: int = 30) -> list[dict]:
    import time
    now = time.time()
    return [s for s in skills if (now - s["last_modified"]) > stale_days * 86400]


def _tool_candidates(report: dict) -> dict:
    """The TOOL-tending candidates (the Operator's 08-12 spec).

    The curator tends TOOLS like the janitor tends files:
      - unused_tools    — registered tools with ZERO activity in the
                          events (candidates for removal/archival).
      - duplicate_tools — tools with near-identical descriptions
                          (merge candidates; the schema is compared by
                          description similarity).
      - overlapping     — tool-vs-skill pairs covering the SAME
                          function (the 1:1 rule: keep one side, drop
                          the other — unless both are necessary).
    All are PROPOSALS: the Operator decides what to act on.
    """
    from filesystem.tools import TOOLS
    used = report.get("tools_used", {})
    skills = report.get("skills", [])
    skill_names = {s["name"] for s in skills}

    # 1. Unused tools (registered, never seen in events).
    unused = []
    for tname in sorted(TOOLS.keys()):
        if tname not in used and tname not in ("_terminal",):
            unused.append({"tool": tname, "uses": 0})

    # 2. Duplicate tools: near-identical PARAMETER SCHEMAS (the actual
    #    function shape — the Operator's 08-12 spec: consolidate by
    #    FUNCTION, not by description prose). Platform wrappers like
    #    append/copy/delete have similar descriptions but different
    #    schemas — those are NOT duplicates.
    dups = []
    t_items = sorted(TOOLS.items())
    for i, (an, at) in enumerate(t_items):
        for bn, bt in t_items[i + 1:]:
            asch = getattr(at, "parameters", None) or {}
            bsch = getattr(bt, "parameters", None) or {}
            aprops = set((asch.get("properties") or {}).keys())
            bprops = set((bsch.get("properties") or {}).keys())
            # Same parameter set (or both empty) + name prefix match =
            # a real duplicate candidate.
            if aprops and aprops == bprops:
                sim = _similar(an, bn)
                dups.append({"a": an, "b": bn, "kind": "same-schema",
                             "params": sorted(aprops)[:5],
                             "name_similarity": round(sim, 2)})

    # 3. Tool-vs-skill overlap (same name or near-identical description).
    overlap = []
    for tname, t in t_items:
        tdesc = getattr(t, "description", "") or ""
        # A skill with the SAME name as a tool is a 1:1-rule candidate.
        if tname in skill_names:
            overlap.append({"tool": tname, "skill": tname, "kind": "same-name",
                            "note": "1:1 rule — keep tool or skill, not both"})
        else:
            for s in skills:
                sdesc = s.get("description", "") or ""
                if sdesc and tdesc and _similar(tdesc, sdesc) >= 0.72:
                    overlap.append({"tool": tname, "skill": s["name"],
                                    "kind": "similar-description",
                                    "similarity": round(_similar(tdesc, sdesc), 2)})
                    break

    return {
        "unused_tools": unused,
        "duplicate_tools": dups,
        "overlapping": overlap,
    }


# -- Actions (write skills, adapt facts) ---------------------------------

def create_skill(name: str, description: str, body: str,
                 *, author: str = "curator") -> dict:
    """Create a new skill from learned experience (factual evidence).

    The skill file is SKILL.md with frontmatter — the same shape Athena
    loads (name/description/version/author + markdown body).
    """
    name = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    if not name:
        return {"ok": False, "detail": "invalid skill name"}
    skill_dir = SKILLS_DIR / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    if path.exists():
        return {"ok": False, "detail": f"skill exists: {name}"}
    content = (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"version: 0.1.0\n"
        f"author: {author}\n"
        "---\n\n"
        f"{body}\n"
    )
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "name": name, "path": str(path)}


def update_skill(name: str, *, description: str = "", body: str = "") -> dict:
    """Bump a skill's version and apply a factual fix/improvement."""
    path = SKILLS_DIR / name / "SKILL.md"
    if not path.exists():
        return {"ok": False, "detail": f"skill not found: {name}"}
    text = path.read_text(encoding="utf-8")
    version = _skill_version(path)
    new_version = _bump(version)
    # rewrite frontmatter version
    text = re.sub(r"version:\s*[\d.]+", f"version: {new_version}", text, count=1)
    if description:
        text = re.sub(r"description:\s*.+", f"description: {description}", text, count=1)
    if body:
        text = text.rstrip() + "\n\n" + body + "\n"
    path.write_text(text, encoding="utf-8")
    return {"ok": True, "name": name, "version": new_version}


def _bump(version: str) -> str:
    parts = version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def merge_skills(a: str, b: str) -> dict:
    """Merge skill B into skill A (B is archived, A keeps both bodies).

    The merge target is the one with the richer description; the absorbed
    skill's content is appended to the keeper's body.
    """
    path_a = SKILLS_DIR / a / "SKILL.md"
    path_b = SKILLS_DIR / b / "SKILL.md"
    if not path_a.exists() or not path_b.exists():
        return {"ok": False, "detail": f"merge target missing: {a} or {b}"}
    text_b = path_b.read_text(encoding="utf-8")
    body_b = text_b.split("---", 2)[-1].strip() if text_b.count("---") >= 2 else text_b
    # absorb into A
    text_a = path_a.read_text(encoding="utf-8")
    new_version = _bump(_skill_version(path_a))
    text_a = re.sub(r"version:\s*[\d.]+", f"version: {new_version}", text_a, count=1)
    text_a = text_a.rstrip() + (
        f"\n\n## Absorbed from `{b}`\n\n{body_b}\n")
    path_a.write_text(text_a, encoding="utf-8")
    # archive B
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVE_DIR / f"{b}.md"
    path_b.rename(dest)
    return {"ok": True, "keeper": a, "absorbed": b, "archived_to": str(dest)}


# -- Section-level merge + optimization ----------------------------------

def merge_sections(path: Path, sections_a: list[dict],
                   sections_b: list[dict], *, provider=None) -> dict:
    """Merge two section lists into one file.

    Sections from B are appended to A's file, then the whole document is
    OPTIMIZED with a provider call (simplify + dedupe) when a provider is
    given. Without a provider, the merge is mechanical (append + keep the
    --- delimiters).
    """
    from intelligence.sections import render_sections, write_section_file

    merged = list(sections_a)
    merged.extend(sections_b)
    text = render_sections(merged)

    optimized = ""
    if provider is not None:
        try:
            optimized = _optimize_with_provider(text, provider)
        except Exception:
            optimized = ""

    if optimized:
        write_section_file(path, parse_sections(optimized))
        return {"ok": True, "merged": len(merged), "optimized": True,
                "path": str(path)}
    write_section_file(path, merged)
    return {"ok": True, "merged": len(merged), "optimized": False,
            "path": str(path)}


def _optimize_with_provider(text: str, provider) -> str:
    """Ask the model to simplify + dedupe a section document.

    The provider call is the OPTIMIZE step: it rewrites the merged
    sections into a cleaner, simpler form — same knowledge, less prose,
    no duplicated instructions. The model keeps the --- delimiters.
    """
    prompt = (
        "Optimize this skill document. Keep ALL the technical content and "
        "steps, but: simplify the wording, remove duplicated instructions, "
        "merge overlapping sections, and keep the --- section delimiters "
        "exactly as they are. Output ONLY the optimized document.\n\n"
        f"{text}"
    )
    from core.message_loop import MessageLoop
    loop = MessageLoop(providers=provider, system_prompt="You are a document optimizer.",
                       max_iterations=2)
    turn = loop.run_turn(prompt)
    out = turn.reply.strip()
    # Safety: only accept output that still has section structure.
    if "---" in out and out.startswith("#"):
        return out
    return ""


def archive_skill(name: str) -> dict:
    """Move an unused skill to the archive (lifecycle: stale → archived)."""
    path = SKILLS_DIR / name / "SKILL.md"
    if not path.exists():
        return {"ok": False, "detail": f"skill not found: {name}"}
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVE_DIR / f"{name}.md"
    path.rename(dest)
    return {"ok": True, "name": name, "archived_to": str(dest)}


# -- The review pass ------------------------------------------------------

def review(profile: str = "", *, dry_run: bool = False) -> dict:
    """The curator's full review: scan → candidates → act.

    With dry_run=True it reports what it WOULD do (safe). Otherwise it
    creates skills from proven tools, patches friction, merges duplicates,
    archives stale — all from factual evidence, no speculation.
    """
    report = scan(profile)
    cand = candidates(report)
    state = _load_state()
    actions = []

    # 1. Proven tools → new skills (repeated success).
    for c in cand["skill_candidates"]:
        action = {
            "type": "create_skill",
            "tool": c["tool"],
            "count": c["count"],
            "description": f"Learn-by-doing skill for {c['tool']} (used {c['count']}x).",
        }
        if not dry_run:
            r = create_skill(
                c["tool"],
                f"Use when working with {c['tool']} — codified from {c['count']}x successful use.",
                f"# {c['tool']}\n\nLearned from repeated use (see events log). "
                f"Used successfully {c['count']} times.",
            )
            action["result"] = r
            if r.get("ok"):
                state["skills_created"].append(c["tool"])
        actions.append(action)

    # 2. Repeated friction → note a fix (we log it; fixing is the nurse's
    #    domain, so the curator only records the pattern).
    for f in cand["friction_candidates"]:
        actions.append({
            "type": "friction_fix",
            "tool": f["tool"],
            "count": f["count"],
            "pattern": f["pattern"][:100],
        })

    # 3. Duplicates → merge.
    for m in cand["merge_candidates"]:
        action = {
            "type": "merge_skills",
            "keeper": m["a"],
            "absorbed": m["b"],
            "similarity": m["similarity"],
        }
        if not dry_run:
            r = merge_skills(m["a"], m["b"])
            action["result"] = r
            if r.get("ok"):
                state["skills_merged"].append(f"{m['a']}+{m['b']}")
        actions.append(action)

    # 4. Stale → archive.
    for s in cand["stale_candidates"]:
        action = {
            "type": "archive_skill",
            "name": s["name"],
            "last_modified": datetime.fromtimestamp(s["last_modified"]).isoformat(),
        }
        if not dry_run:
            r = archive_skill(s["name"])
            action["result"] = r
        actions.append(action)

    state["scans"] += 1
    state["last_scan"] = _now()
    _save_state(state)

    return {
        "scan": report,
        "candidates": cand,
        "actions": actions,
        "dry_run": dry_run,
        "state": state,
    }

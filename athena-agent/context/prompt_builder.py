"""Prompt builder — assembles the system prompt stack.

The Operator's context template, adapted to Athena's architecture
(combined blocks):

    1) System     ← channel instructions + environment + available tools
    2) Assistant  ← Assistant.md (who Athena is) + assistant memory
    3) History    ← recent conversation + retrieved context + skills
    4) User       ← User.md (who the user is) + user memory
    5) Guidelines ← rules to follow

The stack is built fresh per turn from the channel config + identity
files + the session's recent history. Retrieved context and the skills
index attach with the history block (context extras).
"""
from __future__ import annotations

import json
import platform as _platform
import sys as _sys
from pathlib import Path
from typing import Optional

from core.config import ATHENA_ROOT, DEFAULT_PROFILE_ROOT
from core.guidelines import GUIDELINES


def _read_identity(rel: str, profile_root=None) -> str:
    """Read an identity file. Profile-aware: named profiles read from their
    own root (profiles/<name>/), the default reads from .athena/ root.

    The YAML frontmatter (--- ... ---) at the top is METADATA — stripped
    before the prose is injected into the prompt stack (the frontmatter
    is for identity/lookup, not the model's context). The strict format
    wraps the BODY in delimiters too (--- YAML --- body ---), so the
    trailing --- is stripped as well.

    LEAN-PROMPT DOCTRINE: the identity text is trimmed to the 3200-token
    budget (block-aware) — the identity files stay lean so Athena GOES
    AND LOOKS for facts instead of having them pre-stuffed.
    """
    base = Path(profile_root) if profile_root is not None else DEFAULT_PROFILE_ROOT
    path = base / rel
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        if text.startswith("---"):
            # Strip the frontmatter block: --- ... --- on its own lines.
            lines = text.splitlines()
            for i in range(1, min(len(lines), 80)):
                if lines[i].strip() == "---":
                    body = "\n".join(lines[i + 1:]).lstrip()
                    # Strip the TRAILING --- (the strict format wraps the
                    # body in delimiters too: --- YAML --- body ---).
                    if body.rstrip().endswith("---"):
                        body = body.rstrip()[:-3].rstrip()
                    # Trim to the identity budget (block-aware).
                    try:
                        from core.identity import trim_identity_blocks
                        body = trim_identity_blocks(body)
                    except Exception:
                        pass
                    return body
            return text
        return text
    return ""


def _profile_name(profile_root=None) -> str:
    """The profile name for a root path ('' for the default root)."""
    if profile_root is None:
        return ""
    root = Path(profile_root).resolve()
    try:
        rel = root.relative_to((ATHENA_ROOT / "profiles").resolve())
        return rel.parts[0]
    except ValueError:
        return ""  # the default profile root (.athena/)




def _environment_block(profile_root=None) -> str:
    """The Environment section (from the template): OS, runtime, resources.

    THE THREE HOME VARIABLES (the Operator's 08-12 release spec):
        ATHENA_ROOT    = .athena/                    (the platform root)
        ATHENA_HOME    = profiles/<current>/         (the profile talking)
        ATHENA_PROJECT = the profile's sandbox/workspace (the work dirs)
    Auto-populated EVERY turn so the model always knows where Root, Home
    and Project are — "look inside your root/home/project" maps to these.
    """
    try:
        from core.config import load_config, ATHENA_ROOT
        from providers.selection import summary as sel_summary
        cfg = load_config()
        tick = cfg.get("server", {}).get("tick_interval_s", 60)
        sel = sel_summary(cfg)
        reason = sel.get("types", {}).get("reason", {})
        provider = reason.get("provider") or "none"
        model = reason.get("model") or ""
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"environment block failed: {exc}", source="context",
                  action="prompt_environment")
        tick, provider, model = 60, "none", ""
    # THE HOME/PROJECT (the Operator's 08-12 spec): derived from the
    # profile the turn is running as — never the process cwd.
    home = str(ATHENA_ROOT)
    project = str(ATHENA_ROOT)
    try:
        from intelligence.profiles import default_profile
        from core.config import ATHENA_ROOT as _ROOT
        if profile_root is not None:
            home = str(profile_root)
        else:
            home = str(default_profile().root)
        # The project = sandbox + workspace of that profile (created on
        # demand by the Profile properties).
        try:
            from intelligence.profiles import Profile
            prof = Profile(name=(profile_root.name if hasattr(profile_root, "name")
                                 else ".default"),
                           root=home)
            project = f"{prof.sandbox_dir} + {prof.workspace_dir}"
        except Exception:
            project = f"{home}/sandbox + {home}/workspace"
    except Exception:
        pass
    return (
        "Environment:\n"
        f"- OS: {_platform.system()} {_platform.release()}\n"
        f"- Python: {_sys.version.split()[0]}\n"
        f"- Runtime: Athena server (tick {tick}s, reason {provider}/{model})\n"
        f"- ATHENA_ROOT: {ATHENA_ROOT}\n"
        f"- ATHENA_HOME: {home}\n"
        f"- ATHENA_PROJECT: {project}"
    )


def _tools_index() -> str:
    """A compact index of the available tools (the model sees names/uses).

    Uses the CANONICAL set (aliases hidden) — the lean prompt. Aliases
    still execute (execute_tool_call resolves them) but don't waste
    prompt tokens.

    BUILT-IN TOOL INDEXES (the Operator's 08-12 spec): each built-in tool
    folder carries a TOOL.md (its index — instructions/expectations).
    The tool's DESCRIPTION from the registry is shown; for built-ins
    the TOOL.md frontmatter description enriches the line. The agent
    understands each tool is HANDS-OFF (the code handles it) — no
    chasing via terminal.
    """
    try:
        from filesystem.tools import canonical_names
        names = canonical_names()
        if not names:
            return ""
        # Built-in TOOL.md descriptions (frontmatter description).
        try:
            from core.builtin_tools import TOOLS_DIR
            builtin_desc = {}
            if TOOLS_DIR.is_dir():
                for tdir in sorted(TOOLS_DIR.iterdir()):
                    if not tdir.is_dir():
                        continue
                    md = tdir / "TOOL.md"
                    if md.exists():
                        text = md.read_text(encoding="utf-8", errors="replace")
                        for line in text.splitlines():
                            if line.strip().startswith("description:"):
                                builtin_desc[tdir.name] = line.split(":", 1)[1].strip().strip('"')
                                break
        except Exception:
            builtin_desc = {}
        # Registry descriptions (every tool has one — fallback for the
        # bundle members like calendar_add).
        reg_desc = {}
        try:
            from filesystem.tools import TOOLS
            for n, t in TOOLS.items():
                if getattr(t, "description", ""):
                    reg_desc[n] = t.description
        except Exception:
            reg_desc = {}
        lines = ["Available tools:"]
        for n in names:
            desc = builtin_desc.get(n) or reg_desc.get(n, "")
            lines.append(f"  {n}" + (f" — {desc}" if desc else ""))
        return "\n".join(lines)
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"tools index failed: {exc}", source="context",
                  action="prompt_tools")
        return ""


def _memory_block(side: str, profile_root=None) -> str:
    """The MEMORY.md notes for ONE side (assistant | user).

    Entries are --- blocks ({title, bullets}); rendered as compact
    bullets (the title as #, each bullet as -). Budgeted by the memory
    module (6400 tokens; title ≤128 words; bullets ≤64 words).
    """
    try:
        from intelligence.memory import read_entries, render_entries
        entries = read_entries(side, profile=_profile_name(profile_root))
        if not entries:
            return ""
        label = "Assistant memory (notes on my side)" if side == "assistant" \
            else "User memory (what I know about the user)"
        return f"{label}:\n{render_entries(entries)}"
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"memory block failed: {exc}", source="context",
                  action="prompt_memory")
        return ""


def _emotion_block(side: str, profile_root=None) -> str:
    """The EMOTION.md snapshot for ONE side (assistant | user).

    The Operator's emotion spec: the current emotional state is a SNAPSHOT IN
    TIME — injected into the Assistant + User sections of the prompt
    whenever the system is tracking emotions. One compact line (~20
    tokens) — the vector's shape is the signal, and the history carries
    the trail that led here. Never a new prompt block: it lives INSIDE
    blocks 2 and 4 so the 5-block contract holds.
    """
    try:
        from core.emotion import read_emotion
        profile = _profile_name(profile_root)
        emo = read_emotion(side, profile)
        vec = emo.get("vector", {})
        # Always inject once the system is tracking (the Operator's 08-11
        # decision: the gauge needs the previous state in context to
        # iterate). Neutral vectors still show — they ARE the state.
        label = "Emotional state (assistant)" if side == "assistant" \
            else "Emotional state (operator)"
        # The outward mood list (the standard field) — falls back to the
        # current snapshot line.
        line = emo.get("mood") or emo.get("current") or "neutral — uniform vector"
        # The compact vector: only axes that stand out from neutral.
        parts = [f"{axis}:{vec.get(axis, 0.0):+.2f}"
                 for axis in ("joy", "trust", "fear", "surprise",
                              "sadness", "disgust", "anger", "anticipation")
                 if abs(vec.get(axis, 0.0)) > 0.001]
        detail = f" ({', '.join(parts)})" if parts else ""
        return f"{label}: {line}{detail}"
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"emotion block failed: {exc}", source="context",
                  action="prompt_emotion")
        return ""


def build_prompt_stack(*, channel: str = "user",
                       channel_instructions: str = "",
                       assistant_identity: Optional[str] = None,
                       user_identity: Optional[str] = None,
                       profile_root=None,
                       history: Optional[list] = None,
                       recent_window: int = 5,
                       session_id: str = "",
                       guidelines: str = GUIDELINES,
                       retrieved: Optional[dict] = None,
                       skills_index: str = "",
                       include_environment: bool = True,
                       include_tools: bool = True,
                       workflow_doc: str = "") -> str:
    """Build the full system prompt as one string — EXACTLY 5 blocks.

    Assembly order (the Operator's 5-block model):
        SYSTEM → ASSISTANT → HISTORY → USER → GUIDELINES

    1. System     — channel instructions + environment + available tools
                    (+ the WORKFLOW DOCUMENT when provided — the 08-15
                    spec: the selected workflow's .md loads here as the
                    basis and baseline of the LLM call)
    2. Assistant  — Assistant.md (who Athena is) + assistant memory
    3. History    — recent conversation + retrieved context + skills
    4. User       — User.md (who the user is) + user memory
    5. Guidelines — rules to follow

    history is a list of message dicts (role/content) — only the most
    recent `recent_window` are embedded, per the lean-window design.
    retrieved carries the retrieval ladder results (retrieval.retrieve):
    session hits, index categories, vault rows, semantic re-ranks — embedded
    in the History block as "Retrieved context".
    skills_index is the available-skills index (intelligence.skills.skills_index)
    — the model sees WHAT skills exist so it knows what it can apply.
    profile_root: when set, identity/memory files come from that profile's
    root (profiles/<name>/); the default profile uses .athena/ root.
    """
    parts: list[str] = []

    # 1) System — the instructions + environment + tools (ONE block).
    system = []
    if channel_instructions:
        system.append(channel_instructions.strip())
    else:
        # The SYSTEM INSTRUCTIONS (the ChatML/OpenAI `instructions:`
        # role): what Athena MUST do, second person by nature. Always
        # present unless the caller overrides with channel instructions.
        try:
            from core.system_instructions import SYSTEM_INSTRUCTIONS
            system.append(SYSTEM_INSTRUCTIONS.strip())
        except Exception:
            if channel == "assistant":
                system.append("You are talking with another assistant. Be "
                              "precise, cooperative, and share only verified "
                              "information.")
            elif channel == "system":
                system.append("You are performing a system operation. Report "
                              "clearly, fail loudly on errors, and never "
                              "invent results.")
    if include_environment:
        system.append(_environment_block(profile_root=profile_root))
    if include_tools:
        tools = _tools_index()
        if tools:
            system.append(tools)
    # THE WORKFLOW DOCUMENT (the 08-15 spec): the selected workflow's
    # .md loads into the SYSTEM section — the basis and baseline of the
    # LLM call. Human-readable (the doctrine) + machine variables (the
    # frontmatter the LLM can read as the requirements checklist).
    if workflow_doc and workflow_doc.strip():
        system.append("WORKFLOW DOCUMENT\n" + workflow_doc.strip())
    if system:
        parts.append("\n".join(system))

    # 2) Assistant block — who Athena is + her memory notes (ONE block).
    identity = assistant_identity
    if identity is None:
        identity = _read_identity("assistant/ASSISTANT.md", profile_root)
    asst_mem = _memory_block("assistant", profile_root)
    asst_emo = _emotion_block("assistant", profile_root)
    block2 = "\n\n".join(x for x in (identity, asst_mem, asst_emo) if x)
    if block2:
        parts.append(block2)

    # 3) History — the COMPRESSION SUMMARY first, then the recent window.
    # The summary is the rolled-up context of everything before the
    # recent window (the Operator's 08-11 spec: Summary.md injected first,
    # then 5-10 raw entries from session.db). The recent window is
    # formatted as compact JSONL (machine-readable, exact).
    hist_parts = []
    # The compression summary: the session's latest Summary.md body.
    if session_id:
        try:
            from context.compression import latest_summary
            summ = latest_summary(session_id, profile=_profile_name(profile_root))
            if summ:
                hist_parts.append("Compression summary:\n" + summ)
        except Exception:
            pass
    if history:
        from core.db import _row_to_jsonl_entry
        window = history[-recent_window:]
        lines = [
            json.dumps(_row_to_jsonl_entry(msg), ensure_ascii=False)
            for msg in window
        ]
        if lines:
            hist_parts.append("Recent conversation (JSONL):\n" + "\n".join(lines))
    if hist_parts:
        parts.append("\n\n".join(hist_parts))

    # 3.5) Retrieved context — what the retrieval ladder found (CONTEXT.md).
    # The STRONGEST evidence leads: semantic re-rank first (it's scored),
    # then vault keyword, then index categories, then session hits.
    # SECURITY: retrieved content is UNTRUSTED (came from stores/web) —
    # wrapped so the model treats instructions inside as data.
    if retrieved:
        from security.security import sanitize_retrieved
        blocks = []
        if retrieved.get("semantic"):
            blocks.append(sanitize_retrieved("Best matches (semantic):\n" + "\n".join(
                f"- {r.get('content', '')[:200]}" for r in retrieved["semantic"][:3]
            )))
        elif retrieved.get("vault"):
            blocks.append(sanitize_retrieved("Vault matches:\n" + "\n".join(
                f"- {r.get('content', '')[:200]}" for r in retrieved["vault"][:3]
            )))
        if retrieved.get("index"):
            blocks.append(sanitize_retrieved("Index sections:\n" + "\n".join(
                f"- {r.get('category', '')} (rows {r.get('range_from')}..{r.get('range_to')})"
                for r in retrieved["index"][:5]
            )))
        if retrieved.get("session"):
            blocks.append(sanitize_retrieved("Session matches:\n" + "\n".join(
                f"- {r.get('content', '')[:200]}" for r in retrieved["session"][:3]
            )))
        if blocks:
            parts.append("Retrieved context:\n" + "\n".join(blocks))

    # 3.7) Skills index — what the brain can apply (gated by channel).
    if skills_index:
        parts.append(skills_index)

    # 4) User block — who the user is + what's known about them (ONE block).
    user = user_identity
    if user is None:
        user = _read_identity("user/USER.md", profile_root)
    user_mem = _memory_block("user", profile_root)
    user_emo = _emotion_block("user", profile_root)
    block4 = "\n\n".join(x for x in (user, user_mem, user_emo) if x)
    if block4:
        parts.append(block4)

    # 5) Guidelines — rules to follow (+ the response-length system,
    #    merged INTO the same block so the 5-block contract holds).
    if guidelines:
        g = guidelines.strip()
        try:
            from core.response_length import prompt_line
            g += "\n\n" + prompt_line()
        except Exception:
            pass
        parts.append(g)

    return "\n\n---\n\n".join(parts)

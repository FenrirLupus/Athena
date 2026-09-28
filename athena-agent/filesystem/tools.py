"""Minimal tool registry — the tools the Message Loop can call.

Tools are dumb, hands-off operations (the space doctrine): a name, a
schema, and a function. Judgment about WHEN to use them lives in the
Message Loop / skills — never in the tool itself.

The registry speaks the OpenAI tool-calling shape:
    assistant message: {"role": "assistant", "tool_calls": [
        {"id": "...", "type": "function",
         "function": {"name": "...", "arguments": "{...json...}"}}
    ]}
    tool result:       {"role": "tool", "tool_call_id": "...", "content": "..."}
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable

from filesystem.safety import ScopeError


class Tool:
    def __init__(self, name: str, description: str, parameters: dict,
                 fn: Callable[[dict], str]):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn

    def schema(self) -> dict:
        """The OpenAI function schema for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(self, arguments: dict, timeout: float = 60.0) -> str:
        try:
            result = self.fn(arguments, timeout=timeout)
            return str(result)
        except FileNotFoundError as exc:
            # Expected: the model asked for a missing path. The error is
            # returned to the model (it can adapt) and logged as a WARNING
            # (L3), not an ERROR — the system is healthy, the input was bad.
            from core.logging import log_event
            log_event(3, f"tool {self.name}: path not found: {exc}",
                      source="filesystem", action="tool_run", target=self.name)
            return f"error: {exc}"
        except PermissionError as exc:
            # Expected: the model touched something it may not read/write.
            from core.logging import log_event
            log_event(3, f"tool {self.name}: permission denied: {exc}",
                      source="filesystem", action="tool_run", target=self.name)
            return f"error: {exc}"
        except Exception as exc:  # noqa: BLE001
            # Unexpected: a real tool failure — the ERROR level (L4).
            from core.logging import log_event
            log_event(4, f"tool {self.name} failed: {exc}", source="filesystem",
                      action="tool_run", target=self.name)
            return f"error: {exc}"


# -- Built-in tools ----------------------------------------------------

def _terminal(arguments: dict, timeout: float = 60.0) -> str:
    from filesystem.safety import check_command, ScopeError

    # THE cmd ALIAS (the Operator's 08-12 release fix): XML-emitting
    # models send <parameter name="cmd"> while the JSON path sends
    # "command" — accept both.
    command = str(arguments.get("command", "") or arguments.get("cmd", ""))
    if not command.strip():
        return "error: command is required"
    try:
        check_command(command)
    except ScopeError as exc:
        return f"error: {exc}"
    # The Operator's 08-12 HOME rule: terminal runs in the PROFILE's SANDBOX
    # by default (never the process cwd — that polluted .athena roots
    # with stray files). Agents and drones work inside sandbox/ or
    # workspace/, never the root homes.
    cwd = None
    try:
        from intelligence.profiles import default_profile
        prof = default_profile()
        cwd = str(prof.sandbox_dir)
    except Exception:
        cwd = None
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=timeout,
        cwd=cwd,
    )
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode != 0:
        return f"exit {result.returncode}\n{err or out}"
    return out or "(no output)"


def _read_file(arguments: dict, timeout: float = 60.0) -> str:
    from filesystem.safety import check_read, ScopeError

    path = str(arguments.get("path", ""))
    if not path:
        return "error: path is required"
    try:
        resolved = check_read(path)
    except ScopeError as exc:
        return f"error: {exc}"
    with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _write_file(arguments: dict, timeout: float = 60.0) -> str:
    from filesystem.safety import check_write, ScopeError

    path = str(arguments.get("path", ""))
    content = str(arguments.get("content", ""))
    if not path:
        return "error: path is required"
    try:
        resolved = check_write(path)
    except ScopeError as exc:
        return f"error: {exc}"
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with open(resolved, "w", encoding="utf-8") as fh:
        fh.write(content)
    return f"wrote {len(content)} chars to {resolved}"


def _fs_stat(arguments: dict, timeout: float = 60.0) -> str:
    from filesystem.safety import check_read, ScopeError
    import datetime

    path = str(arguments.get("path", ""))
    if not path:
        return "error: path is required"
    try:
        resolved = check_read(path)
    except ScopeError as exc:
        return f"error: {exc}"
    st = resolved.stat()
    kind = "dir" if resolved.is_dir() else "file"
    mtime = datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
    return f"{kind} | size={st.st_size} | modified={mtime} | {resolved}"


# Canonical tool names (the lean set the model sees) and their ALIASES
# (kept so any existing caller / saved rule / skill keeps working). The
# registry holds both; schemas() advertises only the canonical set, so
# the prompt's tools index stays lean while execution accepts every name.
ALIASES: dict[str, str] = {
    "read": "read_file",
    "write": "write_file",
    "stat": "fs_stat",
    "execute": "terminal",
}

# The canonical order for the model (stable, grouped by purpose).
_CANONICAL_ORDER = [
    "terminal", "read_file", "write_file", "append", "replace", "patch",
    "delete", "copy", "move", "rename", "list", "tree", "find", "search",
    "mkdir", "exists", "fs_stat", "hash", "process", "kill", "download",
    "upload", "compress", "extract", "memory_add", "memory_list",
    "vault_query", "vault_semantic", "vault_store",
]


def canonical_names() -> list[str]:
    """The lean tool set the model sees (aliases hidden)."""
    seen = set()
    out = []
    for name in _CANONICAL_ORDER:
        if name in TOOLS and name not in seen:
            out.append(name)
            seen.add(name)
    # Any registered NON-ALIAS tool not in the order (defensive: never
    # hide a real tool). Alias names are intentionally hidden.
    for name in sorted(TOOLS):
        if name in ALIASES:
            continue
        if name not in seen:
            out.append(name)
            seen.add(name)
    return out


def resolve(name: str) -> str:
    """The canonical name for a tool (aliases map to their canonical)."""
    return ALIASES.get(name, name)


TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    TOOLS[tool.name] = tool


register(Tool(
    name="terminal",
    description="Run a shell command and return its output.",
    parameters={
        "type": "object",
        "properties": {"command": {"type": "string", "description": "The command to run"}},
        "required": ["command"],
    },
    fn=_terminal,
))

register(Tool(
    name="read_file",
    description="Read a text file inside .athena/ and return its contents.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to the file (inside .athena/)"}},
        "required": ["path"],
    },
    fn=_read_file,
))

register(Tool(
    name="fs_stat",
    description="Stat a path: type, size, modified time.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to stat"}},
        "required": ["path"],
    },
    fn=_fs_stat,
))


# -- Memory tool (two-sided persistent notes) ---------------------------

def _memory_add(arguments: dict, timeout: float = 60.0) -> str:
    from intelligence.memory import add_entry

    side = str(arguments.get("side", "assistant"))
    content = str(arguments.get("content", ""))
    profile = str(arguments.get("profile", ""))
    if not content:
        return "error: content is required"
    path = add_entry(side, content, profile=profile)
    return f"noted on the {side} side: {path}"


def _memory_list(arguments: dict, timeout: float = 60.0) -> str:
    from intelligence.memory import read_all, render_entries

    profile = str(arguments.get("profile", ""))
    mem = read_all(profile)
    out = []
    if mem["assistant"]:
        out.append("Assistant memory:")
        out.append(render_entries(mem["assistant"]))
    if mem["user"]:
        out.append("User memory:")
        out.append(render_entries(mem["user"]))
    return "\n".join(out) if out else "(no memory notes yet)"


register(Tool(
    name="memory_add",
    description="Save a durable note on ONE side: side='assistant' (your own notes) or side='user' (facts about the user).",
    parameters={
        "type": "object",
        "properties": {
            "side": {"type": "string", "description": "'assistant' or 'user'"},
            "content": {"type": "string", "description": "The note/fact to remember"},
            "profile": {"type": "string", "description": "Profile (default = current)"},
        },
        "required": ["side", "content"],
    },
    fn=_memory_add,
))

register(Tool(
    name="memory_list",
    description="List the persistent memory notes (both sides).",
    parameters={
        "type": "object",
        "properties": {"profile": {"type": "string", "description": "Profile (default = current)"}},
    },
    fn=_memory_list,
))


# -- Vault tools (the assistant's access to the archive) -----------------

def _vault_query(arguments: dict, timeout: float = 60.0) -> str:
    """Keyword search of the vault archive (FTS on the vault rows)."""
    from context.retrieval import search_vault_keyword
    query = str(arguments.get("query", ""))
    profile = str(arguments.get("profile", ""))
    limit = int(arguments.get("limit", 5) or 5)
    if not query:
        return "error: query is required"
    try:
        results = search_vault_keyword(query, limit=limit, profile=profile)
    except Exception as exc:
        return f"error: vault search failed: {exc}"
    if not results:
        return "(no vault matches)"
    return "\n".join(
        f"- [{r.get('kind', 'row')}] {str(r.get('content', ''))[:200]}"
        for r in results
    )


def _vault_semantic(arguments: dict, timeout: float = 60.0) -> str:
    """Semantic search of the vault (embedding-based, needs the local model)."""
    from context.retrieval import retrieve
    query = str(arguments.get("query", ""))
    profile = str(arguments.get("profile", ""))
    if not query:
        return "error: query is required"
    try:
        result = retrieve(query, profile=profile)
    except Exception as exc:
        return f"error: semantic search failed: {exc}"
    semantic = result.get("semantic", []) or []
    vault = result.get("vault", []) or []
    hits = semantic[:3] or vault[:3]
    if not hits:
        return "(no semantic matches)"
    return "\n".join(
        f"- {str(r.get('content', ''))[:200]}" for r in hits
    )


def _vault_store(arguments: dict, timeout: float = 60.0) -> str:
    """Store a fact into the vault archive (the deep memory)."""
    from core import db as db_layer
    content = str(arguments.get("content", ""))
    profile = str(arguments.get("profile", ""))
    type = str(arguments.get("type", arguments.get("kind", "message")))
    if not content:
        return "error: content is required"
    try:
        entry_id = db_layer.record_vault_entry(type, content, profile=profile)
    except Exception as exc:
        return f"error: vault store failed: {exc}"
    return f"stored vault entry {entry_id}"


register(Tool(
    name="vault_query",
    description="Search the vault archive by keyword (the deep memory).",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to find"},
            "limit": {"type": "integer", "description": "Max results (default 5)"},
            "profile": {"type": "string", "description": "Profile (default = current)"},
        },
        "required": ["query"],
    },
    fn=_vault_query,
))

register(Tool(
    name="vault_semantic",
    description="Search the vault by MEANING (embedding-based semantic search).",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to find"},
            "profile": {"type": "string", "description": "Profile (default = current)"},
        },
        "required": ["query"],
    },
    fn=_vault_semantic,
))

register(Tool(
    name="vault_store",
    description="Store a durable fact into the vault archive (the deep memory).",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The fact to store"},
            "kind": {"type": "string", "description": "Entry kind (default note)"},
            "profile": {"type": "string", "description": "Profile (default = current)"},
        },
        "required": ["content"],
    },
    fn=_vault_store,
))

register(Tool(
    name="write_file",
    description="Write a text file. Refused outside .athena/ and in the sanctum (athena-system/).",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to write (inside .athena/)"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    },
    fn=_write_file,
))


# -- Platform toolset (25 wrappers, Linux + Windows backends) ------------
# Every wrapper name below maps 1:1 to a method on systems.platform (the
# active backend: linux or windows). One source of truth — the agent calls
# "read"/"write"/... and the backend for the current OS executes it.

from oslayer import platform

# name -> (schema properties, required args)
_FS_TOOLS = {
    "append":    ({"path": "Path to append", "content": "Content to append"}, ["path", "content"]),
    "replace":   ({"path": "File to edit", "old": "Exact text to find", "new": "Replacement text",
                   "replace_all": "Replace all occurrences (default false)"},
                  ["path", "old", "new"]),
    "patch":     ({"path": "File to patch", "hunks": "List of {old,new} edit hunks"}, ["path", "hunks"]),
    "delete":    ({"path": "Path to delete (never the sanctum)"}, ["path"]),
    "copy":      ({"src": "Source path", "dst": "Destination path"}, ["src", "dst"]),
    "move":      ({"src": "Source path", "dst": "Destination path"}, ["src", "dst"]),
    "rename":    ({"path": "Path to rename", "new_name": "New name"}, ["path", "new_name"]),
    "list":      ({"path": "Directory to list (empty = root)"}, []),
    "tree":      ({"path": "Directory to tree (empty = root)", "max_depth": "Max depth (default 3)"}, []),
    "find":      ({"path": "Directory to search (empty = root)", "pattern": "Glob pattern",
                   "file_type": "file|dir"}, []),
    "search":    ({"pattern": "Regex to search contents", "path": "Dir/file (empty = root)",
                   "file_glob": "Glob filter (default * = all files)"}, ["pattern"]),
    "mkdir":     ({"path": "Directory to create", "recursive": "Create parents (default true)"}, ["path"]),
    "exists":    ({"path": "Path to check"}, ["path"]),
    "hash":      ({"path": "File to hash", "algo": "Algorithm (default sha256)"}, ["path"]),
    "terminal":  ({"command": "Command to run"}, ["command"]),
    "process":   ({"name": "Filter by process name (optional)"}, []),
    "kill":      ({"pid": "Process id", "force": "Force kill (default false)"}, ["pid"]),
    "download":  ({"url": "URL to fetch", "dest": "Destination path"}, ["url", "dest"]),
    "upload":    ({"path": "File to upload", "url": "Destination URL"}, ["path", "url"]),
    "compress":  ({"path": "File/dir to compress", "dest": "Destination zip (optional)"}, ["path"]),
    "extract":   ({"path": "Zip to extract", "dest": "Destination dir (optional)"}, ["path"]),
}

# Wrappers that take paths: (write-ops → check_write, read-ops → check_read).
_WRITE_OPS = {"write", "append", "replace", "patch", "delete", "copy", "move",
              "rename", "mkdir", "download", "compress", "extract"}
_READ_OPS = {"read", "list", "tree", "find", "search", "exists", "stat", "hash",
             "upload"}


def _check_scope(name: str, arguments: dict) -> None:
    """Enforce the domain boundary BEFORE any command runs.

    The command itself is dumb; the scope decision lives here (plus the
    nurse's privileged repair scope). Raises ScopeError when refused.
    """
    from filesystem.safety import check_read, check_write

    if name in _WRITE_OPS:
        for key in ("path", "src", "dst", "dest", "url"):
            val = arguments.get(key)
            if val and isinstance(val, str) and not val.startswith(("http://", "https://")):
                check_write(val)
    elif name in _READ_OPS:
        for key in ("path", "src"):
            val = arguments.get(key)
            if val:
                check_read(val)


def _make_wrapper(name: str):
    """Build a (arguments, timeout) -> str wrapper.

    HANDS-OFF: the assistant provides simple inputs (a path, a pattern,
    content). The wrapper checks scope, assembles the real terminal
    command via the active OS backend (read -> 'cat <path>' on linux,
    'type <path>' on windows), and executes it. The model never sees the
    command — simple in, complex out.
    """

    def wrapper(arguments: dict, timeout: float = 60.0) -> str:
        try:
            # THE PATCH HUNKS NORMALIZATION (the 08-15 fix): the model
            # sometimes sends `hunks` as a STRING (JSON text, or a plain
            # "old → new" pair) instead of a list of {old,new} dicts —
            # the backend's `.get` crashed on the string. Normalize to
            # the list shape the backend expects.
            if name == "patch":
                h = arguments.get("hunks")
                if isinstance(h, str):
                    import json as _pj
                    try:
                        parsed = _pj.loads(h) if h.strip() else []
                        if isinstance(parsed, list):
                            arguments["hunks"] = parsed
                        elif isinstance(parsed, dict):
                            arguments["hunks"] = [parsed]
                        else:
                            # A plain string pair "OLD||NEW" or "OLD\n---\nNEW".
                            arguments["hunks"] = [{"old": h, "new": ""}]
                    except Exception:
                        arguments["hunks"] = [{"old": h, "new": ""}]
                elif isinstance(h, dict):
                    arguments["hunks"] = [h]
                elif h is None:
                    arguments["hunks"] = []
            _check_scope(name, arguments)
            # Content-bearing ops pipe their payload as stdin to the command.
            stdin = ""
            if name in ("write", "append"):
                stdin = str(arguments.get("content", ""))
            command = platform.build_command(name, arguments)
            return platform.run_command(command, stdin=stdin, timeout=timeout)
        except ScopeError as exc:
            return f"error: {exc}"
        except TypeError as exc:
            return f"error: bad arguments: {exc}"
    return wrapper


def _register_platform_tools() -> None:
    for name, (props, required) in _FS_TOOLS.items():
        schema = {"type": "object",
                  "properties": {k: {"type": "string"} for k in props},
                  "required": required}
        for key in ("replace_all", "recursive", "force"):
            if key in schema["properties"]:
                schema["properties"][key] = {"type": "boolean"}
        for key in ("max_depth", "pid"):
            if key in schema["properties"]:
                schema["properties"][key] = {"type": "integer"}
        register(Tool(
            name=name,
            description=f"{name} — platform tool (backend: {platform.name}).",
            parameters=schema,
            fn=_make_wrapper(name),
        ))


_register_platform_tools()

# THE SKILL LOAD TOOL (the Operator's 08-12 spec): skills are CALLABLE,
# not just context. The agent invokes `skill_load {name}` to bring a
# skill's instructions into the turn — that CALL is real work, visible
# in the Thinking block (🖊️ skill:name). The tool returns the skill's
# body + references so the model can apply them.
def _skill_load(arguments: dict, timeout: float = 60.0) -> str:
    name = str(arguments.get("name", "")).strip()
    if not name:
        return "error: skill name is required"
    try:
        from intelligence.skills import load_skills
        skills = load_skills()
    except Exception as exc:
        return f"error: skills unavailable: {exc}"
    sk = next((s for s in skills if s.name.lower() == name.lower()), None)
    if sk is None:
        known = ", ".join(sorted(s.name for s in skills)) or "none"
        return f"error: skill '{name}' not found. Available: {known}"
    body = sk.body or "(no body)"
    refs = sk.references or ""
    return f"SKILL: {sk.name}\n{body}\n{refs}".strip()

register(Tool(
    name="skill_load",
    description=("Load a skill's instructions by name — invoke this when "
                 "a task matches a listed skill (e.g. network, doctor, "
                 "checklist). Returns the skill's body + references to apply."),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string",
                     "description": "The skill name to load (e.g. network, doctor)"},
        },
        "required": ["name"],
    },
    fn=_skill_load,
))

# THE WORKFLOW LOAD TOOL (the Operator's 08-12 workflow spec): a workflow
# is a gated 10-step .md pipeline that holds the LLM's hand through a
# process (programmer: Diagnose → Plan → Checklist → Build → Compare →
# Execute → Verify → Test → Result → Summarize). The agent invokes it and
# follows the steps IN ORDER, recording each step's evidence.
def _workflow_load(arguments: dict, timeout: float = 60.0) -> str:
    name = str(arguments.get("name", "")).strip()
    if not name:
        return "error: workflow name is required"
    try:
        from workflows import load, validate
        wf = load(name)
        errors = validate(wf)
        if errors:
            return f"WORKFLOW '{name}' INVALID: {'; '.join(errors)}"
        steps = "\n".join(
            f"  {s['order']}. {s['name']}" for s in wf["steps"])
        safety = wf.get("meta", {}).get("safety", "")
        return (f"WORKFLOW: {name}\n"
                f"SAFETY: {safety}\n"
                f"STEPS (follow in order, each gated):\n{steps}\n"
                f"---\n" + "\n".join(
                    f"[{s['order']}] {s['name']}\n{s['body']}"
                    for s in wf["steps"]))
    except Exception as exc:
        return f"error: workflow unavailable: {exc}"


register(Tool(
    name="workflow_load",
    description=("Load a gated 10-step workflow by name — invoke this when "
                 "a task is a multi-step process (programmer: diagnose/plan/"
                 "build/verify). Returns the full step-by-step pipeline to "
                 "follow in order."),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string",
                     "description": "The workflow name (e.g. programmer)"},
        },
        "required": ["name"],
    },
    fn=_workflow_load,
))

# THE PROJECT-SET TOOL (the Operator's 08-12 release spec): redirects
# ATHENA_PROJECT — the profile's work directory — to a filesystem URL.
# The project pointer: when the operator says "work in
# <path>", Athena updates her project target and the environment block
# reflects it on the next turn.
def _project_set(arguments: dict, timeout: float = 60.0) -> str:
    from pathlib import Path
    target = str(arguments.get("path", "")).strip()
    if not target:
        return "error: path is required"
    p = Path(target).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return f"error: cannot create project dir: {exc}"
    try:
        from core.config import load_config, save_config
        from intelligence.profiles import default_profile
        prof = default_profile()
        cfg = load_config(prof.name if not prof.is_default else "")
        cfg.setdefault("workspace", {})["dir"] = str(p)
        save_config(cfg, prof.name if not prof.is_default else "")
        return f"project set: {p}"
    except Exception as exc:
        return f"error: project set failed: {exc}"

register(Tool(
    name="project_set",
    description=("Set ATHENA_PROJECT — the profile's work directory — to "
                 "a filesystem path. Use when the operator says to work in "
                 "a specific directory or gives a filesystem URL. Persists "
                 "as the profile's workspace.dir."),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "The project directory path"},
        },
        "required": ["path"],
    },
    fn=_project_set,
))

# ALIAS REGISTRATION (the Operator's no-loss rule): the 4 alias names resolve
# to their canonical tools at execution — register them as POINTERS to
# the same Tool objects so any caller / saved rule / skill using the
# old names keeps working. They are hidden from schemas() but present
# in the registry.
for _alias, _canon in ALIASES.items():
    if _canon in TOOLS and _alias not in TOOLS:
        TOOLS[_alias] = TOOLS[_canon]


def schemas() -> list[dict]:
    """All tool schemas, for the model's tools parameter.

    Advertises ONLY the canonical set (aliases hidden) — the lean
    prompt. Execution still accepts every registered name via
    execute_tool_call's resolve().
    """
    return [TOOLS[n].schema() for n in canonical_names() if n in TOOLS]


def schemas_with_skills(skills: list | None = None) -> list[dict]:
    """The full function list for the model: TOOLS + SKILLS + WORKFLOWS,
    all in the SAME OpenAI function schema (the 08-12 standardized
    execution schema).

    Each allowed skill is advertised as `skill:<name>` — a first-class
    callable with the identical {type, function:{name, description,
    parameters}} shape as a tool. The model invokes either identically.
    THE 08-15 FIX: the WORKFLOW LANES are advertised too as
    `workflow:<name>` callables — the model can load a workflow's full
    contract on demand (previously it guessed skill:<name> and failed).
    """
    out = schemas()
    # THE 08-15 WORKFLOW ADVERTISEMENT: every lane is callable by name.
    # Runs even when skills is empty (workflows are always available).
    try:
        from workflows.registry import list_workflows
        for wf in list_workflows():
            out.append({
                "type": "function",
                "function": {
                    "name": f"workflow:{wf.get('name', '')}",
                    "description": (
                        "Load a workflow lane's full contract (doctrine + "
                        "requirements) to guide this task. "
                        + str(wf.get("description", ""))),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "The workflow name",
                                "enum": [wf.get("name", "")],
                            }
                        },
                    },
                },
            })
    except Exception:
        pass
    if not skills:
        return out
    for sk in skills:
        try:
            if hasattr(sk, "to_tool_schema"):
                out.append(sk.to_tool_schema())
        except Exception:
            continue
    return out


def schemas_for_channel(tools: list | None = None,
                        skills: list | None = None) -> list[dict]:
    """THE CHANNEL-SCOPED FUNCTION LIST (the Operator's 08-14 fix): the
    model sees ONLY the tools + skills the channel ALLOWS — NOT the full
    96-tool registry. The full payload (~42KB, 111 schemas) tripped the
    provider's WAF/limits and degraded replies to terse "ok"/"checked".
    tools/skills: the channel's allowed names, or None/"*" = all.
    """
    names = set(tools) if tools else None
    out = []
    for s in schemas():
        fn = s.get("function", {})
        n = fn.get("name", "")
        if names is None or n in names or "*" in (names or set()):
            out.append(s)
    if skills:
        for sk in skills:
            try:
                if hasattr(sk, "to_tool_schema"):
                    out.append(sk.to_tool_schema())
            except Exception:
                continue
    return out


def execute_tool_call(tool_call: dict, timeout: float = 60.0,
                      profile: str = "") -> str:
    """Run ONE tool call. Returns the result string.

    THE STANDARDIZED DISPATCH (the Operator's 08-12 spec): skills and
    tools execute through the SAME path. A name of "skill:<name>"
    resolves to the skill's loader (returns its body + references —
    the knowledge the model applies); a plain tool name resolves to the
    tool. Same schema in, same result-string out.

    THE 08-17 PROFILE-ALIGNMENT (the Operator's doctrine): profiles are
    INDIVIDUAL, isolated experiences — never a conglomerate. The active
    profile is AUTO-INJECTED into the tool's arguments whenever a
    profile-scoped tool (vault_query / vault_semantic / vault_store /
    memory_*) doesn't already carry one. This guarantees Kali's tools
    always query HER vault, never the .default's — the LLM's own
    arguments must never be trusted to name the right profile.
    """
    function = tool_call.get("function", {})
    name = function.get("name", "")
    raw_args = function.get("arguments", "{}")
    # THE SKILL DISPATCH (the 08-12 fix, updated 08-14): skill:<name>
    # AND the pattern-safe skill_<name> are first-class callables — the
    # SAME schema as a tool (to_tool_schema advertises the safe form;
    # legacy skill: calls still resolve). Returns the skill's body +
    # references for the model.
    if isinstance(name, str) and (name.startswith("skill:")
                                  or name.startswith("skill_")):
        prefix = "skill:" if name.startswith("skill:") else "skill_"
        skill_name = name[len(prefix):]
        try:
            if isinstance(raw_args, str):
                args = json.loads(raw_args) if raw_args.strip() else {}
            else:
                args = raw_args or {}
        except json.JSONDecodeError as exc:
            return f"error: invalid arguments JSON: {exc}"
        # The name arg (the schema's required param) can override.
        wanted = str(args.get("name", skill_name)).strip() or skill_name
        from intelligence.skills import load_skills
        skills = load_skills()
        sk = next((s for s in skills if s.name.lower() == wanted.lower()), None)
        if sk is None:
            known = ", ".join(sorted(s.name for s in skills)) or "none"
            # THE METRICS (the 08-12 audit): a failed skill load is
            # logged — a missing skill must be diagnosable.
            try:
                from core.logging import log_event
                log_event(3, f"skill '{wanted}' not found (available: {known})",
                          source="intelligence", tool="skill_load",
                          action="skill_call")
            except Exception:
                pass
            return f"error: skill '{wanted}' not found. Available: {known}"
        body = sk.body or "(no body)"
        refs = sk.references or ""
        # THE METRICS (the 08-12 audit): every skill load lands in the
        # stream — the console/terminal show skill invocations like tool
        # calls (the mirror rule).
        try:
            from core.logging import log_event
            log_event(2, f"skill loaded: {sk.name} ({len(body)} chars)",
                      source="intelligence", tool="skill_load",
                      action="skill_call", target=sk.name)
        except Exception:
            pass
        return f"SKILL: {sk.name}\n{body}\n{refs}".strip()
    # THE WORKFLOW DISPATCH (the Operator's 08-15 fix): `workflow:<name>`
    # is a first-class callable — the SAME schema as skill:<name>. The
    # model invokes it to load a workflow lane's full contract (frontmatter
    # + doctrine + requirements) and apply it as its working guide.
    if isinstance(name, str) and (name.startswith("workflow:")
                                  or name.startswith("workflow_")):
        prefix = "workflow:" if name.startswith("workflow:") else "workflow_"
        wf_name = name[len(prefix):]
        try:
            if isinstance(raw_args, str):
                args = json.loads(raw_args) if raw_args.strip() else {}
            else:
                args = raw_args or {}
        except json.JSONDecodeError as exc:
            return f"error: invalid arguments JSON: {exc}"
        wanted = str(args.get("name", wf_name)).strip() or wf_name
        try:
            from workflows.registry import list_workflows, load_workflow
            wf = load_workflow(wanted)
            if wf is None:
                known = ", ".join(sorted(w["name"] for w in list_workflows())) or "none"
                try:
                    from core.logging import log_event
                    log_event(3, f"workflow '{wanted}' not found (available: {known})",
                              source="workflows", tool="workflow_load",
                              action="workflow_call")
                except Exception:
                    pass
                return f"error: workflow '{wanted}' not found. Available: {known}"
            body = wf.get("sections_text", "") or ""
            reqs = "".join(
                f"- {r.get('label','')}: {r.get('description','')}\n"
                for r in (wf.get("requirements") or []))
            try:
                from core.logging import log_event
                log_event(2, f"workflow loaded: {wf.get('name')} "
                             f"({len(body)} chars)", source="workflows",
                          tool="workflow_load", action="workflow_call",
                          target=wf.get("name"))
            except Exception:
                pass
            return (f"WORKFLOW: {wf.get('name')}\n"
                    f"{wf.get('description','')}\n\n{body}\n"
                    f"REQUIREMENTS:\n{reqs}").strip()
        except Exception as exc:
            return f"error: workflow load failed: {exc}"
    # ALIAS RESOLUTION: a saved rule / skill may call "read" or "execute"
    # — map to the canonical tool so the call still works.
    tool = TOOLS.get(resolve(name))
    if tool is None:
        return f"error: unknown tool '{name}'"
    try:
        if isinstance(raw_args, str):
            args = json.loads(raw_args) if raw_args.strip() else {}
        else:
            args = raw_args or {}
    except json.JSONDecodeError as exc:
        return f"error: invalid arguments JSON: {exc}"
    if not isinstance(args, dict):
        return "error: arguments must be an object"
    # THE 08-17 PROFILE-ALIGNMENT: auto-inject the active profile into any
    # profile-scoped tool that lacks it. The profile-scoped set = the vault
    # + memory tools (they read/write a profile's private stores).
    if profile:
        _profiled = {"vault_query", "vault_semantic", "vault_store",
                     "memory_add", "memory_list", "memory_read", "memory_del"}
        if (tool.name in _profiled) and not (args.get("profile") or ""):
            args["profile"] = str(profile) if str(profile) != "default" else ".default"
    return tool.run(args, timeout=timeout)

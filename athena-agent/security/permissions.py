"""Interactive permissions — the Operator's Allow/Deny/Block gate.

THE 08-15 MODEL (the Operator's spec): a per-profile permissions.yaml with
FOUR channels sharing ONE schema:

    operator_channel:  {tools: [names], skills: [names]}
    agent_channel:     {tools: [names], skills: [names]}
    system_channel:    {tools: [names], skills: [names]}   # default allow_session
    global_channel:    {tools: {type, level}, skills: {type, level}}

Rules:
  • DEFAULT = ALLOW ONCE for tools/skills on the operator + agent channels
    — every activation prompts the operator.
  • An allow at SESSION or GLOBAL scope POPULATES the name into the
    channel's list (the source of truth): a name in the list = allowed at
    its recorded scope (no re-prompt within the scope); a name absent =
    prompt every time.
  • The SYSTEM channel uses the same schema — entries default to
    allow_session (the house's own machinery, never prompts).
  • The GLOBAL channel is the FOURTH: two flag pairs (Type: allow/deny/
    block × Level: once/session/global) for tools and the same for skills
    — the global security level.

The tool gate (message_loop) consults this engine BEFORE executing an
unsafe command; an unknown + unsafe call returns NEEDS_PROMPT and the
CLI/GUI shows the popup. The verdict persists per scope.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import yaml

from core.config import ATHENA_ROOT

# Risk levels
RISK_SAFE = "safe"          # read-only, no side effects
RISK_UNSAFE = "unsafe"      # writes/deletes/network — needs a rule
RISK_BLOCKED = "blocked"    # never allowed even with a prompt

# Verdicts
ALLOW = "allow"
DENY = "deny"
BLOCK = "block"
NEEDS_PROMPT = "needs_prompt"

# Scopes
ONCE = "once"
SESSION = "session"
GLOBAL = "global"

_VERDICTS = {ALLOW, DENY, BLOCK}
_SCOPES = {ONCE, SESSION, GLOBAL}

# Tools that are always safe (read-only) — never prompt.
_SAFE_TOOLS = {
    "read", "list", "tree", "find", "search", "exists", "stat", "hash",
    "memory_list", "session", "vault_query", "logs", "status", "doctor",
    "skills", "plugins", "tools", "help", "config",
}

# Tools that are always blocked (even a prompt cannot allow them).
_BLOCKED_TOOLS = {
    "kill",  # killing arbitrary processes is never user-approved casually
}

# Patterns that make an otherwise-safe tool unsafe.
_UNSAFE_PATTERNS = [
    "rm -rf", "rm -f /", "mkfs", "dd if=", "> /dev/sd", ":() {",
    "chmod -R 777 /", "DROP TABLE", "DELETE FROM", "TRUNCATE",
]

# Argument keys that carry a PATH (the in-bounds check scans these).
_PATH_KEYS = ("path", "src", "dst", "dest", "url", "target", "dir")
# Keys that carry a COMMAND string (scanned for network/escape patterns).
_COMMAND_KEYS = ("command", "cmd")

# THE FOUR CHANNELS (the Operator's 08-15 spec).
CHANNEL_OPERATOR = "operator_channel"
CHANNEL_AGENT = "agent_channel"
CHANNEL_SYSTEM = "system_channel"
CHANNEL_GLOBAL = "global_channel"
_ALL_CHANNELS = (CHANNEL_OPERATOR, CHANNEL_AGENT, CHANNEL_SYSTEM,
                 CHANNEL_GLOBAL)


def _default_store() -> dict:
    """The empty 4-channel store (the CEO's schema).

    THE 08-15 ORDER (highest → lowest security): Global > System > Agent
    > Operator. The GLOBAL channel is NULL by default — it is SKIPPED in
    the gate until the operator sets its flags in the Settings tab. Only
    then does it apply house-wide."""
    return {
        CHANNEL_OPERATOR: {"tools": [], "skills": []},
        CHANNEL_AGENT: {"tools": [], "skills": []},
        CHANNEL_SYSTEM: {"tools": [], "skills": []},
        CHANNEL_GLOBAL: {
            # NULL by default — the gate skips it until the operator
            # sets the flags (the CEO's 08-15 spec).
            "tools": None,
            "skills": None,
        },
    }


def _in_bounds(arguments: dict | None) -> bool:
    """True when the call's work stays INSIDE the platform (.athena/).

    The Operator's bounds rule: work inside the platform (.athena, athena-system,
    sessions, vault, ...) is Athena's own business — approved without a
    prompt. A call that reaches OUTSIDE the platform boundary is the only
    case that needs the user.

    Rules:
      • a path arg outside .athena/          → out of bounds
      • a path arg inside a sanctum write    → out of bounds
      • a URL/scheme in any arg              → out of bounds
      • a command string with a network call → out of bounds
      • NO path/command args at all          → ambiguous → NOT clearly
        in-bounds (an unsafe tool without a bounded target still prompts)
    """
    arguments = arguments or {}
    found_target = False
    for key in _PATH_KEYS:
        val = arguments.get(key)
        if not val or not isinstance(val, str):
            continue
        found_target = True
        if val.startswith(("http://", "https://", "ftp://")) or "://" in val:
            return False  # a URL or scheme — out of bounds
        try:
            from filesystem.safety import resolve
            p = resolve(val)  # raises ScopeError if outside .athena/
            # The sanctum (readme/, athena-system/) is readable but NOT
            # writable — a write into the sanctum is out of bounds too.
            if key in ("path", "dst", "dest", "target") \
                    and _is_sanctum_write(p):
                return False
        except Exception:
            return False
    # Command strings: a network call (curl/wget/ssh/fetch) reaches out.
    for key in _COMMAND_KEYS:
        val = arguments.get(key)
        if not val or not isinstance(val, str):
            continue
        found_target = True
        low = val.lower()
        for token in ("curl ", "wget ", "http://", "https://", "ssh ", "ftp://"):
            if token in low:
                return False
        # THE 08-15 FIX: a command touching a path OUTSIDE the platform
        # (the same tokens the safety check uses) is out of bounds — e.g.
        # "ls /outside", "cat /etc/passwd", "cd /tmp".
        _plat = str(ATHENA_ROOT).lower()
        # The platform may be reached via the real path (/home/...) OR
        # the symlink path (/var/home/...) — match BOTH.
        _plat_alt = str(ATHENA_ROOT.resolve()).lower()
        _in_plat = (_plat in low) or (_plat_alt in low)
        # The real-path form: /home/<user>/.athena (the /var/home
        # symlink's target is the SAME dir) — derive it by replacing a
        # leading /var/home with /home.
        _plat_real = _plat_alt.replace("/var/home/", "/home/", 1)
        if _plat_real != _plat_alt and _plat_real in low:
            _in_plat = True
        for token in ("/etc/", "/usr/", "/var/", "/bin/", "/root/",
                      "/tmp/", "/proc/", "/sys/", "/home/", "/opt/"):
            if token in low and not _in_plat:
                return False
        # A COMMAND-ONLY call (no explicit path key) is AMBIGUOUS — a
        # command can touch anything. Only clearly in-bounds commands
        # (those referencing the platform path) auto-approve; the rest
        # prompt (the Operator's fail-closed rule).
        if _in_plat:
            return True
        return False
    return found_target


def _is_sanctum_write(path) -> bool:
    try:
        from filesystem.safety import SANCTUM_DIRS
        for s in SANCTUM_DIRS:
            try:
                path.relative_to(s.resolve())
                return True
            except ValueError:
                continue
    except Exception:
        pass
    return False


_lock = threading.Lock()


def _rules_path(profile: str = "") -> Path:
    """The permissions rule file — PER PROFILE (the Operator's 08-15 spec).

    Every profile (system or named) owns a permissions.yaml in its OWN
    root: profiles/<name>/permissions.yaml. The default agent
    (profile="" / "default" / ".default") resolves to the .default
    profile's file — the platform ROOT (.athena/permissions.yaml) is
    never used (the Operator's 08-15 call: the root carries no
    permissions; each profile's root does).
    """
    name = (profile or "").strip()
    if not name or name in ("default", ".default"):
        name = ".default"
    return ATHENA_ROOT / "profiles" / name / "permissions.yaml"


def _load_rules(profile: str = "") -> dict:
    """The full 4-channel store. Missing file → the empty schema."""
    p = _rules_path(profile)
    try:
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict) and "global_channel" in data:
                return data
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"permissions rules read failed: {exc}",
                  source="security", action="load_rules")
    # LEGACY JSON (the 08-15 migration): the old permissions.json held
    # {tool: {verdict, scope}} — migrate it into the operator channel.
    try:
        _legacy = p.with_suffix(".json")
        if _legacy.exists():
            data = json.loads(_legacy.read_text(encoding="utf-8"))
            rules = data.get("rules", {}) if isinstance(data, dict) else {}
            store = _default_store()
            for tool, r in rules.items():
                if r.get("verdict") == ALLOW:
                    store[CHANNEL_OPERATOR]["tools"].append(tool)
            _save_rules(profile, store)
            _legacy.unlink(missing_ok=True)
            return store
    except Exception:
        pass
    return _default_store()


def _save_rules(profile: str, store: dict) -> None:
    try:
        p = _rules_path(profile)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(store, sort_keys=False,
                                    allow_unicode=True), encoding="utf-8")
    except Exception as exc:
        from core.logging import log_event
        log_event(4, f"permissions rules write failed: {exc}",
                  source="security", action="save_rules")


def classify(tool: str, arguments: dict) -> str:
    """Classify a tool call's risk: safe | unsafe | blocked.

    The base: tool name. Then content patterns (an rm -rf hidden inside
    an otherwise-safe terminal call makes it unsafe).
    """
    name = (tool or "").strip()
    if name in _BLOCKED_TOOLS:
        return RISK_BLOCKED
    if name in _SAFE_TOOLS:
        return RISK_SAFE
    # Any tool whose arguments carry a destructive pattern is unsafe.
    blob = json.dumps(arguments or {}).lower()
    if any(p.lower() in blob for p in _UNSAFE_PATTERNS):
        return RISK_UNSAFE
    # Write-capable tools default to unsafe (they need a rule/prompt).
    if name in ("write", "append", "replace", "patch", "delete", "copy",
                "move", "rename", "mkdir", "execute", "terminal", "kill"):
        return RISK_UNSAFE
    return RISK_SAFE


def _channel_has(store: dict, channel: str, kind: str, name: str) -> bool:
    return name in (store.get(channel, {}).get(kind) or [])


def _channel_add(store: dict, channel: str, kind: str, name: str) -> None:
    if name not in (store.get(channel, {}).get(kind) or []):
        store.setdefault(channel, {}).setdefault(kind, []).append(name)


def _channel_remove(store: dict, channel: str, kind: str, name: str) -> None:
    lst = store.get(channel, {}).get(kind)
    if lst and name in lst:
        lst.remove(name)


def check(tool: str, arguments: dict | None = None, *, profile: str = "",
          session_id: str = "") -> dict:
    """The gate decision for a tool call.

    Returns a dict: {allowed, verdict, risk, needs_prompt}.
      • blocked tool        → allowed=False, verdict=BLOCK
      • safe tool           → allowed=True,  verdict=ALLOW
      • channel list has it → allowed=True  (its scope was recorded)
      • global flag allows  → its level gates it
      • nothing known       → allowed=False, verdict=NEEDS_PROMPT
    """
    risk = classify(tool, arguments or {})
    if risk == RISK_BLOCKED:
        return {"allowed": False, "verdict": BLOCK, "risk": risk,
                "needs_prompt": False}
    if risk == RISK_SAFE:
        return {"allowed": True, "verdict": ALLOW, "risk": risk,
                "needs_prompt": False}

    store = _load_rules(profile)

    # THE GLOBAL CHANNEL FIRST (the CEO's 08-15 order: Global > System >
    # Agent > Operator — highest security first). NULL by default → the
    # gate SKIPS it and proceeds. Only a SET global applies house-wide:
    #   type=block            → everything of that kind refused
    #   type=allow, level=global → allowed outright
    #   type=allow, level=session → session-gated (prompts per session)
    #   type=deny             → refused unless a channel/session grants
    _kind = "skills" if str(tool).startswith(("skill:", "skill_")) else "tools"
    g = store.get(CHANNEL_GLOBAL, {}).get(_kind) or {}
    if g:
        g_type = str(g.get("type", "")).lower()
        g_level = str(g.get("level", "")).lower()
        if g_type == BLOCK:
            return {"allowed": False, "verdict": BLOCK, "risk": risk,
                    "needs_prompt": False}
        if g_type == ALLOW and g_level == GLOBAL:
            return {"allowed": True, "verdict": ALLOW, "risk": risk,
                    "needs_prompt": False}
        if g_type == ALLOW and g_level == SESSION:
            sr = _session_rules(session_id).get(tool)
            if sr:
                return {"allowed": sr.get("verdict") == ALLOW, "verdict":
                        sr.get("verdict", NEEDS_PROMPT), "risk": risk,
                        "needs_prompt": False}
            return {"allowed": False, "verdict": NEEDS_PROMPT, "risk": risk,
                    "needs_prompt": True}
        if g_type == DENY:
            # Denied globally — only an explicit channel grant overrides.
            for ch in (CHANNEL_SYSTEM, CHANNEL_AGENT, CHANNEL_OPERATOR):
                if _channel_has(store, ch, _kind, tool):
                    return {"allowed": True, "verdict": ALLOW, "risk": risk,
                            "needs_prompt": False}
            return {"allowed": False, "verdict": DENY, "risk": risk,
                    "needs_prompt": False}
        # type=allow, level=once → prompts every time (no grant).
        if g_type == ALLOW and g_level == ONCE:
            return {"allowed": False, "verdict": NEEDS_PROMPT, "risk": risk,
                    "needs_prompt": True}

    # THE CHANNEL LISTS — System > Agent > Operator (the 08-15 order:
    # highest security first). A name in a list = allowed at its recorded
    # scope (populated when the operator allowed at session/global).
    for ch in (CHANNEL_SYSTEM, CHANNEL_AGENT, CHANNEL_OPERATOR):
        if _channel_has(store, ch, _kind, tool):
            return {"allowed": True, "verdict": ALLOW, "risk": risk,
                    "needs_prompt": False}

    # SESSION rules (an allow-session recorded in memory this session).
    sr = _session_rules(session_id).get(tool)
    if sr:
        return {"allowed": sr.get("verdict") == ALLOW,
                "verdict": sr.get("verdict", NEEDS_PROMPT), "risk": risk,
                "needs_prompt": False}

    # IN-BOUNDS (the Operator's bounds rule): an unsafe tool working
    # INSIDE the platform (.athena — sessions, vault, workspace, config)
    # is Athena's own business — approved without a prompt. Only a call
    # that reaches OUTSIDE the platform boundary prompts.
    if _in_bounds(arguments):
        return {"allowed": True, "verdict": ALLOW, "risk": risk,
                "needs_prompt": False}

    return {"allowed": False, "verdict": NEEDS_PROMPT, "risk": risk,
            "needs_prompt": True}


def decide(tool: str, verdict: str, scope: str, *, profile: str = "",
           session_id: str = "") -> bool:
    """Record the user's choice. Returns True if stored.

    THE 08-15 MODEL: an ALLOW at SESSION or GLOBAL scope POPULATES the
    tool/skill BY NAME into the operator/agent channel list. A DENY at
    session/global removes it (or records the deny). ONCE stores nothing.
    """
    verdict = (verdict or "").strip().lower()
    scope = (scope or "").strip().lower()
    if verdict not in _VERDICTS:
        return False
    if scope not in _SCOPES:
        return False
    _kind = "skills" if str(tool).startswith(("skill:", "skill_")) else "tools"

    if scope == GLOBAL:
        with _lock:
            store = _load_rules(profile)
            _ch = CHANNEL_OPERATOR
            if verdict == ALLOW:
                _channel_add(store, _ch, _kind, tool)
            elif verdict == DENY:
                _channel_remove(store, _ch, _kind, tool)
            _save_rules(profile, store)
        return True
    if scope == SESSION:
        with _lock:
            rules = _session_rules(session_id)
            rules[tool] = {"verdict": verdict, "scope": SESSION}
        return True
    # ONCE: no storage — the caller grants it for this call only.
    return True


def set_global_flags(profile: str, kind: str, type_: str, level: str,
                     *, session_id: str = "") -> bool:
    """The Permissions tab's GLOBAL CHANNEL controls (the CEO's spec):
    {tools: {type, level}, skills: {type, level}}. Empty type+level
    CLEARS the flags back to NULL (the channel is skipped in the gate)."""
    if kind not in ("tools", "skills"):
        return False
    with _lock:
        store = _load_rules(profile)
        if not type_ and not level:
            store.setdefault(CHANNEL_GLOBAL, {})[kind] = None
        else:
            type_ = (type_ or "").lower()
            level = (level or "").lower()
            if type_ not in _VERDICTS or level not in _SCOPES:
                return False
            store.setdefault(CHANNEL_GLOBAL, {})[kind] = {"type": type_,
                                                          "level": level}
        _save_rules(profile, store)
    return True


def set_channel_entry(profile: str, channel: str, kind: str, name: str,
                      present: bool) -> bool:
    """The Permissions tab's per-channel list edits (the CEO's spec)."""
    if channel not in (CHANNEL_OPERATOR, CHANNEL_AGENT, CHANNEL_SYSTEM):
        return False
    if kind not in ("tools", "skills"):
        return False
    with _lock:
        store = _load_rules(profile)
        if present:
            _channel_add(store, channel, kind, name)
        else:
            _channel_remove(store, channel, kind, name)
        _save_rules(profile, store)
    return True


def list_rules(profile: str = "") -> dict:
    """The full 4-channel store (the Permissions tab renders this)."""
    return _load_rules(profile)


_session_store: dict[str, dict] = {}


def _session_rules(session_id: str) -> dict:
    if not session_id:
        return {}
    return _session_store.setdefault(session_id, {})


def clear_session(session_id: str) -> None:
    _session_store.pop(session_id, None)

"""Guardrails — the pre-execution safety layer (the Operator's spec).

Works WITH the security system (permissions.py). The division:
  • PERMISSIONS (security/permissions.py): WHO is allowed — the
    Allow/Deny/Block gate on tools, scoped per session/global.
  • GUARDRAILS (this module): WHAT the call is allowed to DO — validate
    the INTENT of a tool/skill/plugin call before the permission gate
    runs: capability checks, argument patterns, resource limits, and
    scope rules. Guardrails run FIRST; permissions run second.

Guardrail verdicts:
  • PASS   — the call is within safe bounds → permissions decides.
  • HOLD   — the call is risky but possibly fine → needs the user/approval.
  • REJECT — the call violates a hard safety rule → refused outright.

Guardrails are layered, so a plugin's tools carry the plugin's scope and
the plugin's safety rules.
"""
from __future__ import annotations

import json
import re
import threading

# -- Verdicts -----------------------------------------------------------

PASS = "pass"
HOLD = "hold"
REJECT = "reject"

# Hard reject patterns: never allowed, even with a prompt.
_REJECT_PATTERNS = [
    # destructive filesystem ops
    re.compile(r"\brm\s+-rf\s+(/|/[\"']|$|[\s\"'])"), re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if=.*\bof=/dev/sd"), re.compile(r"\bchmod\s+-R\s+777\s+/\b"),
    re.compile(r"\b>?\s*/dev/sd"), re.compile(r"\b:\(\)\{\s*\|\s*:\s*&\s*\}\s*;:"),
    # credential exfiltration
    re.compile(r"cat\s+.*(auth|\.env|credential|api_key|token)", re.I),
    re.compile(r"curl.*(api[_-]?key|token|password)=", re.I),
    re.compile(r"git\s+config.*credential"),
    # destructive database
    re.compile(r"\bdrop\s+table\b", re.I),
    re.compile(r"\btruncate\s+table\b", re.I),
    # process/network weaponry
    re.compile(r"\bkill\s+-9\s+1\b"),
]

# Argument keys that always need a HOLD (destructive intent markers).
_HOLD_ARG_KEYS = {
    "path", "target", "destination", "command", "pattern", "query",
}
_HOLD_ARG_VALUES = [
    re.compile(r"^/etc/"), re.compile(r"^/boot/"),
    re.compile(r"^/dev/sd"), re.compile(r"^/var/lib"),
    re.compile(r"\.env$"), re.compile(r"auth\.json$"),
    re.compile(r"permissions\.json$"),
]

# Capability categories a call can ask for.
CAP_READ = "read"
CAP_WRITE = "write"
CAP_EXEC = "exec"
CAP_NETWORK = "network"
CAP_ADMIN = "admin"

# Tool name → the capability it needs (the guardrail's capability map).
_TOOL_CAPABILITIES: dict[str, str] = {
    # read-only
    "read": CAP_READ, "list": CAP_READ, "tree": CAP_READ, "find": CAP_READ,
    "search": CAP_READ, "exists": CAP_READ, "stat": CAP_READ,
    "memory_list": CAP_READ, "vault_query": CAP_READ, "logs": CAP_READ,
    "status": CAP_READ, "doctor": CAP_READ, "help": CAP_READ,
    "config": CAP_READ, "web_search": CAP_READ, "web_extract": CAP_READ,
    "browser_open": CAP_READ,
    # write
    "write": CAP_WRITE, "append": CAP_WRITE, "replace": CAP_WRITE,
    "patch": CAP_WRITE, "delete": CAP_WRITE, "copy": CAP_WRITE,
    "move": CAP_WRITE, "rename": CAP_WRITE, "mkdir": CAP_WRITE,
    "vault_store": CAP_WRITE, "memory_add": CAP_WRITE,
    # exec
    "execute": CAP_EXEC, "terminal": CAP_EXEC,
    # network
    "web_search": CAP_NETWORK, "web_extract": CAP_NETWORK,
    "browser_open": CAP_NETWORK,
    # admin
    "restart": CAP_ADMIN, "kill": CAP_ADMIN, "provider": CAP_ADMIN,
    "integration": CAP_ADMIN, "runtime": CAP_ADMIN,
}

_lock = threading.Lock()


# -- The core check -----------------------------------------------------

def check(kind: str, name: str, arguments: dict | None = None,
          *, capability: str = "") -> dict:
    """The guardrail gate for a tool/skill/plugin call.

    kind: "tool" | "skill" | "plugin"
    name: the tool/skill/plugin name
    arguments: the call's arguments (tool calls)
    capability: the caller's declared capability (empty = auto from the
        tool map for tools, or the skill/plugin's declared scope)

    Returns {verdict, reason, capability}.
    """
    arguments = arguments or {}
    blob = json.dumps(arguments)

    # 1. HARD REJECT patterns — never allowed.
    for pat in _REJECT_PATTERNS:
        if pat.search(blob):
            return {"verdict": REJECT, "reason": f"matches {pat.pattern}",
                    "capability": capability}

    # 2. Argument path/value HOLD markers — risky targets.
    for key in _HOLD_ARG_KEYS:
        val = arguments.get(key)
        if isinstance(val, str):
            for vpat in _HOLD_ARG_VALUES:
                if vpat.search(val):
                    return {"verdict": HOLD,
                            "reason": f"{key}={val!r} targets a sensitive path",
                            "capability": capability}

    # 3. Capability check: the call's declared capability must cover it.
    needed = capability
    if not needed and kind == "tool":
        needed = _TOOL_CAPABILITIES.get(name, CAP_READ)
    if needed == CAP_ADMIN and kind != "plugin":
        return {"verdict": HOLD, "reason": f"{kind} {name} needs admin",
                "capability": needed}
    if needed == CAP_EXEC and kind == "tool":
        # Exec-capable tools are HOLD by default (the permission gate then
        # prompts) unless the arguments are clearly safe.
        if _blob_safe(arguments):
            return {"verdict": PASS, "reason": "exec with safe arguments",
                    "capability": needed}
        return {"verdict": HOLD, "reason": "exec-capable call",
                "capability": needed}

    # 4. Unknown tools default to PASS (the permission gate still decides).
    return {"verdict": PASS, "reason": "within guardrails",
            "capability": needed}


def _blob_safe(arguments: dict) -> bool:
    """A best-effort safe check for exec arguments (no destructive intent)."""
    blob = json.dumps(arguments).lower()
    for pat in _REJECT_PATTERNS:
        if pat.search(blob):
            return False
    return True


# -- Registry (plugins register their guardrails) -----------------------

# plugin/skill name → declared capabilities
_DECLARED: dict[str, dict] = {}


def declare(name: str, *, capabilities: list[str],
            description: str = "") -> None:
    """A plugin/skill declares what it can do (its guardrail scope)."""
    with _lock:
        _DECLARED[name] = {
            "capabilities": capabilities,
            "description": description,
        }




def registry_status() -> dict:
    """Every declared plugin/skill guardrail scope."""
    with _lock:
        return {k: dict(v) for k, v in _DECLARED.items()}

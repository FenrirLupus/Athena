"""Commands — the command registry (dynamic, refreshable, RECURSIVE).

The CLI's module list is NOT static: every module registers here, and the
auto-completer reads this registry on startup and on /refresh. New commands
from plugins/skills register and immediately appear in tab completion.

The registry supports INFINITELY DEEP command chains (the Operator's spec —
future-proofing for dynamic-sized commands and structure):

    register_command("kanban", ["board", "list", "add"], help="...")
    register_command("kanban", {"board": {"list": [], "add": ["label", "title"]}})

Subcommands may be:
  - a LIST of leaf strings:            ["board", "list"]
  - a NESTED dict (children with their own children):
        {"board": ["list"], "work": {"create": ["title", "label"]}}
  - a list mixing both:                ["list", {"work": ["create"]}]

    list_commands()       → the full registry (top-level module names)
    get_children(name, path) → the children of a node at any depth
    command_tree(name)    → the recursive subtree for one module
"""
from __future__ import annotations

from threading import Lock

# name → {"children": <tree>, "help": "..."}
# <tree> is a dict: node-name → {"children": <tree>, "leaf": bool}
_REGISTRY: dict[str, dict] = {}
_LOCK = Lock()


def _node() -> dict:
    return {"children": {}, "leaf": True}


def _normalize(subcommands) -> dict:
    """Normalize the flexible subcommand shapes into a PURE USER SHAPE.

    Returns {name: {child: ...}} — every value is a dict of its children
    (leaves are {}). This is NOT the stored node shape; _merge_tree
    converts it. Keeping the shapes separate is what prevents the
    "children"/"leaf" key-collision bug.
    """
    tree: dict = {}

    def add(name: str, value) -> None:
        if isinstance(value, dict):
            sub = {}
            for k, v in value.items():
                add(k, v)
                sub[k] = _children_of(k)
            tree[name] = sub
        elif isinstance(value, (list, tuple)):
            sub = {}
            for item in value:
                if isinstance(item, dict):
                    for k, v in item.items():
                        add(k, v)
                        sub[k] = _children_of(k)
                elif isinstance(item, str):
                    sub[item.lower()] = {}
            tree[name] = sub
        else:
            tree[name] = {}

    def _children_of(name: str) -> dict:
        # Fetch whatever add() stored under name — the dict of children.
        return tree.get(name, {})

    if isinstance(subcommands, dict):
        for k, v in subcommands.items():
            add(k, v)
            # add() already set tree[k]; ensure it's the sub-dict.
            tree[k] = _children_of(k)
    elif isinstance(subcommands, (list, tuple)):
        for sub in subcommands:
            if isinstance(sub, dict):
                for k, v in sub.items():
                    add(k, v)
                    tree[k] = _children_of(k)
            elif isinstance(sub, str):
                tree.setdefault(sub.lower(), {})
    return tree


def _merge_tree(target: dict, source: dict) -> None:
    """Merge a USER-SHAPE source ({name: {children...}}) into target nodes.

    target: the stored node dict ({name: {"children": ..., "leaf": ...}}).
    source: the normalized user shape ({name: {child: ...}}) — every
    value is a dict of THAT node's children, never the node wrapper.
    """
    for name, children in source.items():
        t = target.setdefault(name, _node())
        t["leaf"] = False  # has children → not a leaf
        if isinstance(children, dict) and children:
            _merge_tree(t["children"], children)


def register_command(name: str, subcommands=None, help_text: str = "") -> None:
    """Register a command module. Idempotent — re-registering merges.

    subcommands: a LIST of strings (leaf children), a NESTED dict
    (children with their own children — arbitrary depth), or a mix.
    """
    name = name.lower()
    with _LOCK:
        entry = _REGISTRY.setdefault(name, _node())
        entry["help"] = help_text or entry.get("help", "")
        tree = _normalize(subcommands)
        if tree:
            entry["leaf"] = False
            _merge_tree(entry["children"], tree)


def list_commands() -> list[str]:
    """All registered module names, sorted."""
    with _LOCK:
        return sorted(_REGISTRY)


def get_subcommands(name: str) -> list[str]:
    """The immediate children of a module (for backward compat)."""
    with _LOCK:
        entry = _REGISTRY.get(name.lower())
        if not entry:
            return []
        return sorted(entry.get("children", {}).keys())


def get_children(name: str, path: list[str] | None = None) -> list[str]:
    """The children of a node at ANY depth.

    path: the chain under the module, e.g. ["board"] for kanban's board
    children, ["board", "add"] for kanban board add's children.
    """
    with _LOCK:
        node = _REGISTRY.get(name.lower())
        if not node:
            return []
        cur = node
        for seg in (path or []):
            nxt = cur.get("children", {}).get(seg.lower())
            if not nxt:
                return []
            cur = nxt
        return sorted(cur.get("children", {}).keys())


def is_leaf(name: str, path: list[str] | None = None) -> bool:
    """Is the node at this depth a leaf (no further children)?"""
    with _LOCK:
        node = _REGISTRY.get(name.lower())
        if not node:
            return True
        cur = node
        for seg in (path or []):
            nxt = cur.get("children", {}).get(seg.lower())
            if not nxt:
                return True
            cur = nxt
        return bool(cur.get("leaf", True)) or not cur.get("children", {})




def refresh_commands() -> int:
    """Re-read registrations. Returns the current module count."""
    with _LOCK:
        return len(_REGISTRY)


def register_core_commands() -> None:
    """Register the built-in command surface (native)."""
    register_command("send", help_text="send <text> — chat with the runtime")
    register_command("session", ["new", "list"], help_text="session new|list")
    register_command("status", help_text="status — db + server health")
    register_command("kanban", ["board", "list", "add", "update", "decompose", "judge",
                                "delegate", "ask", "spawn", "subagents"],
                     help_text="kanban board|list|add|update|decompose|judge|delegate|ask|spawn|subagents")
    # A DEEPER chain (the Operator's spec — infinitely deep chains): kanban
    # board add takes label/title args; kanban board list takes a status.
    register_command("kanban", {"board": {"add": ["label", "title"],
                                          "list": ["status", "all"]}})
    register_command("cron", ["list", "add", "remove"],
                     help_text="cron list|add <name> <schedule> <prompt>|remove")
    register_command("profile", ["list", "show", "create", "switch", "current"],
                     help_text="profile list|show <name>|create <name>|switch <name>|current")
    register_command("security", help_text="security — integrity check")
    register_command("backup", ["-q", "-l", "import"],
                     help_text="backup [-q] [-l LABEL] | backup import <zip>")
    register_command("skills", help_text="skills — list available skills")
    register_command("plugins", help_text="plugins — list discovered plugins")
    register_command("tools", help_text="tools — list registered tools")
    register_command("config", help_text="config — show loaded config")
    register_command("version", help_text="version — show version info")
    register_command("doctor", help_text="doctor — run health checks")
    register_command("logs", help_text="logs — show recent server logs")
    register_command("lifecycle", ["start", "shutdown", "restart", "refresh"],
                     help_text="lifecycle start|shutdown|restart|refresh")
    register_command("nurse", ["consult", "status"],
                     help_text="nurse consult <task_id> | nurse status — the repair agent")
    register_command("events", ["usage", "summary"],
                     help_text="events [usage] — the agent activity log (levels 1-2)")
    register_command("curator", ["scan", "review", "run"],
                     help_text="curator scan|review|run — the learn-by-doing brain")
    register_command("provider", ["list", "switch", "model"],
                     help_text="provider list|switch <name>|model list|switch <name>")
    register_command("model", ["list", "switch"],
                     help_text="model list|switch <name> — the reason model")
    register_tool_commands()


def register_tool_commands() -> None:
    """Auto-register every registered TOOL as a command (the Operator's rule)."""
    try:
        from filesystem.tools import TOOLS
        for name in sorted(TOOLS.keys()):
            register_command(name, help_text=f"{name} — filesystem/utility tool")
    except Exception:
        pass
    register_command("help", help_text="help — this help")
    register_command("quit", help_text="quit — exit")

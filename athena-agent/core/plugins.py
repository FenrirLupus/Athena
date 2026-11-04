"""Plugins — the widest layer you may add: a program with a PLUGIN.md.

The layers, from the system outward, lose power and gain familiarity:

    System  →  Plugins  →  Tools  →  Skills
    program     a program    one verb    prose

A plugin is the closest thing to the system that is still *yours to add*. It runs in-process, it may register any
number of tools, and it is handed the config and the roots. That is why it has the most ceremony and the strictest
gate:

    .athena/plugins/<name>/PLUGIN.md      what it is, and which script is the entry point
    .athena/plugins/<name>/scripts/       its code
    .athena/plugins/<name>/assets/        data, templates, anything else it needs

```markdown
---
name: clock
description: Current date and time, as a tool.
entry: scripts/plugin.py
---

# clock

Registers `now`, which returns the local time and weekday.
```

`scripts/plugin.py` exposes one function:

```python
def register(registry, ctx):        # ctx carries cfg, profile_root, athena_root — never a credential
    registry.register(Tool(...))
```

**Three rules, because this is arbitrary code in the agent's process:**

1. **Nothing loads unless named in `plugins.enabled`.** A folder is not permission; a line in `config.yaml` is.
2. **A broken plugin is skipped and reported, never fatal** — startup survives, and the reason is visible in
   `./athena.sh plugins`.
3. **A plugin is trusted exactly as far as the person who enabled it.** It runs in-process and is *not*
   scope-checked; one that touches the filesystem must call `Scope.check()` itself.
4. **It must be additive.** `Scope.check`, `_flags`, `ApprovalBroker.request` and the registry's `invoke`/`register`/
   `bind` are recorded before any plugin runs and compared after; anything rebound is **restored** and the plugin is
   rejected with its tools removed. A plugin can add a capability; it cannot widen one.

A profile may hold its own plugins in `profiles/<lane>/plugins/`, but `plugins/` and `config.yaml` inside a profile
are read-only to the agent — otherwise it could write a plugin and its enable line, which is self-modification with
an extra step.
"""
from __future__ import annotations

import importlib.util
import traceback
from pathlib import Path

from tools.registry import Tool

DEFAULT_ENTRY = "scripts/plugin.py"
LEGACY_ENTRIES = ("scripts/plugin.py", "plugin.py")


class PluginError(RuntimeError):
    """A plugin that will not start, or a child process that stopped answering."""

# The modules that decide what an agent may do. A plugin is in-process Python, so "additive" cannot be assumed: the
# whole surface of these modules (and of the classes in them) is recorded before any plugin runs and restored after,
# rather than trusting a hand-written list of function names. A name list was the first attempt and it missed two
# things: attributes inside a dotted class path (keyed wrongly, so never restored) and helpers nobody thought of
# (`core.paths.Path`, whose replacement made every path check permissive while the plugin was still "rejected").
_CRITICAL_MODULES = (
    "core.paths",
    "core.approvals",
    "core.library",
    "core.secrets",
    "tools.registry",
    "tools.filesystem",
    "tools.terminal",
    "tools.library",
    "tools.skills",
    "tools.system",
)
_CRITICAL_CLASSES = (
    ("core.paths", "Scope"),
    ("core.approvals", "ApprovalBroker"),
    ("tools.registry", "Registry"),
    ("tools.registry", "Tool"),
    ("tools.filesystem", "Context"),
)


def _snapshot() -> dict:
    """Every public attribute of the modules that enforce the rules, plus the classes inside them."""
    import importlib

    found: dict[tuple, object] = {}
    for module_name in _CRITICAL_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for attribute in dir(module):
            if attribute.startswith("__"):
                continue
            found[(module_name, None, attribute)] = getattr(module, attribute, None)
    for module_name, class_name in _CRITICAL_CLASSES:
        try:
            owner = getattr(importlib.import_module(module_name), class_name, None)
        except ImportError:
            continue
        if owner is None:
            continue
        for attribute in dir(owner):
            if attribute.startswith("__"):
                continue
            found[(module_name, class_name, attribute)] = getattr(owner, attribute, None)
    return found


def _restore(snapshot: dict) -> list[str]:
    """Put back anything a plugin rebound. Returns the names that had to be restored."""
    import importlib

    restored = []
    cache: dict[str, object] = {}
    for (module_name, owner_name, attribute), original in snapshot.items():
        if module_name not in cache:
            try:
                cache[module_name] = importlib.import_module(module_name)
            except ImportError:
                cache[module_name] = None
        module = cache[module_name]
        if module is None:
            continue
        target = getattr(module, owner_name, None) if owner_name else module
        if target is None:
            continue
        current = getattr(target, attribute, None)
        if current is not original:
            try:
                setattr(target, attribute, original)
                restored.append(f"{module_name}.{owner_name + '.' if owner_name else ''}{attribute}")
            except (AttributeError, TypeError):
                continue
    return restored


# Source patterns that mean "this is not an additive plugin". A plugin adds logic and registers tools; anything that
# reads a file, runs a process or reaches the network belongs in a *tool* (TOOL.md + scripts/), which runs as a
# contained subprocess with credentials masked. A match is a refusal, not a warning.
FORBIDDEN = (
    ("import core", "imports the system layer"),
    ("from core", "imports the system layer"),
    ("import tools", "reaches into the tool implementations"),
    ("from tools import", "reaches into the tool implementations"),
    ("sys.modules", "reaches loaded modules directly"),
    ("__import__", "dynamic import"),
    ("importlib", "dynamic import"),
    ("open(", "opens files"),
    ("subprocess", "runs processes"),
    ("os.system", "runs processes"),
    ("os.popen", "runs processes"),
    ("socket", "opens the network"),
    ("ctypes", "native code"),
    ("setattr(", "rebinds attributes"),
    ("eval(", "evaluates code"),
    ("exec(", "evaluates code"),
)


def scan_source(path) -> list[str]:
    """Static check: what a plugin's code is allowed to be.

    A plugin is meant to add logic and register tools. Reading files, spawning processes or importing the system layer
    is not a plugin's job — that is a *tool* (`TOOL.md` + `scripts/`), which runs as a contained subprocess with
    credentials masked. So the source is scanned before it is imported, and a match is a refusal, not a warning.
    """
    from pathlib import Path

    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ["source could not be read"]
    hits = []
    for needle, reason in FORBIDDEN:
        if needle in text:
            hits.append(f"{reason} ({needle!r})")
    return hits


def probe_guard(profile_root, athena_root) -> list[str]:
    """Ask the guard to refuse something it must always refuse. Catches any patch in the call chain.

    Identity checks only see the functions they name. This runs the real thing: if a plugin has replaced a helper two
    levels down, `Scope.check` will accept a system write and this notices.
    """
    import importlib
    from pathlib import Path

    problems = []
    paths_mod = importlib.import_module("core.paths")
    target = Path(athena_root) / "athena-agent" / "core" / "loop.py"
    try:
        scope = paths_mod.for_profile(profile_root, athena_root)
    except Exception as exc:                          # noqa: BLE001
        return [f"Scope.for_profile is broken: {type(exc).__name__}: {exc}"]

    try:
        scope.check(str(target), paths_mod.WRITE)
        problems.append("Scope.check allowed a write into ATHENA_SYSTEM")
    except ValueError:
        pass
    except Exception as exc:                          # noqa: BLE001
        problems.append(f"Scope.check raised {type(exc).__name__} instead of refusing")

    try:
        filesystem = importlib.import_module("tools.filesystem")
        ctx = filesystem.Context(scope=scope, approvals=None)
        ctx.profile_root, ctx.athena_root, ctx.cfg = Path(profile_root), Path(athena_root), {}
        ctx.check(str(target), paths_mod.WRITE, "probe")
        problems.append("filesystem.Context.check allowed a write into ATHENA_SYSTEM")
    except Exception:                                 # noqa: BLE001
        pass
    return problems


class Context:
    """What a plugin is given: paths, config, and nothing secret."""

    def __init__(self, cfg: dict, profile_root=None, athena_root=None):
        self.cfg = cfg
        self.profile_root = Path(profile_root) if profile_root else None
        self.athena_root = Path(athena_root) if athena_root else None


def roots(profile_root: Path | None = None, athena_root: Path | None = None) -> list[Path]:
    """Profile plugins first (they may shadow a shared name), then the shared directory."""
    found = []
    if profile_root is not None:
        found.append(Path(profile_root) / "plugins")
    if athena_root is not None:
        found.append(Path(athena_root) / "plugins")
    return [p for p in found if p.is_dir()]


def _frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    meta: dict = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            body = text[end + 4:].lstrip("\n")
            for line in text[3:end].splitlines():
                if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
                    continue
                key, _, value = line.partition(":")
                meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    return meta, body


def discover(profile_root: Path | None = None, athena_root: Path | None = None) -> list[dict]:
    """Every plugin folder that exists, whether or not it is enabled."""
    seen: dict[str, dict] = {}
    for base in roots(profile_root, athena_root):
        for folder in sorted(base.iterdir()):
            if not folder.is_dir() or folder.name.startswith(".") or folder.name in seen:
                continue
            descriptor = folder / "PLUGIN.md"
            meta, body = {}, ""
            if descriptor.is_file():
                try:
                    meta, body = _frontmatter(descriptor)
                except OSError:
                    pass
            provides = str(meta.get("provides") or "").lower()
            wanted = str(meta.get("entry") or "").strip()
            candidates = [wanted] if wanted else list(LEGACY_ENTRIES)
            entry = next((folder / c for c in candidates if c and (folder / c).is_file()), None)
            # A bundle of skills has nothing to execute: it contributes prose, not tools. Treating the missing entry
            # as "broken" made every skills-only plugin fail its install smoke test and get rolled back.
            skills_only = entry is None and "skills" in provides
            scripts = folder / "scripts"
            seen[folder.name] = {
                "name": folder.name,
                "dir": str(folder),
                "descriptor": str(descriptor) if descriptor.is_file() else "",
                "has_descriptor": descriptor.is_file(),
                "entry": str(entry) if entry else "",
                "has_entry": entry is not None or skills_only,
                "skills_only": skills_only,
                "provides": provides,
                "description": str(meta.get("description") or (body.strip().splitlines() or [""])[0]),
                "scripts": sorted(p.name for p in scripts.iterdir() if p.is_file()) if scripts.is_dir() else [],
                "references": sorted(str(p.relative_to(folder / "references")) for p in (folder / "references").rglob("*")
                                     if p.is_file()) if (folder / "references").is_dir() else [],
                "assets": sorted(str(p.relative_to(folder / "assets")) for p in (folder / "assets").rglob("*")
                                 if p.is_file()) if (folder / "assets").is_dir() else [],
                "origin": "profile" if profile_root and str(base).startswith(str(profile_root)) else "shared",
            }
    return sorted(seen.values(), key=lambda p: p["name"])


def enabled_names(cfg: dict) -> list[str]:
    block = ((cfg or {}).get("plugins") or {})
    listed = block.get("enabled") or []
    if isinstance(listed, str):
        listed = [n.strip() for n in listed.split(",")]
    return [str(n).strip() for n in listed if str(n).strip()]


class Child:
    """One plugin process. Reads a JSON line per call, with a deadline — a hung plugin must not hang the turn."""

    def __init__(self, argv: list[str], timeout: float = 30.0):
        import subprocess

        self.timeout = float(timeout)
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1)
        self.specs: list[dict] = []

    def _readline(self) -> dict:
        import select
        import json as _json

        ready, _, _ = select.select([self.proc.stdout], [], [], self.timeout)
        if not ready:
            raise TimeoutError(f"plugin did not answer within {self.timeout:.0f}s")
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("plugin process closed its output")
        return _json.loads(line)

    def handshake(self) -> list[dict]:
        first = self._readline()
        if not first.get("ok"):
            raise PluginError(first.get("error") or "plugin failed to load")
        self.specs = first.get("tools") or []
        return self.specs

    def call(self, name: str, args: dict) -> str:
        import json as _json

        if self.proc.poll() is not None:
            raise PluginError("plugin process has exited")
        self.proc.stdin.write(_json.dumps({"call": name, "args": args}) + "\n")
        self.proc.stdin.flush()
        reply = self._readline()
        return str(reply.get("result") or "")

    def close(self) -> None:
        try:
            self.proc.terminate()
        except Exception:                             # noqa: BLE001
            pass


_CHILDREN: dict[str, "Child"] = {}


def _sandbox_argv(entry: Path, athena_root) -> list[str]:
    """bwrap: read-only root, credentials masked, no writable tree — the plugin computes and nothing else."""
    import shutil
    import sys

    bwrap = shutil.which("bwrap")
    # A plugin that declared dependencies has its own venv; run its code on that interpreter, not the server's.
    plugin_python = entry.parent.parent / ".venv" / "bin" / "python3"
    python = str(plugin_python) if plugin_python.exists() else sys.executable
    if not bwrap:
        return [python, str(Path(__file__).with_name("plugin_runner.py")), str(entry.parent), str(entry)]
    argv = [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
            "--die-with-parent", "--new-session", "--unshare-pid"]
    try:
        from tools.terminal import _mask_args

        argv += _mask_args(athena_root, None)
    except Exception:                                 # noqa: BLE001 — masking is best-effort here
        pass
    argv += [python, str(Path(__file__).with_name("plugin_runner.py")), str(entry.parent), str(entry)]
    return argv


def _load_out_of_process(name: str, info: dict, registry, athena_root) -> dict:
    """Start (or reuse) the plugin's process and register proxy tools that talk to it."""
    entry = Path(info["entry"])
    key = f"{name}:{entry}:{int(entry.stat().st_mtime)}"
    child = _CHILDREN.get(key)
    if child is None:
        # The key carries the entry's mtime, so an edit or a reinstall produces a new key; the previous child is closed
        # rather than left running under the server.
        for stale in [k for k in list(_CHILDREN) if k.split(":", 1)[0] == name and k != key]:
            try:
                _CHILDREN.pop(stale).close()
            except Exception:                        # noqa: BLE001 — a dead child must not block the new one
                pass
        child = Child(_sandbox_argv(entry, athena_root))
        child.handshake()
        _CHILDREN[key] = child
    added = []
    for spec in child.specs:
        def make(spec=spec):
            def call(**kwargs):
                return child.call(spec["name"], kwargs)
            return call

        try:
            registry.register(Tool(name=spec["name"], description=spec.get("description") or "",
                                   parameters=spec.get("parameters") or {"type": "object", "properties": {}},
                                   fn=make()))
            added.append(spec["name"])
        except Exception:                             # noqa: BLE001 — a name clash is the parent's, not the child's
            continue
    return {"name": name, "tools": added, "origin": info["origin"], "entry": str(entry),
            "isolation": "process"}


def _record(athena_root, kind: str, detail: str, path: str = "", lane: str = "", agent_id: str = "") -> None:
    """Write a rejection into the security ledger — the red-team subsystem's evidence.

    `lane` and `agent_id` are the attribution: a row that cannot say which agent tried which plugin is a row an
    operator can only read as noise.
    """
    if not athena_root:
        return
    try:
        from core import security

        security.Ledger(Path(athena_root) / "security").record("red", kind, detail, path,
                                                              lane=lane, agent_id=agent_id)
    except Exception:                                # noqa: BLE001
        pass


def lane_plugins(lane_cfg) -> dict | None:
    """The lane's `plugins:` block, or None when it has none (which means: inherit the ceiling)."""
    block = (lane_cfg or {}).get("plugins")
    return block if isinstance(block, dict) else None


def lane_enabled(global_cfg: dict, lane_cfg) -> tuple[list[str], list[str], str]:
    """(allowed, blocked, malformed) for one lane.

    The global `plugins.enabled` is the ceiling — it is the list a human edited in the root config. A lane may narrow
    it and never widen it: a name the lane lists but the ceiling does not is *blocked*, reported, and never loaded.
    A lane block that is not a mapping is a typo; it inherits the ceiling and says so.
    """
    ceiling = enabled_names(global_cfg)
    block = (lane_cfg or {}).get("plugins")
    if block is None:
        return ceiling, [], ""
    if not isinstance(block, dict):
        return ceiling, [], f"plugins must be a mapping, got {type(block).__name__}"
    listed = block.get("enabled")
    if listed is None:
        return ceiling, [], ""
    if isinstance(listed, str):
        listed = [n.strip() for n in listed.split(",")]
    if not isinstance(listed, (list, tuple)):
        return ceiling, [], f"plugins.enabled must be a list, got {type(listed).__name__}"
    wanted = [str(n).strip() for n in listed if str(n).strip()]
    allowed = [name for name in ceiling if name in wanted]
    blocked = [name for name in wanted if name not in ceiling]
    return allowed, blocked, ""


def load_all(cfg: dict, registry, profile_root=None, athena_root=None, lane_cfg=None) -> dict:
    """Load the plugins this agent may run. Returns what loaded, what was skipped, and why.

    Reporting happens *before* the empty short-circuit: a lane narrowed to a name outside the ceiling has an empty
    allow-list, and that is exactly the case an operator needs to see a row for.
    """
    want, blocked, malformed = lane_enabled(cfg, lane_cfg)
    lane = Path(profile_root).name if profile_root else ""
    agent_id = ""
    if profile_root:
        try:
            from core import identity

            agent_id = identity.id_for(profile_root)
        except Exception:                            # noqa: BLE001 — an un-adopted lane is reported, not fatal
            agent_id = ""
    report = {"enabled": want, "blocked": blocked, "malformed": malformed,
              "loaded": [], "skipped": [], "missing": []}
    for name in blocked:
        _record(athena_root, "plugin-not-permitted",
                f"{name} is not in the global plugins.enabled ceiling, so this lane may not run it",
                path=str(profile_root or ""), lane=lane, agent_id=agent_id)
    if malformed:
        _record(athena_root, "plugin-block-malformed", malformed,
                path=str(profile_root or ""), lane=lane, agent_id=agent_id)
    if not want:
        return report
    found = {p["name"]: p for p in discover(profile_root, athena_root)}
    ctx = Context(cfg, profile_root, athena_root)
    baseline = _snapshot()          # pristine system layer, before any plugin code runs
    for name in want:
        info = found.get(name)
        if info is None or not info["has_entry"]:
            report["missing"].append(name)
            continue
        isolation = str(((cfg or {}).get("plugins") or {}).get("isolation") or "process").lower()
        before_tools = set(registry.names())
        # The scan exists because an in-process plugin can do anything undetected. Out of process, the sandbox is the
        # boundary: `open()` simply fails on a masked file or a read-only root, so banning the call would be theatre.
        violations = scan_source(info["entry"]) if isolation != "process" else []
        if violations:
            report["skipped"].append({
                "name": name,
                "error": "not an additive plugin: " + "; ".join(violations),
                "violation": violations})
            _record(athena_root, "plugin-rejected", f"not additive: {'; '.join(violations)}", info["entry"])
            continue
        if info.get("skills_only"):
            skills_dir = Path(info["dir"]) / "skills"
            found = sorted(p.parent.name for p in skills_dir.glob("*/SKILL.md")) if skills_dir.is_dir() else []
            report["loaded"].append({"name": name, "tools": [], "origin": info["origin"], "entry": "",
                                     "isolation": "none (skills bundle)", "skills": found})
            continue

        if isolation == "process":
            try:
                report["loaded"].append(_load_out_of_process(name, info, registry, athena_root))
            except Exception as exc:                  # noqa: BLE001 — a plugin that will not start is skipped
                report["skipped"].append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
            continue

        try:
            spec = importlib.util.spec_from_file_location(f"athena_plugin_{name}", info["entry"])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            register = getattr(module, "register", None)
            if not callable(register):
                raise PluginError(f"{Path(info['entry']).name} has no register(registry, ctx)")
            before = set(registry.names())
            register(registry, ctx)

            # additive, not overriding: anything the plugin rebound is put back before its tools are kept
            tampered = _restore(baseline)
            if tampered:
                for tool_name in set(registry.names()) - before_tools:
                    registry.discard(tool_name)
                report["skipped"].append({
                    "name": name,
                    "error": f"tampered with the system layer: {', '.join(tampered)} — restored, plugin rejected",
                    "violation": tampered})
                _record(athena_root, "plugin-tampered", ", ".join(tampered), info["entry"])
                continue

            # a patch two levels down evades identity checks; run the guard and see if it still refuses
            problems = probe_guard(profile_root, athena_root) if (profile_root and athena_root) else []
            if problems:
                for tool_name in set(registry.names()) - before_tools:
                    registry.discard(tool_name)
                report["skipped"].append({
                    "name": name,
                    "error": "weakened the guard: " + "; ".join(problems),
                    "violation": problems})
                _record(athena_root, "plugin-weakened-guard", "; ".join(problems), info["entry"])
                continue

            added = sorted(set(registry.names()) - before)
            report["loaded"].append({"name": name, "tools": added, "origin": info["origin"],
                                     "entry": info["entry"]})
        except Exception as exc:                      # noqa: BLE001 — a bad plugin must not stop the server
            for tool_name in set(registry.names()) - before_tools:
                registry.discard(tool_name)
            report["skipped"].append({"name": name, "error": f"{type(exc).__name__}: {exc}",
                                      "trace": traceback.format_exc(limit=3).splitlines()[-3:]})
    return report

"""The tool loader — a tool is a package, and packages are the only source of tools.

    <root>/tools/<name>/TOOL.md              what it is, and what it does
    <root>/tools/<name>/scripts/<name>.py    the code it runs
    <root>/tools/<name>/references/          optional: text it carries

A bare module is not a tool. The capability the program ships lives in exactly the same shape a user installs, so
there is one rule to read and one path to test — the absence of a `builtins.py` catalogue and a per-tool import in
`Registry.bind` is the point of this module.

Three roots are searched, lowest precedence first:

    program   athena-agent/tools/          the built-ins that ship with Athena
    home      <ATHENA_ROOT>/tools/         installed packs, and the required `system/` category
    profile   <profile_root>/tools/        one lane's own tools

`system/` under any root is the required category: it loads for every agent, because a missing `filesystem` is not a
preference. Everything else loads only when named in `tools.packs`.

A descriptor from a nearer root replaces one with the same folder name, and the replacement is reported. A descriptor
that cannot be read is skipped with the reason, and a broken one never stops a boot: the report carries it.

`kind:` says how the code runs. There is no separate kind for a built-in: a built-in is the same package, shipped in
the program's `system/` category, and the only difference is that its script is trusted enough to run in-process (see
`_load_one`).

| kind | the descriptor names | runs |
|---|---|---|
| `script` | `script: scripts/<name>.py` | a shipped `system/` package: imported by path and called as `run(args, ctx)`; an installed pack: a contained subprocess with JSON arguments |
| `command` | `command: ["bin", "arg"]` | argv built from the arguments, no shell |
| `http` | `url`, optional `method` | one request per call, no credentials injected |

`actions:` lists the verbs inside the capability and the argument schema carries the matching `action` enum; the
loader checks the two agree, so a descriptor cannot promise a verb its schema does not offer. One descriptor registers
**one Tool** — `filesystem`, `kanban`, `sql` — and the verbs are what the model picks inside it.
"""
from __future__ import annotations

import importlib.abc
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import types
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tools.registry import Tool, ToolError
from core import references as references_mod
from core import trace

KINDS = ("script", "command", "http")
PROGRAM_TOOLS = Path(__file__).resolve().parent
MAX_OUTPUT = 20000
INTERPRETERS = {".py": "python3", ".sh": "bash", ".bash": "bash", ".js": "node", ".rb": "ruby", ".pl": "perl"}
_SKIP_DIRS = {"system", "scripts", "references", "assets", "__pycache__"}
_RANK = {"program": 0, "home": 1, "profile": 2}

# one module object per script path: a test that patches `terminal.shutil.which` must patch the same module the
# registered tools came from, and two descriptors pointing at one script must not load it twice.
_MODULES: dict[str, types.ModuleType] = {}


class DescriptorError(ValueError):
    """A package that does not meet the standard. Recorded in the report; never raised out of a boot."""


class PackError(DescriptorError):
    """A descriptor that cannot be turned into a Tool."""


def _lane(source) -> str:
    """The lane a loader line belongs to: a calling context's channel/profile, or a descriptor's ATHENA_HOME.

    Two sources because the loader runs at two times — `dispatch` has the agent's context, a contained script only
    has the scrubbed environment it was handed. Neither is a secret; both name the lane the turn belongs to.
    """
    if isinstance(source, dict):
        home = str((source.get("_env") or {}).get("ATHENA_HOME") or "")
        return Path(home).name if home else "-"
    name = str(getattr(source, "channel", "") or "")
    root = getattr(source, "profile_root", None)
    return name or (Path(root).name if root else "-")


def dispatch(build, args, ctx, name: str):
    """The one entry point a package's script exposes: `action` selects the verb, the rest are its arguments.

    A descriptor registers one Tool, so the script is what knows its verbs. `build(ctx)` already lists them as Tools —
    one per verb, each carrying its own argument schema — and an action resolves to the Tool named exactly that, or to
    the single Tool whose name ends in `_<action>` (`tables` → `sql_tables`). Anything else is a refusal that names the
    verbs that do exist, because a model that guessed wrong should learn the list.

    Resolution is traced (`→ dispatch`, then `← dispatch` with the verb it chose, how it matched, and the verbs that
    were on offer); a refusal raises as before and is recorded as a failure against the same span.
    """
    call = dict(args or {})
    if "action" in call:
        wanted = call.pop("action")
    elif "verb" in call:
        wanted = call.pop("verb")
    else:
        wanted = ""
    wanted = str(wanted or "").strip().lower()
    with trace.span("dispatch", channel=_lane(ctx), tool=name, action=wanted or "-") as sp:
        tools = {tool.name: tool for tool in build(ctx)}
        via = "single"
        if not wanted:
            if len(tools) != 1:
                raise ToolError(f"{name}: an action is required; expected one of {', '.join(sorted(tools))}")
            tool = next(iter(tools.values()))
        else:
            tool = tools.get(wanted)
            via = "exact"
            if tool is None:
                suffixed = [candidate for key, candidate in tools.items() if key.endswith(f"_{wanted}")]
                tool = suffixed[0] if len(suffixed) == 1 else None
                via = "suffix"
        if tool is None:
            raise ToolError(f"{name}: unknown action {wanted!r}; expected one of {', '.join(sorted(tools))}")
        sp.ok(verb=tool.name, via=via, declared=sorted(tools))
    return tool.fn(**call)


# -- contexts -----------------------------------------------------------------

def action_map(build, ctx=None) -> dict:
    """{action: callable} for one package's script — the verbs a capability offers, callable in-process.

    The loader needs this to dispatch; a suite needs it to test one verb without going through a registry.
    """
    return {tool.name: tool.fn for tool in build(ctx)}


def agent_roots(ctx) -> tuple[Path | None, Path]:
    """(profile_root, athena_root) for a tool script: what the calling agent has, else the machine's home."""
    athena_root = getattr(ctx, "athena_root", None)
    if athena_root is None:
        from core.root import athena_root as default_root

        athena_root = default_root()
    profile_root = getattr(ctx, "profile_root", None)
    return (Path(profile_root) if profile_root else None), Path(athena_root)


class _DefaultContext:
    """A context for a load with no agent behind it — a CLI listing, or a plugin installer's probe.

    The tools are built (so they can be listed) and every call that needs a scope refuses, which is the honest
    answer when no agent is bound.
    """

    def __init__(self, profile_root=None, athena_root=None, cfg=None):
        self.profile_root = Path(profile_root) if profile_root else None
        self.athena_root = Path(athena_root) if athena_root else None
        self.cfg = cfg or {}
        self.scope = None
        self.approvals = None
        self.agent_id = ""
        self.provider = None
        self.is_subagent = False


def ensure_ctx(ctx, profile_root=None, athena_root=None, cfg=None):
    """The context a script's `build()` is called with: the caller's, or a minimal honest one."""
    if ctx is not None:
        return ctx
    return _DefaultContext(profile_root, athena_root, cfg)


# -- discovery ----------------------------------------------------------------

def bases(profile_root=None, athena_root=None, program_tools=PROGRAM_TOOLS) -> list[tuple[str, Path]]:
    """The roots to search, lowest precedence first. A root that is not there is simply absent."""
    candidates = []
    if program_tools is not None:
        candidates.append(("program", Path(program_tools)))
    if athena_root is not None:
        candidates.append(("home", Path(athena_root) / "tools"))
    if profile_root is not None:
        candidates.append(("profile", Path(profile_root) / "tools"))
    return [(origin, base) for origin, base in candidates if base.is_dir()]


def _folders(base: Path) -> list[tuple[Path, bool]]:
    """Every tool folder in one root: `<base>/*/` then the required `<base>/system/*/`."""
    out: list[tuple[Path, bool]] = []
    if not base.is_dir():
        return out
    for entry in sorted(base.iterdir()):
        if entry.is_dir() and not entry.name.startswith((".", "_")) and entry.name not in _SKIP_DIRS:
            out.append((entry, False))
    system = base / "system"
    if system.is_dir():
        for entry in sorted(system.iterdir()):
            if entry.is_dir() and not entry.name.startswith((".", "_")):
                out.append((entry, True))
    return out


def _script_of(folder: Path, meta: dict) -> Path:
    """The script a descriptor names, resolved inside its own folder — `..` is not a location for code."""
    root = Path(folder).resolve()
    declared = str(meta.get("script") or "").strip()
    if declared:
        target = (root / declared).resolve()
        if target != root and root not in target.parents:
            raise DescriptorError(f"script {declared!r} points outside the tool folder")
        if not target.is_file():
            raise DescriptorError(f"script not found: {declared}")
        return target
    scripts = root / "scripts"
    candidates = sorted(p for p in scripts.glob("*") if p.is_file()) if scripts.is_dir() else []
    if len(candidates) != 1:
        raise DescriptorError("declare `script:` in TOOL.md (or leave exactly one file in scripts/)")
    return candidates[0]


def _describe(folder: Path, in_system: bool, origin: str) -> dict:
    """One folder's descriptor, read and checked. Problems are collected, never raised."""
    descriptor = folder / "TOOL.md"
    scripts = folder / "scripts"
    references = folder / "references"
    row = {
        "name": folder.name,
        "dir": str(folder),
        "descriptor": str(descriptor) if descriptor.is_file() else "",
        "has_descriptor": descriptor.is_file(),
        "kind": "",
        "tool_name": "",
        "script": "",
        "description": "",
        "parameters": {},
        "problems": [],
        "scripts": sorted(p.name for p in scripts.iterdir() if p.is_file()) if scripts.is_dir() else [],
        "references": sorted(str(p.relative_to(references)) for p in references.rglob("*") if p.is_file())
        if references.is_dir() else [],
        "origin": origin,
        "system": in_system,
    }
    if not descriptor.is_file():
        row["problems"].append("no TOOL.md")
        return row
    try:
        from core import frontmatter

        meta, _body = frontmatter.parse(descriptor.read_text(encoding="utf-8"), typed=True)
    except OSError as exc:
        row["problems"].append(f"TOOL.md could not be read: {exc}")
        return row
    if not meta:
        row["problems"].append("TOOL.md has no frontmatter block")
        return row
    row["kind"] = str(meta.get("kind") or "").strip().lower()
    row["tool_name"] = str(meta.get("name") or "").strip()
    row["description"] = str(meta.get("description") or "").strip()
    row["parameters"] = meta.get("parameters") or {}
    if not row["tool_name"]:
        row["problems"].append("descriptor declares no name")
    if not row["kind"]:
        row["problems"].append("descriptor does not declare kind")
    elif row["kind"] not in KINDS:
        row["problems"].append(f"unknown kind {row['kind']!r}; expected one of {', '.join(KINDS)}")
    elif row["kind"] == "script":
        try:
            row["script"] = str(_script_of(folder, meta))
            if in_system:
                # a system package is the program's own code (shipped or installed into system/), so a script that
                # defines run(args, ctx) is called in-process — the only way it can ask the scope guard
                source = Path(row["script"]).read_text(encoding="utf-8", errors="replace")
                row["in_process"] = "def run(" in source
                if origin == "program" and not row["in_process"]:
                    row["problems"].append("a built-in script must define run(args, ctx)")
        except DescriptorError as exc:
            row["problems"].append(str(exc))
    elif row["kind"] == "command" and not meta.get("command"):
        row["problems"].append("kind: command without command")
    elif row["kind"] == "http" and not meta.get("url"):
        row["problems"].append("kind: http without url")
    try:
        row["actions"] = _actions(meta, row["parameters"])
    except DescriptorError as exc:
        row["problems"].append(str(exc))
    return row


def discover_with_notes(profile_root=None, athena_root=None,
                        program_tools=PROGRAM_TOOLS) -> tuple[list[dict], list[dict]]:
    """Every package, plus what the nearer roots replaced. Precedence is per folder name."""
    found: dict[str, dict] = {}
    notes: list[dict] = []
    for origin, base in bases(profile_root, athena_root, program_tools):
        for folder, in_system in _folders(base):
            row = _describe(folder, in_system, origin)
            previous = found.get(row["name"])
            if previous is None:
                found[row["name"]] = row
                continue
            if not row["problems"] and not previous["problems"]:
                found[row["name"]] = row
                notes.append({"name": row["name"], "origin": origin, "replaced": previous["origin"],
                              "reason": "a nearer descriptor of the same name wins"})
            elif row["problems"]:
                # an unreadable descriptor must not take a working tool away: it is reported instead
                notes.append({"name": row["name"], "origin": origin, "kept": previous["origin"],
                              "reason": "; ".join(row["problems"])})
            else:
                found[row["name"]] = row
                notes.append({"name": row["name"], "origin": origin, "replaced": previous["origin"],
                              "reason": "the outer descriptor is malformed"})
    return sorted(found.values(), key=lambda r: (_RANK.get(r["origin"], 9), r["name"])), notes


def discover(profile_root=None, athena_root=None, program_tools=PROGRAM_TOOLS) -> list[dict]:
    """Every tool package, enabled or not."""
    return discover_with_notes(profile_root, athena_root, program_tools)[0]


def enabled_names(cfg: dict) -> list[str]:
    """`tools.packs` — not `tools.enabled`, which is the boolean that turns the whole toolset on or off."""
    block = (cfg or {}).get("tools") or {}
    listed = block.get("packs") or []
    if isinstance(listed, str):
        listed = [n.strip() for n in listed.split(",")]
    return [str(n).strip() for n in listed if str(n).strip()]


def system_names(athena_root=None) -> list[str]:
    """The required category of one root: everything under `<root>/tools/system/` with a TOOL.md."""
    if athena_root is None:
        return []
    folder = Path(athena_root) / "tools" / "system"
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_dir() and (p / "TOOL.md").is_file())


# -- importing a script by path ----------------------------------------------

def import_script(path) -> types.ModuleType:
    """Import one script from its file, once. The module name never collides with a package."""
    target = Path(path).resolve()
    key = str(target)
    if key in _MODULES:
        return _MODULES[key]
    slug = re.sub(r"[^a-z0-9_]+", "_", target.with_suffix("").as_posix().lower()).strip("_")
    name = f"athena_tool_{slug}"
    spec = importlib.util.spec_from_file_location(name, target)
    if spec is None or spec.loader is None:
        raise DescriptorError(f"cannot import {target}")
    module = importlib.util.module_from_spec(spec)
    _MODULES[key] = module            # before exec: a script that reaches for itself by path gets this object
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        _MODULES.pop(key, None)
        sys.modules.pop(name, None)
        raise
    return module


def descriptor_named(name: str, profile_root=None, athena_root=None,
                     program_tools=PROGRAM_TOOLS) -> dict | None:
    """The package called `name`, nearest valid root first (home over program, profile over both)."""
    rows, _notes = discover_with_notes(profile_root, athena_root, program_tools)
    for row in reversed(rows):
        if row["name"] == name and row["script"] and not row["problems"]:
            return row
    return None


def script_module(name: str):
    """The module behind `tools.<name>`, loaded by path — the one source of the implementation.

    This is what makes `from tools.firewall import ...` mean "the script of that package" without a `.py` shim for
    every tool. It returns None when no package of that name exists, and the import then fails as it should.
    """
    row = descriptor_named(name)
    if row is None or not row["script"]:
        return None
    try:
        return import_script(row["script"])
    except Exception:                  # noqa: BLE001 — an import hook must not turn a missing package into a crash
        return None


class _Preloaded:
    """A loader for a module that is already imported: it hands back the one instance."""

    def __init__(self, module):
        self._module = module

    def create_module(self, spec):
        return self._module

    def exec_module(self, module):
        return None


class ToolModuleFinder(importlib.abc.MetaPathFinder):
    """Resolves `tools.<name>` to `<package>/scripts/<name>.py`.

    Appended after `PathFinder`, so a real submodule (`tools.registry`, `tools.loader`) always wins and only a name
    with no file behind it is answered from a package script. Without it, deleting the shims would have broken
    `from tools.filesystem import Context` in the server — and a shim kept alive to satisfy an import is exactly the
    second source of truth this refactor removes.
    """

    def find_spec(self, fullname, path=None, target=None):
        prefix = f"{__package__ or 'tools'}."
        if not fullname.startswith(prefix):
            return None
        rest = fullname[len(prefix):]
        if "." in rest or not rest:
            return None
        module = script_module(rest)
        if module is None:
            return None
        return importlib.util.spec_from_loader(fullname, _Preloaded(module))


def install_finder() -> None:
    """Put the finder at the end of `sys.meta_path`, once."""
    if not any(isinstance(finder, ToolModuleFinder) for finder in sys.meta_path):
        sys.meta_path.append(ToolModuleFinder())


# -- subprocess kinds (script / command / http) -------------------------------

_SENSITIVE = re.compile(r"(TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL|API_KEY)", re.I)


def _athena_root_of(folder: Path) -> Path | None:
    """`.athena/tools/<name>` → `.athena`, without guessing for a pack installed somewhere else."""
    parent = Path(folder).parent
    return parent.parent if parent.name == "tools" else None


def _scrubbed_env(extra: dict | None = None) -> dict:
    """The minimum a pack needs — never the server's whole environment."""
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
           "LANG": os.environ.get("LANG", "C.UTF-8"),
           "HOME": os.environ.get("HOME", "/tmp")}
    for key, value in (extra or {}).items():
        if not _SENSITIVE.search(str(key)):
            env[str(key)] = str(value)
    return env


def _contained(argv: list[str], cwd: Path, athena_root: Path | None) -> list[str]:
    """bwrap around a pack script: read-only root, credentials masked, writable only where the agent may write."""
    if argv and "/" not in str(argv[0]):
        name = str(argv[0])
        # a python pack runs on THIS process's interpreter: PATH's first `python3` is often a venv outside the
        # sandbox (and the credential masks hide ~/.hermes entirely), so resolving through PATH produced
        # "execvp python3: No such file or directory" — a tool that looks broken rather than contained
        argv = [str(Path(sys.executable).resolve()) if name.startswith("python") else (shutil.which(name) or name),
                *argv[1:]]
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return argv                       # no sandbox available: the caller still gets the scrubbed env
    out = [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
           "--die-with-parent", "--new-session", "--unshare-pid"]
    # writable only where the agent may write; `cwd` is "." when a pack is loaded with no agent context, and binding
    # that would have handed the pack write access to the program tree
    try:
        where = Path(cwd).resolve()
        profiles = (Path(athena_root).resolve() / "profiles") if athena_root else None
        if profiles and where.is_dir() and where.is_relative_to(profiles):
            out += ["--bind", str(where), str(where)]
    except (OSError, ValueError):
        pass
    if athena_root is not None:
        try:
            terminal = script_module("terminal")
            out += terminal._mask_args(athena_root, None)
        except Exception:                 # noqa: BLE001 — masking is best-effort, containment is not
            pass
    return out + argv


def _run_script(meta: dict, args: dict, folder: Path, cwd: Path) -> dict:
    if str(meta.get("script") or "").strip():
        target = Path(folder) / str(meta.get("script")).strip()
    else:
        target = Path(meta.get("_script") or "")
    if not target.is_file():
        raise PackError(f"script not found: {meta.get('script') or target.name}")

    interpreter = str(meta.get("interpreter") or "").strip() or INTERPRETERS.get(target.suffix.lower(), "")
    payload = json.dumps(args, ensure_ascii=False)
    argv = ([interpreter] if interpreter else []) + [str(target), payload]
    timeout = float(meta.get("timeout_s") or 20)
    env = _scrubbed_env(meta.get("_env"))
    env["ATHENA_ARGS"] = payload
    argv = _contained(argv, Path(cwd), _athena_root_of(folder))
    lane = _lane(meta)
    contained = bool(argv) and Path(str(argv[0])).name == "bwrap"
    # one span per process: which script, how it ran, how big the argv was, what it exited with and how long it took
    with trace.span("tool.script", channel=lane, script=target.name, mode="contained" if contained else "direct",
                    argv=len(argv), timeout_s=timeout) as sp:
        try:
            done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                                  shell=False, env=env)
        except subprocess.TimeoutExpired as exc:
            trace.event("tool.script.timeout", level="info", channel=lane, script=target.name, timeout_s=timeout)
            sp.failed(exc)
            return {"ok": False, "error": f"timed out after {timeout:.0f}s"}
        except OSError as exc:
            sp.failed(exc)                    # a process that never started is a failure, not an empty result
            return {"ok": False, "error": str(exc)}
        out = (done.stdout or "") + (("\n" + done.stderr) if done.stderr else "")
        sp.ok(exit=done.returncode, bytes=len(out))
        return {"ok": done.returncode == 0, "exit": done.returncode, "output": out[:MAX_OUTPUT]}


def _run_command(meta: dict, args: dict, folder: Path, cwd: Path) -> dict:
    argv = []
    for part in meta.get("command") or []:
        text = str(part)
        for key, value in args.items():
            text = text.replace("{" + str(key) + "}", str(value))
        argv.append(text)
    if not argv:
        raise PackError("command is empty")
    timeout = float(meta.get("timeout_s") or 20)
    program = Path(str(argv[0])).name
    argv = _contained(argv, Path(cwd), _athena_root_of(folder))
    lane = _lane(meta)
    contained = bool(argv) and Path(str(argv[0])).name == "bwrap"
    with trace.span("tool.command", channel=lane, program=program, mode="contained" if contained else "direct",
                    argv=len(argv), timeout_s=timeout) as sp:
        try:
            done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, shell=False,
                                  env=_scrubbed_env(meta.get("_env")))
        except subprocess.TimeoutExpired as exc:
            trace.event("tool.command.timeout", level="info", channel=lane, program=program, timeout_s=timeout)
            sp.failed(exc)
            return {"ok": False, "error": f"timed out after {timeout:.0f}s"}
        except OSError as exc:
            sp.failed(exc)
            return {"ok": False, "error": str(exc)}
        out = (done.stdout or "") + (("\n" + done.stderr) if done.stderr else "")
        sp.ok(exit=done.returncode, bytes=len(out))
        return {"ok": done.returncode == 0, "exit": done.returncode, "output": out[:MAX_OUTPUT]}


def _run_http(meta: dict, args: dict) -> dict:
    method = str(meta.get("method") or "GET").upper()
    url = str(meta.get("url") or "")
    if not url:
        raise PackError("url is missing")
    for key, value in args.items():
        url = url.replace("{" + str(key) + "}", urllib.parse.quote(str(value)))
    timeout = float(meta.get("timeout_s") or 20)
    data = json.dumps(args).encode() if method == "POST" else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"content-type": "application/json", "accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read(MAX_OUTPUT).decode("utf-8", "replace")
        try:
            return {"ok": True, "json": json.loads(text)}
        except json.JSONDecodeError:
            return {"ok": True, "text": text}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": exc.read(400).decode("utf-8", "replace")}
    except Exception as exc:                          # noqa: BLE001 — a tool must fail as data, not as a crash
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _parameters(raw) -> dict:
    """`parameters:` may be a full JSON Schema or the shorthand `{name: {type, description, required}}`."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise PackError(f"parameters: is not JSON: {exc}") from exc
    if raw in (None, "", {}):
        return {"type": "object", "properties": {}}
    if not isinstance(raw, dict):
        raise PackError("parameters: must be a JSON object")
    if isinstance(raw.get("properties"), dict) and raw.get("type", "object") == "object":
        properties = {}
        for key, info in raw["properties"].items():
            info = info if isinstance(info, dict) else {}
            properties[key] = {k: v for k, v in info.items() if k in ("type", "description", "enum")}
            properties[key] = properties[key] or {"type": "string"}
        return {"type": "object", "properties": properties,
                "required": [str(name) for name in (raw.get("required") or [])]}
    properties, required = {}, []
    for key, info in raw.items():
        info = info if isinstance(info, dict) else {}
        property_schema = {k: v for k, v in info.items() if k in ("type", "description", "enum")}
        properties[key] = property_schema or {"type": "string"}
        if info.get("required"):
            required.append(key)
    return {"type": "object", "properties": properties, "required": required}


def _actions(meta: dict, parameters) -> list[str]:
    """The verbs a descriptor declares, checked against the schema it publishes.

        actions: add, list, dispatch, close
        parameters: '{"type": "object", "properties": {"action": {"type": "string",
                        "enum": ["add", "list", "dispatch", "close"]}, ...}}'

    Two spellings, one meaning: `actions:` is canonical, `verbs:` is accepted from descriptors written before the key
    settled. The list is what the tool advertises; the enum is what a call may contain, so if they disagree the
    descriptor is inconsistent and is reported rather than trusted.
    """
    declared = meta.get("actions", meta.get("verbs"))
    if declared in (None, ""):
        declared = []
    elif isinstance(declared, str):
        text = declared.strip()
        if text.startswith("["):
            try:
                declared = json.loads(text)
            except json.JSONDecodeError as exc:
                raise DescriptorError(f"actions: is not JSON: {exc}") from exc
        else:
            declared = [part.strip() for part in text.split(",") if part.strip()]
    if not isinstance(declared, (list, tuple)):
        raise DescriptorError("actions: must be a comma-separated list")
    actions = [str(entry).strip().lower() for entry in declared if str(entry).strip()]
    schema = _parameters(parameters or {}) if parameters else {}
    enum = [str(entry).strip().lower() for entry in
            ((schema.get("properties") or {}).get("action") or {}).get("enum") or []]
    if actions and enum and sorted(actions) != sorted(enum):
        raise DescriptorError(f"actions {actions} and the action enum {enum} disagree")
    return actions or enum


def _tool_from(meta: dict, row: dict, cwd: Path, env: dict, *, parameters: dict | None = None,
               description: str = "") -> Tool:
    """A descriptor as the one Tool it registers, run contained with JSON arguments."""
    name = str(meta.get("name") or "").strip()
    if not name:
        raise PackError("name is required in TOOL.md")
    kind = str(meta.get("kind") or "script").lower()
    if kind not in KINDS:
        raise PackError(f"unknown kind {kind!r}; expected one of {', '.join(KINDS)}")
    folder = Path(row["dir"])
    meta = dict(meta, _env=env, _script=row.get("script") or "")

    def run(**kwargs):
        if kind == "command":
            return _run_command(meta, dict(kwargs), folder, cwd)
        if kind == "http":
            return _run_http(meta, dict(kwargs))
        return _run_script(meta, dict(kwargs), folder, cwd)

    return Tool(name=name, description=description or str(meta.get("description") or f"tool {name}"),
                parameters=parameters or _parameters(meta.get("parameters")), fn=run)


# -- registration -------------------------------------------------------------

def _claim(registry, tool: Tool, row: dict, report: dict, owner: dict) -> bool:
    """Register one Tool, unless its name is taken. A collision is reported, never a boot failure."""
    if tool.name in owner:
        report["conflicts"].append({"tool": tool.name, "declared_by": row["name"],
                                    "already_provided_by": owner[tool.name]})
        return False
    try:
        registry.register(tool)
    except Exception as exc:                          # noqa: BLE001 — a duplicate name is information
        report["conflicts"].append({"tool": tool.name, "declared_by": row["name"], "already_provided_by": "",
                                    "error": f"{type(exc).__name__}: {exc}"})
        return False
    owner[tool.name] = row["name"]
    return True


def _load_one(row: dict, registry, ctx, report: dict, owner: dict, cwd: Path, env: dict) -> None:
    """Register the one Tool a descriptor declares.

    A script shipped in the program's `system/` category is imported by path and called as `run(args, ctx)`: it is the
    program's own code, and it needs the live context — the filesystem tool asks the scope guard before every call, and
    a subprocess has no scope to ask. A script installed under the home or a profile is user-authored, so it runs as a
    contained subprocess with JSON arguments and a scrubbed environment.
    """
    if row["problems"]:
        report["skipped"].append({"name": row["name"], "origin": row["origin"],
                                  "error": "; ".join(row["problems"])})
        return
    from core import frontmatter

    meta, _body = frontmatter.parse(Path(row["descriptor"]).read_text(encoding="utf-8"), typed=True)
    in_process = bool(row.get("in_process")) and row["kind"] == "script"
    # the descriptor and the documents it points at are what a caller needs to operate the tool, so they travel with
    # it: the routing line first (that is what the model selects on), then the body, then the references
    attached = references_mod.attach(row["dir"], meta)
    about = " ".join([row["description"], f"({Path(row['script']).name if row['script'] else row['kind']}; "
                                          f"actions: {', '.join(row.get('actions') or []) or 'none'})"])
    body_text = (_body or "").strip()
    description = about + (f"\n\n{body_text}" if body_text else "") + attached["text"]
    if in_process:
        module = import_script(row["script"])
        entry = getattr(module, "run", None)
        if not callable(entry):
            raise DescriptorError(f"{Path(row['script']).name} defines no run(args, ctx)")
        lane = _lane(ctx)
        script_name = Path(row["script"]).name

        def call(entry=entry, **arguments):
            # the other half of `mode=`: a system package runs here, in this process, so the line says so and the
            # verb it was asked for is the one fact the registry's own span cannot see
            with trace.span("tool.script", channel=lane, script=script_name, mode="in-process",
                            action=str(arguments.get("action") or "-")) as sp:
                out = entry(arguments, ctx)
                sp.ok(size=len(out) if isinstance(out, (str, bytes, list, dict, tuple)) else 1)
            return out

        tool = Tool(name=row["tool_name"], description=description,
                    parameters=_parameters(meta.get("parameters")), fn=call)
    else:
        tool = _tool_from(meta, row, cwd, env, description=description)
    _claim(registry, tool, row, report, owner)
    report["loaded"].append({"name": row["name"], "tool": tool.name, "kind": row["kind"],
                             "actions": row.get("actions") or [], "in_process": in_process,
                             "references": {"attached": attached["attached"], "omitted": attached["omitted"],
                                            "bytes": attached["bytes"], "problems": attached["problems"]},
                             "origin": row["origin"], "required": row["system"], "scripts": row["scripts"]})


def load(registry, ctx=None, profile_root=None, athena_root=None, cfg=None,
         program_tools=PROGRAM_TOOLS) -> dict:
    """Register every required package and every named pack. One pass; nothing here can stop a boot."""
    if profile_root is None:
        profile_root = getattr(ctx, "profile_root", None)
    if athena_root is None:
        athena_root = getattr(ctx, "athena_root", None)
    if athena_root is None:
        from core.root import athena_root as default_root

        athena_root = default_root()
    cfg = cfg if isinstance(cfg, dict) else (getattr(ctx, "cfg", None) or {})
    want = enabled_names(cfg)
    rows, notes = discover_with_notes(profile_root, athena_root, program_tools)
    report = {"enabled": want,
              "required": [row["name"] for row in rows if row["system"]],
              "loaded": [], "skipped": [], "missing": [], "conflicts": [], "overridden": notes}

    selected = [row for row in rows if row["system"]]
    for name in want:
        row = next((candidate for candidate in rows if candidate["name"] == name), None)
        if row is None or not row["has_descriptor"]:
            report["missing"].append(name)
        elif row not in selected:
            selected.append(row)

    cwd = (Path(profile_root) / "sandbox") if profile_root else Path(".")
    env = {}
    if profile_root is not None:
        home = Path(profile_root)
        athena = Path(athena_root)
        env = {"ATHENA_ROOT": str(athena), "ATHENA_HOME": str(home),
               "ATHENA_SANDBOX": str(home / "sandbox"), "ATHENA_WORKSPACE": str(home / "workspace"),
               "ATHENA_SYSTEM": str(athena / "athena-agent")}

    ctx = ensure_ctx(ctx, profile_root, athena_root, cfg)
    owner: dict[str, str] = {}
    for row in selected:
        try:
            _load_one(row, registry, ctx, report, owner, cwd, env)
        except Exception as exc:                      # noqa: BLE001 — a broken package is reported, not fatal
            report["skipped"].append({"name": row["name"], "origin": row["origin"],
                                      "error": f"{type(exc).__name__}: {exc}"})
    return report


def load_all(cfg: dict, registry, profile_root=None, athena_root=None, ctx=None) -> dict:
    """The historical name, for callers that read `core.toolpacks`."""
    return load(registry, ctx=ctx, profile_root=profile_root, athena_root=athena_root, cfg=cfg)

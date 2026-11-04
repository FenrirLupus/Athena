"""Tools — a verb with its own code, described by a TOOL.md.

The layers, from the system outward, lose power and gain familiarity:

    System  →  Plugins  →  Tools  →  Skills
    program     a program    one verb    prose

A tool is the layer below a plugin: it *does* something (unlike a skill, which is prose), but it is one declared
verb with its own script rather than a program that can register anything (unlike a plugin).

    .athena/tools/<name>/TOOL.md      what it is, and what arguments it takes
    .athena/tools/<name>/scripts/     the code it actually runs

```markdown
---
name: disk_usage
description: Free space and mount point for the filesystem holding a path.
kind: script
script: scripts/disk_usage.py
interpreter: python3
timeout_s: 20
parameters: {"path": {"type": "string", "description": "Any path on that filesystem.", "required": true}}
---

# disk_usage

Reports `df -h` for whatever filesystem holds the given path.
```

Three kinds:

| kind | runs | note |
|---|---|---|
| `script` | a file under `scripts/`, with the interpreter its extension asks for | arguments arrive as JSON |
| `command` | an argv with `{placeholders}` filled in | no shell, so a value cannot become a second command |
| `http` | GET/POST a URL, parsed JSON back | no credentials injected |

**Arguments reach a script as JSON**, in `ATHENA_ARGS` and as `argv[1]`, so a script never parses a command line
and a value containing a space or a quote cannot change what runs:

```python
import json, os, sys
args = json.loads(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ATHENA_ARGS", "{}"))
print(args["path"])
```

The five `ATHENA_*` variables are exported to the script, and its `cwd` is the agent's `sandbox/`.

**Who may write one.** `.athena/tools/` is inside `ATHENA_ROOT`, which is read-and-list for agents: an agent sees
the tools and cannot author one. A tool is a capability, and capabilities come from the person.

**Trust.** A tool's script is user-authored code running with the server's privileges — the scope guard does not
police it, exactly as it does not police a plugin. That is why the folder is not agent-writable.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tools.registry import Tool

KINDS = ("script", "command", "http", "builtin")
MAX_OUTPUT = 20000
INTERPRETERS = {".py": "python3", ".sh": "bash", ".bash": "bash", ".js": "node", ".rb": "ruby", ".pl": "perl"}


class PackError(ValueError):
    pass


def _frontmatter(path: Path) -> tuple[dict, str]:
    """`---` block plus body. Values may be JSON (`{...}`, `[...]`), a quoted string, a number or a bool."""
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
                meta[key.strip().lower()] = _value(value.strip())
    return meta, body


def _value(raw: str):
    text = raw.strip()
    if not text:
        return ""
    if text[0] in "{[":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        return text


def discover(profile_root: Path | None = None, athena_root: Path | None = None) -> list[dict]:
    """Every tool folder, enabled or not, nearest first (a profile tool shadows a shared one by name)."""
    bases = []
    if profile_root is not None:
        bases.append(Path(profile_root) / "tools")
    if athena_root is not None:
        bases.append(Path(athena_root) / "tools")
    found: dict[str, dict] = {}
    for base in bases:
        if not base.is_dir():
            continue
        # the required category lives one level deeper: tools/system/<name>/
        entries = list(base.iterdir()) + [sub for sub in (base / "system").iterdir()
                                          if (base / "system").is_dir() for sub in [sub]] \
            if (base / "system").is_dir() else list(base.iterdir())
        for folder in sorted(entries):
            if not folder.is_dir() or folder.name.startswith(".") or folder.name in found:
                continue
            descriptor = folder / "TOOL.md"
            scripts = folder / "scripts"
            references = folder / "references"
            found[folder.name] = {
                "name": folder.name,
                "dir": str(folder),
                "descriptor": str(descriptor) if descriptor.is_file() else "",
                "has_descriptor": descriptor.is_file(),
                "scripts": sorted(p.name for p in scripts.iterdir() if p.is_file()) if scripts.is_dir() else [],
                "references": sorted(str(p.relative_to(references)) for p in references.rglob("*") if p.is_file())
                if references.is_dir() else [],
                "origin": "system" if folder.parent.name == "system" else
                          ("profile" if profile_root and str(base).startswith(str(profile_root)) else "shared"),
            }
    return sorted(found.values(), key=lambda p: p["name"])


def enabled_names(cfg: dict) -> list[str]:
    """`tools.packs`, not `tools.enabled` — the latter is the boolean that turns the whole toolset on or off."""
    block = (cfg or {}).get("tools") or {}
    listed = block.get("packs") or []
    if isinstance(listed, str):
        listed = [n.strip() for n in listed.split(",")]
    return [str(n).strip() for n in listed if str(n).strip()]


def system_names(athena_root: Path | None = None) -> list[str]:
    """The required category: everything under `tools/system/`, always loaded.

    A system tool is the program's own capability described in the tools directory, so it is not something a user
    opts into — leaving `filesystem` out of `tools.packs` would leave an agent unable to read a file.
    """
    if athena_root is None:
        return []
    folder = Path(athena_root) / "tools" / "system"
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_dir() and (p / "TOOL.md").is_file())


_SENSITIVE = re.compile(r"(TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL|API_KEY)", re.I)


def _athena_root_of(folder: Path) -> Path | None:
    """`.athena/tools/<name>` → `.athena`, without guessing for a pack installed somewhere else."""
    parent = Path(folder).parent
    return parent.parent if parent.name == "tools" else None


def _scrubbed_env(extra: dict | None = None) -> dict:
    """The minimum a pack needs — never the server's whole environment.

    `dict(os.environ)` handed every provider key, the API token and the operator's session variables to user-installed
    pack code, while the documentation promised the opposite. A tool is told what it needs and nothing else.
    """
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
           "LANG": os.environ.get("LANG", "C.UTF-8"),
           "HOME": os.environ.get("HOME", "/tmp")}
    for key, value in (extra or {}).items():
        if not _SENSITIVE.search(str(key)):
            env[str(key)] = str(value)
    return env


def _contained(argv: list[str], cwd: Path, athena_root: Path | None) -> list[str]:
    """bwrap around a pack script: read-only root, credentials masked, writable only where the agent may write."""
    # an absolute interpreter: inside the sandbox PATH may resolve differently, and `execvp python3` failing is a
    # silent "the tool is broken" rather than an honest containment error
    if argv and "/" not in str(argv[0]):
        name = str(argv[0])
        # a python pack runs on THIS process's interpreter: PATH's first `python3` is often a venv outside the
        # sandbox (and the credential masks hide ~/.hermes entirely), so resolving through PATH produced
        # "execvp python3: No such file or directory" — a tool that looks broken rather than contained
        argv = [str(Path(sys.executable).resolve()) if name.startswith("python") else (shutil.which(name) or name), *argv[1:]]
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return argv                       # no sandbox available: the caller still gets the scrubbed env
    out = [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
           "--die-with-parent", "--new-session", "--unshare-pid"]
    # Writable only where the agent may write. `cwd` is "." when a pack is loaded without an agent context, and
    # binding that would have handed the pack write access to the program tree — the opposite of containment.
    try:
        where = Path(cwd).resolve()
        profiles = (Path(athena_root).resolve() / "profiles") if athena_root else None
        if profiles and where.is_dir() and where.is_relative_to(profiles):
            out += ["--bind", str(where), str(where)]
    except (OSError, ValueError):
        pass
    if athena_root is not None:
        try:
            from tools.terminal import _mask_args

            out += _mask_args(athena_root, None)
        except Exception:                 # noqa: BLE001 — masking is best-effort, containment is not
            pass
    return out + argv


def _run_script(meta: dict, args: dict, folder: Path, cwd: Path) -> dict:
    relative = str(meta.get("script") or "").strip()
    if not relative:
        candidates = sorted(p for p in (folder / "scripts").glob("*") if p.is_file()) \
            if (folder / "scripts").is_dir() else []
        if len(candidates) != 1:
            raise PackError("script: is required in TOOL.md (or leave exactly one file in scripts/)")
        target = candidates[0]
    else:
        target = folder / relative
    if not target.is_file():
        raise PackError(f"script not found: {relative or target.name}")

    interpreter = str(meta.get("interpreter") or "").strip() or INTERPRETERS.get(target.suffix.lower(), "")
    payload = json.dumps(args, ensure_ascii=False)
    argv = ([interpreter] if interpreter else []) + [str(target), payload]
    timeout = float(meta.get("timeout_s") or 20)
    env = _scrubbed_env(meta.get("_env"))
    env["ATHENA_ARGS"] = payload
    argv = _contained(argv, Path(cwd), _athena_root_of(folder))
    try:
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                              shell=False, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {timeout:.0f}s"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    out = (done.stdout or "") + (("\n" + done.stderr) if done.stderr else "")
    return {"ok": done.returncode == 0, "exit": done.returncode, "output": out[:MAX_OUTPUT]}


def _run_command(meta: dict, args: dict, cwd: Path) -> dict:
    argv = []
    for part in meta.get("command") or []:
        text = str(part)
        for key, value in args.items():
            text = text.replace("{" + str(key) + "}", str(value))
        argv.append(text)
    if not argv:
        raise PackError("command is empty")
    timeout = float(meta.get("timeout_s") or 20)
    argv = _contained(argv, Path(cwd), _athena_root_of(folder))
    try:
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, shell=False,
                              env=_scrubbed_env(meta.get("_env")))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {timeout:.0f}s"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    out = (done.stdout or "") + (("\n" + done.stderr) if done.stderr else "")
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


def _tool_from(meta: dict, folder: Path, cwd: Path, env: dict) -> Tool:
    name = str(meta.get("name") or "").strip()
    if not name:
        raise PackError("name is required in TOOL.md")
    kind = str(meta.get("kind") or "script").lower()
    if kind not in KINDS:
        raise PackError(f"unknown kind {kind!r}; expected one of {', '.join(KINDS)}")

    params = meta.get("parameters") or {}
    if not isinstance(params, dict):
        raise PackError("parameters: must be a JSON object")
    properties, required = {}, []
    for key, info in params.items():
        info = info if isinstance(info, dict) else {}
        prop = {k: v for k, v in info.items() if k in ("type", "description", "enum")}
        properties[key] = prop or {"type": "string"}
        if info.get("required"):
            required.append(key)

    meta = dict(meta, _env=env)

    def run(**kwargs):
        if kind == "script":
            return _run_script(meta, kwargs, folder, cwd)
        if kind == "command":
            return _run_command(meta, kwargs, cwd)
        return _run_http(meta, kwargs)

    return Tool(name=name, description=str(meta.get("description") or f"tool {name}"),
                parameters={"type": "object", "properties": properties, "required": required}, fn=run)


def load_all(cfg: dict, registry, profile_root: Path | None = None, athena_root: Path | None = None,
             ctx=None) -> dict:
    """Register the enabled tools and the required system category. A broken TOOL.md is reported, never fatal."""
    want = enabled_names(cfg)
    required = system_names(athena_root)
    report = {"enabled": want, "required": required, "loaded": [], "skipped": [], "missing": []}
    if not want and not required:
        return report
    found = {p["name"]: p for p in discover(profile_root, athena_root)}
    cwd = (Path(profile_root) / "sandbox") if profile_root else Path(".")
    env = {}
    if profile_root is not None:
        home = Path(profile_root)
        athena = Path(athena_root) if athena_root else home.parents[1]
        env = {"ATHENA_ROOT": str(athena), "ATHENA_HOME": str(home),
               "ATHENA_SANDBOX": str(home / "sandbox"), "ATHENA_WORKSPACE": str(home / "workspace"),
               "ATHENA_SYSTEM": str(athena / "athena-agent")}
    for name in [*required, *[n for n in want if n not in required]]:
        row = found.get(name)
        if row is None or not row["has_descriptor"]:
            report["missing"].append(name)
            continue
        try:
            meta, _body = _frontmatter(Path(row["descriptor"]))
            kind = str(meta.get("kind") or "script").lower()
            if kind == "builtin":
                from tools import system as system_impls

                tools = system_impls.build_impl(str(meta.get("impl") or name), ctx, registry)
                for tool in tools:
                    registry.register(tool)
                report["loaded"].append({"name": name, "tool": name, "kind": "builtin",
                                         "tools": [t.name for t in tools],
                                         "origin": row["origin"],
                                         "required": name in required})
                continue
            registry.register(_tool_from(meta, Path(row["dir"]), cwd, env))
            report["loaded"].append({"name": name, "tool": str(meta.get("name")), "kind": kind,
                                     "origin": row["origin"], "scripts": row["scripts"],
                                     "required": name in required})
        except Exception as exc:                      # noqa: BLE001
            report["skipped"].append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
    return report

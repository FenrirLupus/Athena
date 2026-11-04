"""Installing a staged plugin — the step between "inspected" and "enabled".

The agent can inspect a repository and prepare a plugin inside its own sandbox. It cannot write `.athena/plugins/`,
because `ATHENA_ROOT` is read-only to agents, and that is deliberate: an agent that can install code has a way around
every rule this architecture has. So installation is a **user command** over something the agent staged:

    agent   repo_inspect → assess → stage into sandbox/staging/<name>/
    user    ./athena.sh plugins install <name>
    this    validate → copy → hash → enable → smoke → re-baseline

Nothing from the repository is executed at any point. The only code that runs is the plugin's own `register()` when
the smoke test asks the registry for its tools — in a child process, under bubblewrap, with credentials masked and a
read-only root, because that is how plugins run.

A manifest is mandatory (`PLUGIN.md` with `name` and `entry`). A plugin without one cannot be uninstalled cleanly
later, so it cannot be installed now.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

MANIFEST = "PLUGIN.md"
INSTALL_LOG = "installs.json"
FORBIDDEN_IN_STAGE = ("node_modules", ".git", "__pycache__", ".venv", "venv")


class InstallError(RuntimeError):
    pass


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip()).strip("-")
    if not slug:
        raise InstallError("a plugin name is required")
    return slug


def _read_manifest(folder: Path) -> dict:
    path = folder / MANIFEST
    if not path.is_file():
        raise InstallError(f"{folder.name} has no {MANIFEST} — a plugin without a manifest cannot be installed "
                           f"(or uninstalled cleanly later). Add one with `name` and `entry`.")
    text = path.read_text(encoding="utf-8", errors="replace")
    meta: dict = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    if not meta.get("name"):
        raise InstallError(f"{MANIFEST} has no name in its header")
    provides = str(meta.get("provides") or "tools").lower()
    meta["provides"] = provides
    # A declaration the build ignores is worse than no declaration: the author is told the plugin "provides agents"
    # and then nothing hosts one. Known, hosted kinds only.
    unknown = [part.strip() for part in provides.replace(",", " ").split()
               if part.strip() and part.strip() not in HOSTED_PROVIDES]
    if unknown:
        raise InstallError(
            f"{MANIFEST} declares provides: {', '.join(unknown)}, which this build does not host (it hosts: "
            f"{', '.join(sorted(HOSTED_PROVIDES))}). Agents are created with `./athena.sh agents` and are not "
            f"something a plugin can ship yet — drop it from `provides` so the manifest says what actually happens")
    # A plugin that ships only skills or data has no entry script; requiring one would make a repository of markdown
    # impossible to install, which is exactly what a "skills bundle" is.
    needs_entry = "tools" in provides or not (meta.get("entry") == "" and "skills" in provides)
    entry = str(meta.get("entry") or "").strip()
    if "tools" in provides:
        entry = entry or "scripts/plugin.py"
        if not (folder / entry).is_file():
            raise InstallError(f"{MANIFEST} provides tools but entry {entry!r} does not exist in the staged folder")
    elif entry and not (folder / entry).is_file():
        raise InstallError(f"{MANIFEST} points at entry {entry!r}, which does not exist in the staged folder")
    meta["entry"] = entry
    # `requires: ["six>=1.16", ...]` — declared, not executed. The installer builds a venv for them on approval.
    raw = meta.get("requires")
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.strip("[]").split(",") if part.strip()]
    meta["requires"] = [str(item).strip().strip('"').strip("'") for item in (raw or []) if str(item).strip()]
    return meta


def _hash_tree(folder: Path) -> dict:
    files = {}
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        # the plugin's own venv is not part of what was installed: hashing it recorded 900 files instead of 2, and a
        # pip upgrade inside it would read as drift in the plugin's source
        if VENV in path.relative_to(folder).parts:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[str(path.relative_to(folder))] = {"sha256": digest, "bytes": path.stat().st_size}
    return files


def _copy_clean(source: Path, dest: Path) -> None:
    """Copy the staged folder, minus build noise and anything that could carry a checkout inside a checkout."""
    dest.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        if any(part in FORBIDDEN_IN_STAGE for part in path.parts):
            continue
        target = dest / path.relative_to(source)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _enable_in_config(config_path: Path, name: str) -> str:
    """Add the name to `plugins.enabled`, keeping the file readable — and verify it still parses.

    A folder on disk is not permission; this line is. The edit is re-read through the loader and rolled back if the
    YAML stops parsing, because a broken config stops the server.
    """
    original = config_path.read_text(encoding="utf-8")
    text = original

    if re.search(r"^\s*enabled:\s*\[.*\b" + re.escape(name) + r"\b.*\]\s*$", text, re.M):
        return "already enabled"

    inline = re.search(r"^(\s*)enabled:\s*\[(.*?)\]\s*$", text, re.M)
    block = re.search(r"^(\s*)enabled:\s*$\n((?:\1\s*-\s*.+\n)+)", text, re.M)

    if inline:
        indent, existing = inline.group(1), inline.group(2).strip()
        items = [item.strip() for item in existing.split(",") if item.strip()]
        items.append(name)
        replacement = f"{indent}enabled: [{', '.join(items)}]"
        text = text[:inline.start()] + replacement + text[inline.end():]
    elif block:
        indent = block.group(1)
        addition = f"{indent}  - {name}\n"
        text = text[:block.end()] + addition + text[block.end():]
    else:
        raise InstallError("could not find a `plugins: enabled:` list in config.yaml — add the plugin by hand")

    config_path.write_text(text, encoding="utf-8")
    # Verify against the file we just wrote. `config.loader.load()` reads the *live* installation, which is not the
    # tree under test, and would report "it did not take effect" for a perfectly good edit.
    if name not in _enabled_names(config_path.read_text(encoding="utf-8")):
        config_path.write_text(original, encoding="utf-8")
        raise InstallError("config edit could not be verified and was rolled back")
    return "enabled"


def _enabled_names(text: str) -> list[str]:
    """The `plugins.enabled` names, read out of the YAML text without a YAML dependency."""
    section = re.search(r"^plugins:\s*$", text, re.M)
    body = text[section.end():] if section else text
    inline = re.search(r"^\s*enabled:\s*\[(.*?)\]\s*$", body, re.M)
    if inline:
        return [item.strip().strip('"').strip("'") for item in inline.group(1).split(",") if item.strip()]
    block = re.search(r"^\s*enabled:\s*$\n((?:\s*-\s*.+\n)+)", body, re.M)
    if block:
        return [line.split("-", 1)[1].strip().strip('"').strip("'")
                for line in block.group(1).splitlines() if "-" in line]
    return []


def _disable_in_config(config_path: Path, name: str) -> str:
    original = config_path.read_text(encoding="utf-8")
    text = original
    inline = re.search(r"^(\s*)enabled:\s*\[(.*?)\]\s*$", text, re.M)
    if inline:
        indent, existing = inline.group(1), inline.group(2).strip()
        items = [item.strip() for item in existing.split(",") if item.strip() and item.strip() != name]
        replacement = f"{indent}enabled: [{', '.join(items)}]"
        text = text[:inline.start()] + replacement + text[inline.end():]
    else:
        text = re.sub(r"^\s*-\s*" + re.escape(name) + r"\s*$\n", "", text, flags=re.M)
    config_path.write_text(text, encoding="utf-8")
    return "disabled" if text != original else "was not enabled"


VENV = ".venv"
LOCK = "requirements.lock"
SEED = "athena-server.pth"


def venv_python(dest: Path) -> Path | None:
    """The interpreter for a plugin that declares dependencies, or None when it needs none."""
    candidate = Path(dest) / VENV / "bin" / "python3"
    return candidate if candidate.exists() else None


def _seed_shared_stack(target: Path) -> str:
    """Point the plugin's venv at the server's site-packages, read-only.

    Sharing without sharing the risk: the plugin sees the stack the server already has (httpx, yaml, whatever), and
    installs only its own deltas — so a dozen plugins do not each carry a copy of the same libraries. The cost is
    honest and it is recorded: the plugin now depends on the server's *versions* of those libraries.
    """
    import sysconfig

    try:
        shared = sysconfig.get_paths()["purelib"]
    except Exception:                                 # noqa: BLE001
        return ""
    sites = list((target / "lib").glob("python*/site-packages"))
    if not shared or not sites:
        return ""
    (sites[0] / SEED).write_text(shared + "\n", encoding="utf-8")
    return shared


def _lock_path(dest: Path) -> Path:
    return Path(dest) / LOCK


def _lock_info(dest: Path) -> dict:
    """What a staged `requirements.lock` says, without trusting it further than its lines."""
    path = _lock_path(dest)
    if not path.is_file():
        return {"present": False, "packages": 0, "sha256": "", "path": ""}
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    return {"present": True, "packages": len(lines), "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
            "path": str(path)}


def prepare_environment(dest: Path, requires: list[str], timeout: float = 300.0) -> dict:
    """Create the plugin's own venv and install what its manifest declares.

    This is the one place a package install happens, and it happens **at install time, on the user's click** — never
    from agent code. The venv lives inside the plugin folder, so uninstalling archives it with the plugin, and each
    plugin's dependencies are its own: one repo cannot upgrade a library another plugin depends on.
    """
    import subprocess
    import sys

    if not requires:
        return {"created": False, "packages": [], "reason": "none declared"}

    target = Path(dest) / VENV
    log: list[str] = []
    try:
        subprocess.run([sys.executable, "-m", "venv", str(target)], check=True,
                       capture_output=True, text=True, timeout=120)
    except Exception as exc:                          # noqa: BLE001
        return {"created": False, "packages": [], "error": f"venv creation failed: {type(exc).__name__}: {exc}"}

    # Seed the shared stack *after* installing, not before: with the `.pth` in place, `pip freeze` also lists the
    # server's packages, so the plugin's lock recorded 19 packages it never asked for. Order matters for accounting.
    shared = ""
    python = target / "bin" / "python3"

    # A lock file, when the plugin ships one, is the *only* thing installed: that is what "reproducible" means. When
    # it does not, the declared specifiers are installed and the resolved freeze is written back as the lock — so the
    # first install records what happened and every later repair reproduces it exactly.
    lock = _lock_path(dest)
    from_lock = lock.is_file()
    if from_lock:
        cmd = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-r", str(lock)]
    else:
        cmd = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", *requires]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"created": True, "packages": [], "error": f"pip timed out after {timeout:.0f}s"}
    log = (done.stdout or "").splitlines()[-8:]

    installed = []
    try:
        freeze = subprocess.run([str(python), "-m", "pip", "freeze"], capture_output=True, text=True, timeout=60)
        installed = [line.strip() for line in (freeze.stdout or "").splitlines() if line.strip()]
    except Exception:                                 # noqa: BLE001
        pass

    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or ["unknown error"]
        return {"created": True, "packages": installed, "shared_stack": shared, "from_lock": from_lock,
                "error": f"pip install failed: {detail[0][:200]}", "log": log}

    lock_written = False
    if not from_lock and installed:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("\n".join(installed) + "\n", encoding="utf-8")
        lock_written = True

    shared = _seed_shared_stack(target)

    return {"created": True, "packages": installed, "python": str(python), "log": log,
            "shared_stack": shared, "from_lock": from_lock, "lock_written": lock_written,
            "lock": _lock_info(dest)}


def smoke_test(plugins_root: Path, name: str, athena_root: Path) -> dict:
    """Load the plugin the way the server does — a child process, sandboxed — and report what it declares."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core import plugins as plugins_mod
    from tools.registry import Registry

    registry = Registry()
    report = plugins_mod.load_all({"plugins": {"enabled": [name]}}, registry, None, plugins_root.parent)
    loaded = [row for row in report["loaded"] if row["name"] == name]
    skipped = [row for row in report["skipped"] if row["name"] == name]
    return {"loaded": bool(loaded), "tools": loaded[0]["tools"] if loaded else [],
            "isolation": loaded[0].get("isolation") if loaded else "",
            "error": skipped[0]["error"] if skipped else ""}


def _restore(dest: Path, backup: Path | None) -> None:
    """Undo a half-applied install: the previous version comes back, or nothing is left behind.

    `force`/`repair` moved the live plugin to `backups/retired/…` and wrote the staged copy into `plugins/<name>`
    BEFORE `_enable_in_config` ran — and that can raise. There was no path that put the backup back, so a failed
    install left a half-copied plugin live, the previous version parked, and `plugins.enabled` still naming it.
    """
    try:
        if dest.exists():
            shutil.rmtree(dest)
    except OSError:
        pass
    if backup and Path(backup).exists():
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup), str(dest))
        except OSError:
            pass



def install(staged: Path, plugins_root: Path, athena_root: Path, *, force: bool = False,
            smoke: bool = True) -> dict:
    """Validate a staged plugin, copy it in, hash it, enable it, smoke-test it."""
    staged = Path(staged)
    plugins_root = Path(plugins_root)
    if not staged.is_dir():
        raise InstallError(f"no staged plugin at {staged}")
    manifest = _read_manifest(staged)

    name = _slug(manifest["name"])
    if name != staged.name:
        raise InstallError(f"manifest name {name!r} does not match the staged folder {staged.name!r} — "
                           f"one of them is wrong, and guessing which would be worse than stopping")

    dest = plugins_root / name
    if dest.exists() and not force:
        raise InstallError(f"{dest} already exists — remove it first, or pass --force to replace it")

    backup = None
    if dest.exists():
        backup = plugins_root.parent / "backups" / "retired" / f"{name}-replaced-{time.strftime('%Y%m%d-%H%M%S')}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(backup))

    _copy_clean(staged, dest)
    try:
        environment = prepare_environment(dest, manifest.get("requires") or [])
        files = _hash_tree(dest)
    except Exception as exc:
        _restore(dest, backup)
        raise InstallError(f"{name} could not be prepared ({exc}); the previous version is back in place") from exc

    record = {"name": name, "installed_at": int(time.time()), "source": str(staged),
              "declared": {k: manifest.get(k) for k in ("name", "description", "entry", "provides", "requires",
                                                        "source_url")},
              "environment": environment, "files": files}
    log_path = athena_root / "security" / INSTALL_LOG
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}
    existing[name] = record
    log_path.write_text(json.dumps(existing, indent=1), encoding="utf-8")

    config_path = athena_root / "config.yaml"
    try:
        state = _enable_in_config(config_path, name)
    except Exception as exc:      # a failed config edit must not leave the swap half-applied
        _restore(dest, backup)
        try:
            existing.pop(name, None)
            log_path.write_text(json.dumps(existing, indent=1), encoding="utf-8")
        except OSError:
            pass
        raise InstallError(f"could not enable {name} ({exc}); the install was rolled back") from exc

    result = {"name": name, "path": str(dest), "files": len(files), "enabled": state,
              "manifest": str(dest / MANIFEST), "install_record": str(log_path),
              "environment": {"packages": environment.get("packages") or [],
                              "error": environment.get("error", ""),
                              "venv": bool(environment.get("created")),
                              "shared_stack": environment.get("shared_stack", ""),
                              "from_lock": environment.get("from_lock", False),
                              "lock_written": environment.get("lock_written", False)}}
    if environment.get("error"):
        # the copy is live but unusable: put the previous version back (or leave nothing behind), and say so
        _restore(dest, backup)
        if backup is None:
            # nothing was replaced, so nothing should still name it: an entry for a directory that does not
            # exist would report a plugin as enabled when it is not installed
            try:
                _disable_in_config(config_path, name)
            except Exception:                     # noqa: BLE001 — the rollback already happened
                pass
        try:
            existing.pop(name, None)
            log_path.write_text(json.dumps(existing, indent=1), encoding="utf-8")
        except OSError:
            pass
        result["enabled"] = "rolled back — the declared dependencies could not be installed"
        result["rolled_back"] = True
        return result
    if backup:
        result["replaced_backup"] = str(backup)

    if smoke:
        result["smoke"] = smoke_test(plugins_root, name, athena_root)
        if not result["smoke"]["loaded"]:
            # a plugin that cannot load leaves nothing to keep: disable it and say why, rather than leaving the
            # config pointing at something broken
            _disable_in_config(config_path, name)
            result["enabled"] = "rolled back — the plugin did not load"
    return result


def uninstall(name: str, plugins_root: Path, athena_root: Path) -> dict:
    """Disable, verify, then archive with a manifest. Never delete first."""
    name = _slug(name)
    dest = Path(plugins_root) / name
    config_path = Path(athena_root) / "config.yaml"
    state = _disable_in_config(config_path, name)

    if not dest.is_dir():
        return {"name": name, "removed": False, "enabled": state,
                "note": "no such plugin folder — the enable line is the only thing that existed"}

    # A second removal on the same day must not collide with the first archive: two installs, two removals, two
    # copies — the manifest is what makes the second one useful, and nesting it inside the first loses that.
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    retired = Path(athena_root) / "backups" / "retired" / f"{name}-{stamp}"
    suffix = 2
    while retired.exists():
        retired = Path(athena_root) / "backups" / "retired" / f"{name}-{stamp}-{suffix}"
        suffix += 1
    retired.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dest), str(retired / "plugin"))
    manifest_lines = [f"{record['sha256']}  {path}" for path, record in sorted(_hash_tree(retired / "plugin").items())]
    (retired / "MANIFEST.sha256").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

    log_path = Path(athena_root) / "security" / INSTALL_LOG
    try:
        records = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        records = {}
    record = records.pop(name, None)
    if record is not None:
        log_path.write_text(json.dumps(records, indent=1), encoding="utf-8")

    return {"name": name, "removed": True, "enabled": state, "archived_at": str(retired),
            "install_record_removed": record is not None,
            "note": "re-run `./athena.sh security baseline` so the removal is recorded as authorised"}


def installed(plugins_root: Path, athena_root: Path) -> list[dict]:
    """What is installed, according to the install log rather than the directory listing."""
    log_path = Path(athena_root) / "security" / INSTALL_LOG
    try:
        records = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = []
    for name, record in sorted(records.items()):
        folder = Path(plugins_root) / name
        rows.append({"name": name, "present": folder.is_dir(), "files": len(record.get("files") or {}),
                     "installed_at": record.get("installed_at"),
                     "source": (record.get("declared") or {}).get("source_url") or record.get("source") or ""})
    return rows


# -- install requests --------------------------------------------------------
# The agent stages into its sandbox and *asks*. The request is what the interface shows the user; the server performs
# the install when the user answers, because that click is the authorisation. The agent never writes ROOT.
PENDING = "pending.json"
DENIED = "answered.json"


def _pending_path(athena_root: Path) -> Path:
    folder = Path(athena_root) / "security" / "installs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / PENDING


def _load_pending(athena_root: Path) -> dict:
    path = _pending_path(athena_root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_pending(athena_root: Path, rows: dict) -> None:
    _pending_path(athena_root).write_text(json.dumps(rows, indent=1), encoding="utf-8")


def _fingerprint(folder: Path) -> str:
    """A stage's identity: its path plus when its manifest last changed. Re-staging changes it, denying does not."""
    manifest = Path(folder) / MANIFEST
    try:
        stamp = int(manifest.stat().st_mtime)
    except OSError:
        stamp = 0
    return f"{folder}:{stamp}"


def _answers(athena_root: Path) -> dict:
    path = _pending_path(athena_root).parent / DENIED
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _remember_answer(athena_root: Path, folder: Path, decision: str) -> None:
    """Record that this stage has been answered, so a poll does not re-ask the same question.

    Without this, denying a request simply re-created it on the next poll: a prompt that cannot be dismissed. A
    re-stage (new manifest mtime) is a new question, and it will be asked.
    """
    rows = _answers(athena_root)
    rows[_fingerprint(folder)] = {"decision": decision, "at": int(time.time())}
    (_pending_path(athena_root).parent / DENIED).write_text(json.dumps(rows, indent=1), encoding="utf-8")


def request_tool_install(staged: Path, athena_root: Path, *, reason: str = "", agent: str = "") -> dict:
    """Queue a staged tool pack for the user, the same way a plugin is queued."""
    staged = Path(staged)
    meta = _read_tool(staged)                      # refuses an unusable stage before anyone is asked
    import secrets

    rid = secrets.token_hex(6)
    rows = _load_pending(athena_root)
    rows[rid] = {"id": rid, "kind": "tool", "name": _slug(meta["name"]), "staged": str(staged),
                 "agent": str(agent or ""), "source_url": "", "reason": reason or "a tool pack staged in the sandbox",
                 "declared": meta, "created_at": int(time.time()),
                 "files": sum(1 for p in staged.rglob("*") if p.is_file())}
    _save_pending(athena_root, rows)
    return {"request": rid, "kind": "tool", "name": rows[rid]["name"],
            "awaiting": "the user is prompted; nothing is installed until they answer"}


def request_install(staged: Path, athena_root: Path, *, source_url: str = "", reason: str = "",
                    agent: str = "") -> dict:
    """Record a staged plugin as *waiting for the user*. Validates the manifest now, so a bad stage is refused
    before a person is bothered with it."""
    staged = Path(staged)
    manifest = _read_manifest(staged)                 # raises if the stage is unusable
    import secrets

    rid = secrets.token_hex(6)
    rows = _load_pending(athena_root)
    rows[rid] = {"id": rid, "kind": "install", "name": _slug(manifest["name"]), "staged": str(staged),
                 "agent": str(agent or ""), "source_url": source_url or "", "reason": reason, "declared": manifest,
                 "created_at": int(time.time()),
                 "files": sum(1 for p in staged.rglob("*") if p.is_file())}
    _save_pending(athena_root, rows)
    return {"request": rid, "name": rows[rid]["name"], "files": rows[rid]["files"],
            "awaiting": "the user is prompted in the interface; nothing is installed until they answer"}


def _staging_folders(athena_root: Path) -> list[Path]:
    """Every `staging/<name>/` folder across profiles — the places an agent can legitimately stage a plugin."""
    found = []
    for profile in sorted((Path(athena_root) / "profiles").glob("*")):
        staging = profile / "sandbox" / "staging"
        if not staging.is_dir():
            continue
        for folder in sorted(staging.iterdir()):
            if folder.is_dir() and (folder / MANIFEST).is_file():
                found.append(folder)
    return found


def _install_state(name: str, staged: Path, athena_root: Path, plugins_root: Path,
                   kind: str = "plugin") -> dict:
    """What actions make sense for this artefact right now, and why."""
    root = Path(plugins_root).parent / "tools" if kind == "tool" else Path(plugins_root)
    installed = (root / name)
    if not installed.is_dir():
        return {"installed": False, "drifted": False, "actions": ["install", "cancel"],
                "detail": "not installed"}

    drifted, missing = [], []
    try:
        records = json.loads((Path(athena_root) / "security" / INSTALL_LOG).read_text(encoding="utf-8"))
        expected = (records.get(name) or {}).get("files") or {}
    except (OSError, json.JSONDecodeError):
        expected = {}
    for rel, record in expected.items():
        target = installed / rel
        if not target.is_file():
            missing.append(rel)
        elif hashlib.sha256(target.read_bytes()).hexdigest() != record.get("sha256"):
            drifted.append(rel)

    actions = (["install", "repair", "uninstall", "cancel"] if kind == "plugin"
               else ["install", "uninstall", "cancel"])
    if drifted or missing:
        detail = f"installed, but {len(drifted) + len(missing)} file(s) differ from what was recorded"
    else:
        detail = "installed and unchanged"
    return {"installed": True, "drifted": bool(drifted or missing), "drifted_files": (drifted + missing)[:6],
            "actions": actions, "detail": detail}


def reconcile(athena_root: Path, plugins_root: Path) -> int:
    """Turn *staged folders on disk* into install requests, whatever staged them.

    Requests are derived from the folders present, so every route that stages one — the staging tool or a copy made
    with the filesystem tools — produces the same pending request for the user to act on.
    """
    rows = _load_pending(athena_root)
    known = {row["staged"] for row in rows.values()}
    answered = _answers(athena_root)
    created = 0
    for folder in _staging_folders(athena_root):
        if str(folder) in known:
            continue
        try:
            manifest = _read_manifest(folder)
        except InstallError:
            continue                                   # not a usable stage: no prompt, no noise
        state = _install_state(_slug(manifest["name"]), folder, athena_root, plugins_root)
        # An answered stage is not asked again — unless the installed copy has drifted, which is a *new* question
        # and the one case where the user must be told rather than reminded.
        if _fingerprint(folder) in answered and not state["drifted"]:
            continue
        import secrets

        rid = secrets.token_hex(6)
        name = _slug(manifest["name"])
        rows[rid] = {"id": rid, "kind": "install", "name": name, "staged": str(folder),
                     "source_url": "", "reason": "staged in the sandbox and waiting for a decision",
                     "declared": manifest, "created_at": int(time.time()),
                     "files": sum(1 for p in folder.rglob("*") if p.is_file()),
                     "detected": True}
        created += 1
    if created:
        _save_pending(athena_root, rows)
    return created


def pending_installs(athena_root: Path, plugins_root: Path | None = None) -> list[dict]:
    athena_root = Path(athena_root)
    plugins_root = Path(plugins_root) if plugins_root else athena_root / "plugins"
    reconcile(athena_root, plugins_root)
    rows = _load_pending(athena_root)
    # A request whose staged copy was cleaned up can only ever offer Cancel, and it keeps popping a dialog. Box
    # requests have no staged path, so they are kept until answered.
    stale = [rid for rid, row in rows.items()
             if (row.get("staged") or "") and not Path(row["staged"]).is_dir()]
    if stale:
        for rid in stale:
            rows.pop(rid, None)
        _save_pending(athena_root, rows)

    out = []
    for row in sorted(rows.values(), key=lambda r: r.get("created_at") or 0):
        folder = Path(row["staged"])
        # plugin requests carry kind "install" (historical); tools carry "tool"; box rows have no folder
        artefact = "tool" if (row.get("kind") == "tool") else "plugin"
        state = (_install_state(row["name"], folder, athena_root, plugins_root, artefact)
                 if folder.is_dir()
                 else {"installed": False, "drifted": False, "actions": ["cancel"],
                       "detail": "the staged copy is gone"})
        out.append({**row, "present": folder.is_dir(),
                    "files": sum(1 for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else 0,
                    "lock": _lock_info(folder) if folder.is_dir() else {"present": False},
                    **state})
    return out


def resolve_install(rid: str, decision: str, athena_root: Path, plugins_root: Path,
                    force: bool = False) -> dict:
    """`install` performs it (server-side, on the user's click); `deny` forgets it and says so."""
    rows = _load_pending(athena_root)
    row = rows.get(rid)
    if row is None:
        raise KeyError(rid)
    staged_for_answer = Path(row["staged"])
    verdict = (decision or "").strip().lower()
    if row.get("kind") == "tool" and verdict in ("uninstall", "remove"):
        rows.pop(rid, None)
        _save_pending(athena_root, rows)
        return {"id": rid, "decision": "uninstalled",
                **uninstall_tool(row["name"], Path(plugins_root).parent / "tools", athena_root)}

    if row.get("kind") == "box" and verdict in ("install", "allow", "yes"):
        rows.pop(rid, None)
        _save_pending(athena_root, rows)
        result = install_box(row.get("packages") or [], athena_root)
        return {"id": rid, "decision": "installed" if result.get("ok") else "failed",
                "name": row["name"], "kind": "box", **result,
                "note": "a box change is not undone by uninstalling a plugin — remove it with apt if you want it gone"}

    if row.get("kind") == "tool" and verdict in ("install", "allow", "yes"):
        rows.pop(rid, None)
        _save_pending(athena_root, rows)
        try:
            result = install_tool(Path(row["staged"]), Path(plugins_root).parent / "tools", athena_root)
        except InstallError as exc:
            return {"id": rid, "decision": "failed", "name": row["name"], "error": str(exc)}
        return {"id": rid, "decision": "installed", **result}
    if verdict in ("cancel", "deny", "denied", "no"):
        rows.pop(rid, None)
        _save_pending(athena_root, rows)
        if staged_for_answer.is_dir():
            _remember_answer(athena_root, staged_for_answer, "cancelled")
        return {"id": rid, "decision": "denied", "name": row["name"],
                "note": "nothing was installed; the staged copy stays in the sandbox until it is cleaned up"}
    if verdict in ("uninstall", "remove"):
        rows.pop(rid, None)
        _save_pending(athena_root, rows)
        if staged_for_answer.is_dir():
            _remember_answer(athena_root, staged_for_answer, "uninstalled")
        return {"id": rid, "decision": "uninstalled", **uninstall(row["name"], plugins_root, athena_root)}
    if verdict == "repair":
        # repair is an install from the staged copy over what is there — same hashes, same smoke test, forced
        force = True
    elif verdict not in ("install", "allow", "yes"):
        raise InstallError("decision must be install, repair, uninstall or cancel")

    staged = Path(row["staged"])
    # Claim the request *before* doing the work. A request that stays pending after a failed or half-done install
    # invites a retry loop: click, install fails because the folder now exists, click again, fail again.
    rows.pop(rid, None)
    _save_pending(athena_root, rows)

    if not staged.is_dir():
        raise InstallError(f"the staged copy at {staged} is gone — ask the agent to stage it again")

    if staged.is_dir():
        _remember_answer(athena_root, staged, verdict)
    try:
        result = install(staged, plugins_root, athena_root, force=force)
    except InstallError as exc:
        return {"id": rid, "decision": "failed", "name": row["name"], "error": str(exc),
                "note": "the request is cleared; fix the problem and ask the agent to stage it again"}
    return {"id": rid, "decision": "installed", **result}


# -- tool packs ---------------------------------------------------------------
# Same lifecycle as a plugin, different artefact: a TOOL.md plus scripts, enabled by a `tools.packs` line instead of
# `plugins.enabled`. Plugins had a card; tool packs did not, which is why an agent could prepare one and still have to
# tell the user "flip a config line". This makes the two shapes equal.

def _read_tool(folder: Path) -> dict:
    path = Path(folder) / "TOOL.md"
    if not path.is_file():
        raise InstallError(f"{Path(folder).name} has no TOOL.md — a tool pack needs one to be installable")
    meta: dict = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if ":" not in line or line.strip().startswith("#"):
                    continue
                key, _, value = line.partition(":")
                meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    name = str(meta.get("name") or "").strip()
    if not name:
        raise InstallError("TOOL.md has no name in its header")
    kind = str(meta.get("kind") or "script").lower()
    if kind == "script":
        script = str(meta.get("script") or "").strip()
        if script:
            if not (Path(folder) / script).is_file():
                raise InstallError(f"TOOL.md points at script {script!r}, which does not exist")
        else:
            candidates = [p for p in (Path(folder) / "scripts").glob("*") if p.is_file()] \
                if (Path(folder) / "scripts").is_dir() else []
            if len(candidates) != 1:
                raise InstallError("TOOL.md needs `script:` (or exactly one file under scripts/)")
    meta["kind"] = kind
    return meta


def _enable_pack(config_path: Path, name: str) -> str:
    """Add the name to `tools.packs`, then verify by reading the file back."""
    original = config_path.read_text(encoding="utf-8")
    if name in _packs_names(original):
        return "already enabled"
    inline = re.search(r"^(\s*)packs:\s*\[(.*?)\]\s*$", original, re.M)
    if inline:
        indent, existing = inline.group(1), inline.group(2).strip()
        items = [item.strip() for item in existing.split(",") if item.strip()] + [name]
        text = original[:inline.start()] + f"{indent}packs: [{', '.join(items)}]" + original[inline.end():]
    else:
        block = re.search(r"^(\s*)packs:\s*$\n((?:\1\s*-\s*.+\n)+)", original, re.M)
        if not block:
            raise InstallError("could not find `tools: packs:` in config.yaml — enable it by hand")
        text = original[:block.end()] + f"{block.group(1)}  - {name}\n" + original[block.end():]
    config_path.write_text(text, encoding="utf-8")
    if name not in _packs_names(config_path.read_text(encoding="utf-8")):
        config_path.write_text(original, encoding="utf-8")
        raise InstallError("the tools.packs edit could not be verified and was rolled back")
    return "enabled"


def _packs_names(text: str) -> list[str]:
    section = re.search(r"^tools:\s*$", text, re.M)
    body = text[section.end():] if section else text
    inline = re.search(r"^\s*packs:\s*\[(.*?)\]\s*$", body, re.M)
    if inline:
        return [item.strip().strip('"').strip("'") for item in inline.group(1).split(",") if item.strip()]
    block = re.search(r"^\s*packs:\s*$\n((?:\s*-\s*.+\n)+)", body, re.M)
    if block:
        return [line.split("-", 1)[1].strip().strip('"').strip("'")
                for line in block.group(1).splitlines() if "-" in line]
    return []


def install_tool(staged: Path, tools_root: Path, athena_root: Path, *, force: bool = False) -> dict:
    """Validate a staged tool pack, copy it into `.athena/tools/`, enable it, and load it once."""
    staged, tools_root = Path(staged), Path(tools_root)
    if not staged.is_dir():
        raise InstallError(f"no staged tool pack at {staged}")
    meta = _read_tool(staged)
    name = _slug(meta["name"])
    if name != staged.name:
        raise InstallError(f"TOOL.md name {name!r} does not match the staged folder {staged.name!r}")

    dest = tools_root / name
    if dest.exists() and not force:
        raise InstallError(f"{dest} already exists — remove it first, or pass --force")
    _copy_clean(staged, dest)
    files = _hash_tree(dest)

    record = {"name": name, "kind": "tool", "installed_at": int(time.time()), "source": str(staged),
              "declared": {k: meta.get(k) for k in ("name", "description", "kind", "script", "command")},
              "files": files}
    log_path = Path(athena_root) / "security" / INSTALL_LOG
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}
    existing[f"tool:{name}"] = record
    log_path.write_text(json.dumps(existing, indent=1), encoding="utf-8")

    state = _enable_pack(Path(athena_root) / "config.yaml", name)

    # load it the way a turn would
    from tools import registry as registry_mod

    cfg = {"tools": {"packs": [name]}}
    registry = registry_mod.Registry()
    from core import toolpacks

    report = toolpacks.load_all(cfg, registry, None, athena_root)
    return {"name": name, "kind": "tool", "path": str(dest), "files": len(files), "enabled": state,
            "tools": [row.get("tool") for row in report["loaded"]],
            "skipped": report["skipped"], "manifest": str(dest / "TOOL.md"),
            "install_record": str(log_path)}


# -- box (system) packages ----------------------------------------------------
# The container's OS layer. This is the one thing here that is not scoped to an artefact: a package installed for one
# plugin is installed for every plugin and for the server. So it is *off by default*, must be asked for by name, and
# the card says plainly that it changes the box rather than a plugin.
BOX_LOG = "box_installs.json"
# what a plugin can actually contribute today
HOSTED_PROVIDES = {"tools", "skills"}
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,60}$")


def _box_allowed(athena_root: Path) -> bool:
    try:
        text = (Path(athena_root) / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        return False
    match = re.search(r"^\s*allow_box_installs:\s*(true|false)\s*$", text, re.M | re.I)
    return bool(match and match.group(1).lower() == "true")


def _box_packages(raw) -> list[str]:
    items = raw if isinstance(raw, list) else str(raw or "").replace(",", " ").split()
    packages = [str(item).strip() for item in items if str(item).strip()]
    if not packages:
        raise InstallError("no packages named")
    for package in packages:
        if not PACKAGE_RE.match(package):
            raise InstallError(f"{package!r} is not a package name — refusing (names only, no options, no paths)")
    return packages


def request_box_install(packages, athena_root: Path, *, reason: str = "", agent: str = "") -> dict:
    """Queue a system-package install for the user. Refused outright when the config does not allow it."""
    athena_root = Path(athena_root)
    if not _box_allowed(athena_root):
        raise InstallError("system packages are disabled on this installation — set `security.allow_box_installs: "
                           "true` in config.yaml if you want the box to be changeable at all. A package installed here "
                           "belongs to every plugin and to the server, not to one of them")
    names = _box_packages(packages)
    import secrets

    rid = secrets.token_hex(6)
    rows = _load_pending(athena_root)
    rows[rid] = {"id": rid, "kind": "box", "name": " ".join(names), "packages": names,
                 "agent": str(agent or ""), "staged": "", "source_url": "", "created_at": int(time.time()),
                 "reason": reason or "system packages requested by an agent",
                 "warning": "installs into the container's OS layer — affects every agent, and is not undone by "
                            "removing a plugin"}
    _save_pending(athena_root, rows)
    return {"request": rid, "kind": "box", "packages": names,
            "awaiting": "the user is prompted; nothing is installed until they answer"}


def install_box(packages, athena_root: Path, *, dry_run: bool = False, timeout: float = 600.0) -> dict:
    """Install system packages inside the container. Server-side, on the user's click, never from agent code."""
    import subprocess

    athena_root = Path(athena_root)
    names = _box_packages(packages)
    argv = ["sudo", "-n", "apt-get", "install", "-y", "--no-install-recommends"]
    if dry_run:
        argv.append("--dry-run")
    argv += names
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "packages": names, "error": f"apt-get timed out after {timeout:.0f}s"}
    except OSError as exc:
        return {"ok": False, "packages": names, "error": f"{type(exc).__name__}: {exc}"}

    tail = ((done.stdout or "") + (done.stderr or "")).strip().splitlines()[-12:]
    record = {"at": int(time.time()), "packages": names, "ok": done.returncode == 0, "dry_run": dry_run,
              "exit": done.returncode, "output": "\n".join(tail)[:4000]}
    log = Path(athena_root) / "security" / BOX_LOG
    try:
        existing = json.loads(log.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = []
    existing.append(record)
    log.write_text(json.dumps(existing[-50:], indent=1), encoding="utf-8")
    return record


def uninstall_tool(name: str, tools_root: Path, athena_root: Path) -> dict:
    """Disable a tool pack, then archive it with a manifest — the same order as a plugin."""
    name = _slug(name)
    dest = Path(tools_root) / name
    config_path = Path(athena_root) / "config.yaml"

    original = config_path.read_text(encoding="utf-8")
    inline = re.search(r"^(\s*)packs:\s*\[(.*?)\]\s*$", original, re.M)
    state = "was not enabled"
    if inline and name in _packs_names(original):
        indent, existing = inline.group(1), inline.group(2).strip()
        items = [item.strip() for item in existing.split(",") if item.strip() and item.strip() != name]
        fixed = f"{indent}packs: [{', '.join(items)}]"
        config_path.write_text(original[:inline.start()] + fixed + original[inline.end():], encoding="utf-8")
        state = "disabled"

    if not dest.is_dir():
        return {"name": name, "kind": "tool", "removed": False, "enabled": state,
                "note": "no such tool pack folder"}

    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    retired = Path(athena_root) / "backups" / "retired" / f"tool-{name}-{stamp}"
    suffix = 2
    while retired.exists():
        retired = Path(athena_root) / "backups" / "retired" / f"tool-{name}-{stamp}-{suffix}"
        suffix += 1
    retired.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dest), str(retired / "tool"))
    lines = [f"{rec['sha256']}  {path}" for path, rec in sorted(_hash_tree(retired / "tool").items())]
    (retired / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")

    log_path = Path(athena_root) / "security" / INSTALL_LOG
    try:
        records = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        records = {}
    if records.pop(f"tool:{name}", None) is not None:
        log_path.write_text(json.dumps(records, indent=1), encoding="utf-8")

    return {"name": name, "kind": "tool", "removed": True, "enabled": state, "archived_at": str(retired)}

"""terminal — a shell, run inside bubblewrap so it cannot write outside the agent's own trees.

A shell is the tool that makes path-based permission meaningless: `python -c 'open("/x","w")'` never asks the guard
anything. So the containment has to be the kernel's job, not ours:

    bwrap --ro-bind / /            everything on the box is readable, nothing is writable
          --bind <sandbox> <sandbox>
          --bind <workspace> <workspace>
          --tmpfs /tmp
          --die-with-parent --new-session --unshare-pid
          /bin/bash -lc "<command>"

Inside that, `rm -rf ~/.athena/athena-agent` fails with EROFS no matter what the command says.

**Reads are masked, not just writes.** `--ro-bind / /` makes the whole box readable, and the shell runs as the user,
so `cat .athena/.secret` would work: 0600 protects a file from *other* users, not from a shell running as you. Every
credential store is therefore over-mounted with an empty file (files) or an empty `--tmpfs` (directories), so the path
exists and reads as nothing rather than being a hole.

**If bubblewrap is unavailable, the tool refuses rather than running uncontained** (`tools.terminal.require_containment`,
default `true`). A shell that quietly degrades from "cannot touch anything" to "can touch everything" is worse than no
shell, because the agent has no way to tell which one it got.

`cwd` must be somewhere the agent may write — normally `sandbox/`. `network` is on by default: research needs it,
and the process was already able to reach the network through the provider.
"""
from __future__ import annotations

import os
import tempfile
import shutil
import subprocess
from pathlib import Path

from core import paths as paths_mod
from core import trace
from tools.registry import Tool, ToolError

MAX_OUTPUT = 20000
DEFAULT_TIMEOUT = 120


def _lane(ctx) -> str:
    """Which lane a trace line belongs to: the channel's name, else the profile folder the shell is bound to."""
    name = str(getattr(ctx, "channel", "") or "")
    root = getattr(ctx, "profile_root", None)
    return name or (Path(root).name if root else "-")


def _bwrap() -> str:
    found = shutil.which("bwrap")
    if not found:
        raise ToolError("bubblewrap (bwrap) is not installed — refusing to run a shell uncontained. "
                        "Install bubblewrap, or set tools.terminal.require_containment: false to accept the risk")
    return found


# Credential stores that must be empty inside the sandbox. Files are over-mounted with /dev/null; directories with
# an empty tmpfs. `~` is the real user home — the shell runs as that user, so anything readable is readable to it.
MASK_FILES = (
    ".athena/.secret",
    ".athena/security/api_token",   # controls every agent, approval and profile — masking .secret alone missed it
    ".hermes/.env",
    ".netrc",
    ".git-credentials",
    ".config/gh/hosts.yml",
    ".docker/config.json",
    ".npmrc",
    ".pypirc",
    ".cargo/credentials",
    ".kube/config",
    ".config/gcloud/application_default_credentials.json",
)
MASK_DIRS = (
    ".ssh",
    ".gnupg",
    ".aws",
    ".password-store",
    ".kde4/share/apps/kwallet",
    ".local/share/kwalletd",
    ".config/gh",
    ".config/gcloud",
    ".mozilla",
    ".config/google-chrome",
    ".config/chromium",
    ".config/BraveSoftware",
)


def _empty_file(profile_root) -> str:
    """A real, empty, world-readable file to bind over each credential.

    Binding `/dev/null` works but reads fail with `EACCES` inside the sandbox (a device node the sandbox may not
    open), which looks like a permissions bug rather than a mask. An empty regular file reads as zero bytes with
    exit 0 — unambiguous, and it cannot be confused with "the secret is gone".
    """
    # never $HOME: `_mask_args(root, None)` happens on every plugin load, and the fallback wrote a 0444 file into the
    # user's home on each run — outside every declared scope and unremovable without `rm -f`
    base = Path(profile_root) / "sandbox" if profile_root else Path(tempfile.gettempdir()) / "athena-masks"
    base.mkdir(parents=True, exist_ok=True)
    empty = base / ".mask-empty"
    if not empty.exists():
        empty.write_text("", encoding="utf-8")
    os.chmod(empty, 0o444)
    return str(empty)


def _mask_args(athena_root, profile_root) -> list[str]:
    """Arguments that make credentials unreadable inside the sandbox without removing the paths."""
    args: list[str] = []
    empty = _empty_file(profile_root)
    real_home = Path(os.path.expanduser("~"))
    candidates = [real_home / rel for rel in MASK_FILES]
    if athena_root:
        candidates.append(Path(athena_root) / ".secret")
        profiles = Path(athena_root) / "profiles"
        if profiles.is_dir():
            candidates.extend(sorted(profiles.glob("*/.secret")))
    if profile_root:
        candidates.append(Path(profile_root) / ".secret")

    for path in candidates:
        try:
            if path.is_file():
                args += ["--ro-bind", empty, str(path)]
            elif path.is_dir():
                args += ["--tmpfs", str(path)]
        except OSError:
            continue

    for rel in MASK_DIRS:
        folder = real_home / rel
        if folder.is_dir():
            args += ["--tmpfs", str(folder)]
    return args


def _mounts(ctx) -> list[str]:
    """The writable trees: the agent's sandbox, its workspace, plus every granted workspace."""
    writable: list[str] = []
    for place in [ctx.profile_root / "sandbox" if ctx.profile_root else None,
                  ctx.profile_root / "workspace" if ctx.profile_root else None]:
        if place is not None and Path(place).is_dir():
            writable.append(str(Path(place).resolve()))
    for granted in getattr(ctx.scope, "granted", []) or []:
        if Path(granted).is_dir():
            writable.append(str(Path(granted).resolve()))
    return list(dict.fromkeys(writable))


def build(ctx) -> list[Tool]:
    cfg = (ctx.cfg or {})
    block = cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}
    term = (block or {}).get("terminal") if isinstance((block or {}).get("terminal"), dict) else {}
    require_containment = bool((term or {}).get("require_containment", True))
    network_default = bool((term or {}).get("network", True))
    default_timeout = float((term or {}).get("timeout_s") or DEFAULT_TIMEOUT)
    max_output = int((block or {}).get("max_result_bytes") or MAX_OUTPUT)

    def default_cwd() -> Path:
        sandbox = (ctx.profile_root / "sandbox") if ctx.profile_root else Path.cwd()
        return sandbox if sandbox.is_dir() else Path.cwd()

    def run(command: str, cwd: str = "", timeout_s: float = 0, network: bool | None = None) -> dict:
        text = (command or "").strip()
        timeout = float(timeout_s or default_timeout)
        allow_net = network_default if network is None else bool(network)
        # the command itself is never logged: it is the caller's data and may carry credentials. Its length, the
        # cwd and the outcome are what a reader needs to tell one call from another
        with trace.span("tool.terminal.run", channel=_lane(ctx), cwd=cwd or "sandbox", timeout_s=timeout,
                        network=allow_net, command_chars=len(text)) as sp:
            if not text:
                raise ToolError("command is required")
            target = Path(cwd).expanduser() if cwd else default_cwd()
            if not target.is_absolute() and ctx.profile_root:
                target = ctx.profile_root / target
            # the working directory must be a place the agent may write — the shell will write there
            try:
                target = ctx.scope.check(str(target), paths_mod.WRITE, tool="terminal")
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            if not target.is_dir():
                raise ToolError(f"{target} is not a directory")

            env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                   "LANG": os.environ.get("LANG", "C.UTF-8"),
                   "HOME": str(ctx.profile_root or target),
                   "ATHENA_ROOT": str(ctx.athena_root or ""),
                   "ATHENA_HOME": str(ctx.profile_root or ""),
                   "ATHENA_SANDBOX": str((ctx.profile_root / "sandbox") if ctx.profile_root else target),
                   "ATHENA_WORKSPACE": str((ctx.profile_root / "workspace") if ctx.profile_root else target),
                   "ATHENA_SYSTEM": str((Path(ctx.athena_root) / "athena-agent") if ctx.athena_root else "")}
            # XDG dirs point inside the sandbox: the read-only root means tools cannot create ~/.config, and every
            # command printed a bwrap errno line because of it
            for key, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_CACHE_HOME", ".cache"),
                             ("XDG_DATA_HOME", ".local/share"), ("XDG_STATE_HOME", ".local/state")):
                folder = (ctx.profile_root / "sandbox" / sub) if ctx.profile_root else (target / sub)
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                except OSError:
                    continue
                env[key] = str(folder)

            contained = False
            if require_containment:
                bwrap = _bwrap()
                argv = [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
                        "--die-with-parent", "--new-session", "--unshare-pid"]
                for place in _mounts(ctx):
                    argv += ["--bind", place, place]
                argv += _mask_args(ctx.athena_root, ctx.profile_root)
                if not allow_net:
                    argv += ["--unshare-net"]
                argv += ["--chdir", str(target), "--setenv", "HOME", env["HOME"],
                         "/bin/bash", "-lc", text]
                contained = True
            else:
                argv = ["/bin/bash", "-lc", text]

            try:
                done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                      cwd=None if contained else str(target),
                                      env={**env, "ATHENA_COMMAND": text} if contained else env)
            except subprocess.TimeoutExpired as exc:
                sp.failed(exc)
                return {"ok": False, "contained": contained, "error": f"timed out after {timeout:.0f}s",
                        "cwd": str(target)}
            except OSError as exc:
                sp.failed(exc)
                return {"ok": False, "contained": contained, "error": str(exc), "cwd": str(target)}

            out = (done.stdout or "") + (("\n" + done.stderr) if done.stderr else "")
            result = {"ok": done.returncode == 0, "exit": done.returncode, "cwd": str(target),
                      "contained": contained, "output": out[:max_output]}
            if contained and done.returncode != 0 and "bwrap:" in (done.stderr or ""):
                result["ok"] = False
                result["error"] = "bubblewrap refused to start — the command did NOT run uncontained"
            sp.ok(exit=done.returncode, bytes=len(out), contained=contained, ok=result["ok"], cwd=str(target))
            return result

    return [Tool(
        name="run",
        description="Run a shell command, contained so it can only write inside your sandbox and workspace.",
        parameters={"type": "object",
                    "properties": {"command": {"type": "string", "description": "The shell command to run."},
                                   "cwd": {"type": "string",
                                           "description": "Working directory; defaults to sandbox/."},
                                   "timeout_s": {"type": "number", "description": "Seconds before it is killed."},
                                   "network": {"type": "boolean",
                                               "description": "Allow network access (default from config)."}},
                    "required": ["command"]},
        fn=run,
    )]


def run(args=None, ctx=None):
    """The entry point the loader calls: `action` selects a verb, the rest are that verb's arguments."""
    from tools.loader import dispatch

    return dispatch(build, args, ctx, "terminal")

"""Where an agent may go — `ATHENA_ROOT`, `HOME`, `SANDBOX`, `WORKSPACE`, `DRIVE`, `SYSTEM`.

**Safety is about the person; security is about the agent.** Nothing here protects the user's files from bad
*intent* — that is the guard's job. This module answers one narrow question, for every file operation: *is this
path inside a place this agent is allowed to touch, for this operation?*

Three permission flags, two of them harmless:
  `read` and `list` change nothing; `write` creates; `modify` changes or destroys what already exists.

The scopes, in the operator's words:

| variable | what it is |
|---|---|
| `ATHENA_ROOT` | the directory that contains `.athena/` |
| `ATHENA_HOME` | this agent's profile, `.athena/profiles/<name>/` |
| `ATHENA_SANDBOX` | `.athena/profiles/<name>/sandbox/` — scripts and experiments go here, never in the workspace |
| `ATHENA_WORKSPACE` | **a list**: the profile's `workspace/` plus every folder the user granted by URL |
| `ATHENA_SYSTEM` | `.athena/athena-agent/` — the program itself: **readable, never writable** |

Rules:

1. **`ATHENA_SYSTEM` is read-only, always.** She may read her own code to understand herself, and she may ship a
   *plugin* to gain functionality — but the system tree is never written by an agent. A program an agent can
   rewrite is not a program, it is a suggestion.
2. **`HOME` is writable except for `SYSTEM`.** Sessions, memories, configs, workspace and sandbox all live there.
3. **`SANDBOX` and `WORKSPACE` are writable**; they are the two places an agent is meant to *do* things.
4. **Everything else is refused**, unless the user granted that folder as a workspace URL.
5. **Grants are per profile and deduplicated**: adding the same URL twice is answered with "you already have
   access", not silently accepted.
7. **Symlinks are resolved before the check**, so a link cannot be used to step outside a scope.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from core.jsonio import read_json
from core import durable
from core import trace

# Three permission flags. Two are harmless and two are not — `list` is a read, so it rides the read flag.
READ = "read"        # harmless: reading a file's contents
LIST = "list"        # harmless: enumerating a directory
WRITE = "write"      # harmful: creating something new
MODIFY = "modify"    # harmful: changing or destroying something that already exists

HARMLESS = (READ, LIST)
HARMFUL = (WRITE, MODIFY)
# which flag an operation needs
FLAG_FOR_OP = {READ: READ, LIST: READ, WRITE: WRITE, MODIFY: MODIFY,
               "r": READ, "w": MODIFY}      # "r"/"w" kept for callers from before the flags

DENY_HINT = ("ask the user to add it as a workspace (a filesystem URL pointing at the folder) — that is the "
             "only way outside your own scope")


# Names inside an agent's own home that are readable and never writable. Each carries its own reason, because
# "refused" alone tells an agent nothing about why it should stop.
PROTECTED = {
    "plugins": "a plugin is code, and code an agent can write is capability it granted itself",
    "config.yaml": "the config decides which capabilities run",
    "state.json": "state belongs to the lifecycle, not to the agent it describes",
    "identity.json": "an id its own subject can rewrite is not an identity",
    "workspaces.json": "this file *is* the agent's permission list — an agent that can write it grants itself any "
                       "folder, and the guard would then honour it",
}


class Scope:
    """One agent's allowed places, resolved once and reused by every tool."""

    def __init__(self, profile_root: Path, athena_root: Path | None = None, workspaces: list[str] | None = None):
        self.home = Path(profile_root).resolve()
        self.root = Path(athena_root or (self.home.parent.parent)).resolve()
        self.system = (self.root / "athena-agent").resolve()
        self.sandbox = (self.home / "sandbox").resolve()
        self.workspace = (self.home / "workspace").resolve()
        self.granted = [Path(w).resolve() for w in (workspaces or []) if w]

    # -- the list ----------------------------------------------------------
    def workspaces(self) -> list[Path]:
        """The workspace list: the profile's own folder first, then everything the user granted."""
        seen, out = set(), []
        for entry in [self.workspace, *self.granted]:
            if entry not in seen:
                seen.add(entry)
                out.append(entry)
        return out

    def variables(self) -> dict:
        """The environment an agent's scripts see — the same names the guard uses."""
        return {
            "ATHENA_ROOT": str(self.root),
            "ATHENA_HOME": str(self.home),
            "ATHENA_SANDBOX": str(self.sandbox),
            "ATHENA_WORKSPACE": os.pathsep.join(str(w) for w in self.workspaces()),
            "ATHENA_SYSTEM": str(self.system),
        }

    # -- grants ------------------------------------------------------------
    def grant(self, url: str) -> dict:
        """Grant a folder outside the scope. Idempotent, and it says so rather than pretending."""
        try:
            return self._grant(url)
        except ValueError as exc:
            # a refusal here is the user's scope boundary being tested from the outside: name the folder, not the key
            trace.failure("workspace.refused", exc, channel=self.home.name, url=str(url)[:200])
            raise

    def _grant(self, url: str) -> dict:
        if not url or not str(url).strip():
            raise ValueError("a workspace needs a filesystem URL")
        target = Path(str(url).strip()).expanduser()
        if not target.is_absolute():
            raise ValueError(f"{url!r} is not an absolute path — a workspace is a filesystem URL, not a relative one")
        try:
            resolved = target.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"{url!r} does not exist ({exc.strerror or exc})") from exc
        if not resolved.is_dir():
            raise ValueError(f"{url!r} is a file; a workspace must be a folder")
        for existing in self.workspaces():
            if resolved == existing:
                return {"added": False, "path": str(resolved),
                        "message": f"already available: {resolved} is already in this agent's workspace list"}
        self.granted.append(resolved)
        trace.event("workspace.granted", level="debug", channel=self.home.name, path=str(resolved))
        return {"added": True, "path": str(resolved)}

    def revoke(self, url: str) -> dict:
        target = Path(str(url).strip()).expanduser().resolve()
        kept = [w for w in self.granted if w != target]
        removed = len(kept) != len(self.granted)
        self.granted = kept
        return {"removed": removed, "path": str(target)}

    # -- the guard ---------------------------------------------------------
    def permissions(self, raw: str) -> dict:
        """What this agent may do here: `{read, list, write, modify}` booleans. Never raises."""
        try:
            resolved = self.check(raw, READ)
        except ValueError:
            return {flag: False for flag in (READ, LIST, WRITE, MODIFY)}
        allowed = self._flags(resolved)
        return {flag: flag in allowed for flag in (READ, LIST, WRITE, MODIFY)}

    def _flags(self, resolved: Path) -> tuple:
        """The flags a resolved path grants.

        Order matters, and two rules come first on purpose:

        1. **No secret is ever readable by an agent** — not the global one, not another agent's, and not its own.
           A key reaches a provider through the resolver; a key must never reach the model, because whatever the
           model can read it can repeat into a transcript.
        2. **`ATHENA_ROOT` is shared infrastructure, read and list only**, and every agent's home is carved out of
           it: `profiles/<other>/` belongs to that agent, not to this one.
        """
        if resolved.name == ".secret" or resolved.name.startswith(".secret."):
            return ()                                  # credentials: no flags at all, for anyone
        if _within(resolved, self.system):
            return (READ, LIST)                        # both HARMLESS flags; her own program, never changed
        # `plugins/` and `config.yaml` inside her own home are the self-modification door: a plugin is arbitrary
        # code and `plugins.enabled` is what lets it run, so an agent able to write both could grant itself
        # anything and call it a plugin. Readable, so she can see what she has; never writable.
        if any(_within(resolved, self.home / name) for name in PROTECTED):
            return (READ, LIST)
        if any(_within(resolved, place) for place in (self.home, self.sandbox, *self.workspaces())):
            # Same path, different filesystem: something was mounted inside a writable tree. A bind mount is
            # invisible to a path check — it *is* inside — so compare the device of the nearest existing parent
            # against the profile's own. No variable for this; it is a property of the path, not a setting.
            if _foreign_mount(resolved, self.home):
                # debug, not a failure: this is a standing property of the tree, and `permissions()` probes it per
                # path — the refusal itself is still reported by `check()`, this says *why* the flags came back empty
                trace.event("scope.foreign_mount", level="debug", channel=self.home.name,
                            path=str(resolved), home=str(self.home))
                return ()
            return (READ, LIST, WRITE, MODIFY)
        if any(_within(resolved, place) for place in self.granted):
            return (READ, LIST, WRITE, MODIFY)
        if _within(resolved, self.root):
            # `ATHENA_ROOT`: shared infrastructure (config.yaml, provider.json, models/, logs/, metrics/) is
            # readable; another agent's home is not this agent's business
            other_home = any(_within(resolved, other)
                             for other in _profile_dirs(self.root) if other != self.home)
            return () if other_home else (READ, LIST)
        return ()

    def check(self, raw: str, mode: str = READ, approvals=None, tool: str = "") -> Path:
        """`_check` plus one line of evidence when it refuses."""
        try:
            return self._check(raw, mode, approvals, tool)
        except ValueError as exc:
            _emit("refused", raw, f"{mode}: {exc}", getattr(self.home, "name", ""))
            raise

    def _check(self, raw: str, mode: str = READ, approvals=None, tool: str = "") -> Path:
        """Resolve `raw` and return it, or raise ValueError naming the flag that was refused and why.

        When an `ApprovalBroker` is supplied, a refusal that is merely *out of scope* becomes a question put to
        the user and the call blocks for the answer. Three cases never ask, because they are lines rather than
        choices: a `.secret`, the system tree, and another agent's home.
        """
        if not str(raw or "").strip():
            raise ValueError("a path is required")
        candidate = Path(str(raw).strip()).expanduser()
        if not candidate.is_absolute():
            # relative paths are anchored at the agent's home, which is where it works
            candidate = self.home / candidate
        # resolve BEFORE judging: a symlink must not be able to step outside a scope
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise ValueError(f"cannot resolve {raw!r}: {exc.strerror or exc}") from exc

        needed = FLAG_FOR_OP.get(mode, mode)
        if needed not in (READ, LIST, WRITE, MODIFY):
            raise ValueError(f"unknown operation {mode!r} — use read, list, write or modify")

        if resolved.name == ".secret" or resolved.name.startswith(".secret."):
            raise ValueError(
                f"{resolved} is a credentials file — no agent may read or write a `.secret`, not the global one, "
                f"not another agent's and not its own. Keys reach a provider through the resolver, never the model")
                # never askable: an approval prompt for a key file trains people to click Allow

        allowed = self._flags(resolved)
        if allowed:
            if needed in allowed:
                return resolved
            # partial flags: say which line this is, because the three are refused for different reasons
            if _within(resolved, self.system):
                raise ValueError(
                    f"{resolved} is inside ATHENA_SYSTEM ({self.system}): the {needed!r} flag is refused — an agent "
                    f"may read its own program but never change it. Add a plugin under {self.home / 'plugins'} to "
                    f"extend functionality")   # never askable: the program is not negotiable
            for name, why in PROTECTED.items():
                if _within(resolved, self.home / name):
                    raise ValueError(
                        f"{resolved} is your own {name}: readable, so you can see what you have, but never "
                        f"writable — {why}. Ask the user to change it")
            raise ValueError(
                f"{resolved} is readable but the {needed!r} flag is refused here; add it as a workspace if you "
                f"need to work in it")

        if _within(resolved, self.root) and any(_within(resolved, other) for other in _profile_dirs(self.root)):
            raise ValueError(f"{resolved} is inside another agent's home — that profile is not yours to read")
            # never askable: one agent asking the user for another agent's files is a social-engineering prompt

        message = (f"{resolved} is outside this agent's scope ({self.home}) — no flags at all here; {DENY_HINT}")

        # already granted for this session?
        if approvals is not None and approvals.covers(self.home.name, str(resolved), needed):
            return resolved

        # out of scope is a QUESTION, not a verdict: ask, and wait for the answer
        if approvals is not None:
            decision = approvals.request(
                agent=self.home.name, path=str(resolved), op=needed, reason=message, tool=tool,
                flags=(READ, LIST, WRITE, MODIFY))
            if decision.allowed:
                if decision.scope == "permanent":
                    try:
                        self.grant(str(resolved if resolved.is_dir() else resolved.parent))
                    except ValueError:
                        pass
                return resolved
            message = decision.message or message

        # the tree this agent may touch does not contain the path: this is the escape the guard exists to stop,
        # so it is recorded with the frame that refused, not left to the exception's text
        trace.failure("scope.outside", channel=self.home.name, path=str(resolved), op=needed,
                      home=str(self.home))
        raise ValueError(message)


def _profile_dirs(root: Path) -> list[Path]:
    """Every agent home under this ATHENA_ROOT."""
    try:
        base = Path(root) / "profiles"
        return [p.resolve() for p in base.iterdir() if p.is_dir()]
    except OSError as exc:
        # an unreadable profiles/ reads as "no other agents", which widens the root check — say it out loud
        trace.event("scope.profile_dirs.unreadable", level="debug", path=str(root),
                    error=type(exc).__name__)
        return []


def _device_of(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return probe.stat().st_dev
    except OSError:
        return -1


def _foreign_mount(resolved: Path, home: Path) -> bool:
    """True when `resolved` sits on a different filesystem from the profile — a mount inside the tree."""
    here, there = _device_of(resolved), _device_of(home)
    return here != -1 and there != -1 and here != there


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def load_workspaces(profile_root: Path) -> list[str]:
    """Grants persist per profile, beside the config: `profiles/<name>/workspaces.json`."""
    path = Path(profile_root) / "workspaces.json"
    data = read_json(path, [])
    entries = data.get("workspaces") if isinstance(data, dict) else data
    return [str(e) for e in (entries or [])]


def save_workspaces(profile_root: Path, entries: list[str]) -> Path:
    """Store grants canonically: two spellings of the same folder must not become two entries."""
    entries = [str(Path(e).expanduser().resolve()) for e in entries if str(e).strip()]
    path = Path(profile_root) / "workspaces.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    durable.write_state(path, json.dumps({"workspaces": entries}, indent=2))
    trace.event("workspace.saved", level="debug", channel=Path(profile_root).name, count=len(entries))
    return path


# -- evidence sink ---------------------------------------------------------
# The guard records what it refused so the red-team subsystem can attribute a later change to an internal attempt.
# Injected rather than imported: this module must stay loadable on its own (tests, CLI, scopes without a server).
_EVENT_SINK = None


def set_event_sink(sink) -> None:
    """`sink(kind, path, detail, lane)` — called on every refusal. `None` disables it."""
    global _EVENT_SINK
    _EVENT_SINK = sink


def _emit(kind: str, path, detail: str, lane: str = "") -> None:
    if _EVENT_SINK is None:
        return
    try:
        _EVENT_SINK(kind, str(path), detail, lane)
    except Exception:                                # noqa: BLE001 — an observability hook must never break a refusal
        pass


def for_profile(profile_root: Path, athena_root: Path | None = None) -> Scope:
    return Scope(profile_root, athena_root, load_workspaces(profile_root))

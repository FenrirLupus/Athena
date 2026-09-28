"""Profiles — the agent registry.

Each profile is a full AGENT: its own identity (assistant/ASSISTANT.md +
user/USER.md), its own skills, plugins, workspace, sessions and vault
entries (tagged by profile). The DEFAULT profile IS the root (.athena/) —
mirroring the layout where the default profile lives at the home root and
named profiles live under profiles/<name>/.

Layout:
    .athena/                        ← default profile (root)
    .athena/profiles/<name>/        ← named profiles
        ├── assistant/ASSISTANT.md  ← that agent's identity
        ├── user/USER.md
        ├── skills/                 ← that agent's skills
        ├── plugins/                ← that agent's plugins
        └── workspace/              ← that agent's workspace
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.config import (ATHENA_ROOT, DEFAULT_PROFILE_ROOT, SHARED_PLUGINS,
                         SHARED_TOOLS, SHARED_SKILLS)

DEFAULT_PROFILE = "default"
# Module-global so doctor tests can patch it for isolation. Read at
# CALL time (never captured & frozen at import) — see _profiles_dir.
PROFILES_DIR = ATHENA_ROOT / "profiles"


def _profiles_dir() -> Path:
    """profiles/ under the CURRENT ATHENA_ROOT — read at call time.

    Uses the module-global PROFILES_DIR so doctor tests that patch
    it (isolation) keep working.
    """
    return PROFILES_DIR


def _ensure_shared_roots() -> None:
    """Create the shared capability homes (.athena/plugins|tools|skills)
    if missing. Always real dirs at the .athena root — the one place
    every profile reads them from."""
    for root in (SHARED_PLUGINS, SHARED_TOOLS, SHARED_SKILLS):
        root.mkdir(parents=True, exist_ok=True)


def _symlink_if_missing(link: Path, target: Path) -> None:
    """Make `link` a native symlink to `target`.

    Idempotent: an existing symlink to the target is left alone; an
    existing REAL dir is converted ONLY when empty (a profile that has
    real content keeps it — the shared homes take over new dirs).
    """
    try:
        if link.is_symlink():
            return
        if link.exists() and link.is_dir():
            try:
                link.rmdir()  # only succeeds on an EMPTY dir
            except OSError:
                return  # real content — leave it
        link.symlink_to(target, target_is_directory=True)
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"profile symlink failed: {link} → {target}: {exc}",
                  source="intelligence", action="ensure_layout")


@dataclass
class Profile:
    name: str
    root: Path            # the profile's home directory
    is_default: bool

    @property
    def assistant_identity(self) -> Path:
        return self.root / "assistant" / "ASSISTANT.md"

    @property
    def user_identity(self) -> Path:
        return self.root / "user" / "USER.md"

    @property
    def skills_dir(self) -> Path:
        return self.root / "skills"

    @property
    def plugins_dir(self) -> Path:
        return self.root / "plugins"

    @property
    def workspace_dir(self) -> Path:
        """The profile's workspace — the work dir for terminal + files.

        Settable by nature (the Operator's spec): the profile's config.yaml
        may set `workspace.dir` to point at any project path; when unset
        (null), the profile works in its OWN workspace/ dir.
        """
        try:
            from core.config import load_config
            cfg = load_config(self.name if not self.is_default else "")
            custom = (cfg.get("workspace") or {}).get("dir")
            if custom and str(custom).strip():
                p = Path(str(custom).strip()).expanduser()
                p.mkdir(parents=True, exist_ok=True)
                return p
        except Exception:
            pass
        root = self.root / "workspace"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @property
    def sandbox_dir(self) -> Path:
        """The profile's SANDBOX — where its terminal sessions open.

        Mirrors the architecture: each profile owns a sandbox/
        dir — the safe home base a terminal starts in before navigating
        elsewhere. Settable by nature via `sandbox.dir` in the profile's
        config.yaml; unset (null) = the profile's own sandbox/ dir.
        """
        try:
            from core.config import load_config
            cfg = load_config(self.name if not self.is_default else "")
            custom = (cfg.get("sandbox") or {}).get("dir")
            if custom and str(custom).strip():
                p = Path(str(custom).strip()).expanduser()
                p.mkdir(parents=True, exist_ok=True)
                return p
        except Exception:
            pass
        root = self.root / "sandbox"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def ensure_layout(self) -> None:
        """Create the profile's standard subdirectories if missing.

        The Operator's 08-10 architecture: plugins/tools/skills are SHARED
        across all profiles — each profile's dirs are native symlinks
        to the shared homes (.athena/plugins, .athena/tools,
        .athena/skills). The rest (assistant/user/workspace/sessions)
        are per-profile real dirs.
        """
        _ensure_shared_roots()
        for sub in ("assistant", "user", "workspace", "sessions", "agent",
                    "runtime", "sandbox", "logs", "events"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        _symlink_if_missing(self.root / "plugins", SHARED_PLUGINS)
        _symlink_if_missing(self.root / "tools", SHARED_TOOLS)
        _symlink_if_missing(self.root / "skills", SHARED_SKILLS)
        # The Operator's completeness rule (08-11): a profile ALWAYS has all
        # of its applicable files — identity, memory, emotion — on BOTH
        # sides. Missing ones get their defaults at every load, so no
        # profile is ever born half-formed or silently missing a file.
        ensure_profile_files(self)


def ensure_profile_files(profile: Profile) -> dict:
    """Check every applicable file of a profile; create defaults missing.

    The six system files (the Operator's 08-11 completeness rule — ALL files
    MUST exist accordingly):
        assistant/ASSISTANT.md  — the agent's identity
        assistant/MEMORY.md     — notes on her own side
        assistant/EMOTION.md    — the agent's emotion vector
        user/USER.md            — the operator's identity
        user/MEMORY.md          — what is known about the operator
        user/EMOTION.md         — the operator's emotion vector

    Returns {checked, created, files} — created lists the files that
    were missing and seeded with defaults.
    """
    created = []
    root = profile.root
    name = profile.name if not profile.is_default else DEFAULT_PROFILE
    display = (profile.name or "default").lstrip(".")
    # DEFENSIVE DIRS (the Operator's 08-12 wipe-test fix): ensure_profile_files
    # is the "always complete" guarantee — it must not ASSUME the side
    # dirs exist (a wiped/recreated profile may lack user/ or assistant/).
    # Create them before writing, so the six-file rule always holds.
    for side in ("assistant", "user"):
        (root / side).mkdir(parents=True, exist_ok=True)
    try:
        from core.identity import _default_base  # noqa: F401
    except Exception:
        pass

    # ASSISTANT.md — the agent identity. THE STANDARD MARKDOWN SCHEMA
    # (the Operator's 08-12 spec): HEADER (frontmatter YAML) → empty line →
    # BODY (the identity sections, NO delimiters) → empty line →
    # FOOTER (closing). Exactly 4 --- delimiters.
    ap = root / "assistant" / "ASSISTANT.md"
    if not ap.exists():
        ap.write_text(
            "---\n"
            'name_first: null\n'
            'name_last: null\n'
            'name_nick: null\n'
            'gender: null\n'
            'sexuality: null\n'
            'sun_sign: null\n'
            'moon_sign: null\n'
            'rising_sign: null\n'
            'birth_date: null\n'
            'home: "The Assistant Hub, Headquarters"\n'
            'role: "Agent"\n'
            "---\n"
            "\n"
            f"# {display}\n"
            f"**{display}** — an Athena agent profile.\n"
            "\n"
            "# Who I Am\n"
            "- Placeholder: this agent's identity details.\n"
            "\n"
            "# What I Do\n"
            "- Placeholder: this agent's role and responsibilities.\n"
            "\n"
            "# Boundaries\n"
            "- Placeholder: this agent's limits and safeguards.\n"
            "\n"
            "---\n"
            "# Footer\n"
            "Standard Markdown Schema: 4 delimiters (2 Header, 2 Footer).\n"
            "---\n",
            encoding="utf-8")
        created.append("assistant/ASSISTANT.md")

    # USER.md — the operator identity. THE STANDARD MARKDOWN SCHEMA:
    # HEADER (full null schema) → empty line → BODY (operator sections,
    # NO delimiters) → empty line → FOOTER (closing).
    up = root / "user" / "USER.md"
    if not up.exists():
        up.write_text(
            "---\n"
            'name_first: null\n'
            'name_last: null\n'
            'name_nick: null\n'
            'gender: null\n'
            'sexuality: null\n'
            'sun_sign: null\n'
            'moon_sign: null\n'
            'rising_sign: null\n'
            'birth_date: null\n'
            'home: null\n'
            'role: null\n'
            "---\n"
            "\n"
            "# User\n"
            "**The operator** — known details fill in over time.\n"
            "\n"
            "# About the Operator\n"
            "- Placeholder: what is known about the operator.\n"
            "\n"
            "# Preferences\n"
            "- Placeholder: how the operator likes things done.\n"
            "\n"
            "# Boundaries\n"
            "- Placeholder: what the operator expects and permits.\n"
            "\n"
            "---\n"
            "# Footer\n"
            "Standard Markdown Schema: 4 delimiters (2 Header, 2 Footer).\n"
            "---\n",
            encoding="utf-8")
        created.append("user/USER.md")

    # MEMORY.md on both sides — the website-model shape: HEADER (the
    # memory store title) → BODY (one empty entry block) → FOOTER (the
    # closing ---). The memory reader splits on ---, so an empty entry
    # block keeps the file parseable from day one.
    for side, title in (("assistant", "Assistant Memory"),
                        ("user", "User Memory")):
        mp = root / side / "MEMORY.md"
        if not mp.exists():
            mp.write_text(
                "---\n"
                f"# {title}\n"
                "- Placeholder: notes are added here over time.\n"
                "---\n",
                encoding="utf-8")
            created.append(f"{side}/MEMORY.md")

    # EMOTION.md on both sides — the standard vector format.
    try:
        from core.emotion import write_emotion, default_emotion
        for side in ("assistant", "user"):
            ep = root / side / "EMOTION.md"
            if not ep.exists():
                write_emotion(side, name, default_emotion(), mood="Neutral")
                created.append(f"{side}/EMOTION.md")
    except Exception:
        pass

    return {"checked": 6, "created": created}


def default_profile() -> Profile:
    # the Operator's spec: the default profile is `.default` (dot-prefixed
    # like the system profiles) — it lives at profiles/.default/ and is
    # LOCKED (architecture-critical). The legacy name "default" resolves
    # to it, but the canonical name is ".default".
    return Profile(name=".default", root=DEFAULT_PROFILE_ROOT,
                   is_default=True)


def get_profile(name: str) -> Profile | None:
    """Return a named profile (or the default). None if it doesn't exist."""
    name = (name or "").strip()
    if not name or name == DEFAULT_PROFILE:
        return default_profile()
    root = PROFILES_DIR / name
    if not root.is_dir():
        return None
    return Profile(name=name, root=root, is_default=False)


def current_profile() -> Profile:
    """The ACTIVE profile: the runtime state variable (profile.active in
    config.yaml) if set, else the default. `athena profile switch <name>`
    writes the config variable; this is what the CLI/server consult at
    boot."""
    from core.config import active_profile_name
    name = active_profile_name()
    if name and name != DEFAULT_PROFILE:
        p = get_profile(name)
        if p is not None:
            return p
    return default_profile()


def list_profiles() -> list[Profile]:
    """All profiles: the default (.default) first, then named ones."""
    result = [default_profile()]
    if _profiles_dir().exists():
        for pdir in sorted(_profiles_dir().iterdir()):
            if pdir.is_dir() and pdir.name != ".default":
                result.append(Profile(name=pdir.name, root=pdir, is_default=False))
    return result


def create_profile(name: str) -> Profile:
    """Create a new named profile with its layout. Fails if it exists.

    A new profile is born with the FULL config schema (the Operator's 08-10
    rule): its config.yaml carries every setting the platform config has —
    populated 1:1, nothing missing — seeded from the platform config and
    overlaid with nothing (a fresh profile has no own values yet).
    """
    name = (name or "").strip().lower()
    if not name or name == DEFAULT_PROFILE:
        raise ValueError("invalid profile name")
    root = _profiles_dir() / name
    if root.exists():
        raise ValueError(f"profile already exists: {name}")
    profile = Profile(name=name, root=root, is_default=False)
    try:
        profile.ensure_layout()
        # ensure_layout → ensure_profile_files creates ALL six system files
        # (ASSISTANT.md, USER.md, MEMORY.md ×2, EMOTION.md ×2) with defaults.
        # Full-schema config.yaml at birth (the Operator's rule: no partial file).
        from core.config import profile_schema, save_config
        save_config(profile_schema(profile=name), profile=name)
    except Exception:
        # ATOMIC BIRTH (the Operator's no-partial rule): if creation fails
        # partway (e.g. a test's isolated config root), remove the partial
        # dir so no half-born profile leaks into the registry.
        import shutil
        shutil.rmtree(root, ignore_errors=True)
        raise
    return profile

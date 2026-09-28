"""Athena config spine — the one place every part reads settings from.

Loads .athena/config.yaml when present; otherwise falls back to defaults.
Settings live in config.yaml; credentials live in authentication.json.
Secrets never live in this file.
"""
from __future__ import annotations

import os
from pathlib import Path

from core.logging import log_event

ATHENA_ROOT = Path(os.environ.get("ATHENA_ROOT", str(Path.home() / ".athena"))).resolve()

# The Operator's 08-12 path doctrine:
#   ATHENA_ROOT    = .athena              — the install root (configs,
#                                            auths, secrets, profiles/)
#   ATHENA_HOME    = profiles/{profile}/  — a profile's HOME (the
#                                            agent's native dir)
#   ATHENA_PROJECT = wherever the user specifies — a designated
#                    PROJECT directory Athena works in (separate from
#                    the install; set via env or config, null = no
#                    active project).
ATHENA_HOME = ATHENA_ROOT / "profiles"
ATHENA_PROJECT = None
_proj_env = os.environ.get("ATHENA_PROJECT", "").strip()
if _proj_env:
    ATHENA_PROJECT = Path(_proj_env).expanduser().resolve()

# The DEFAULT profile's home (the Operator's spec): profiles/.default/ —
# dot-prefixed like the system profiles (.nurse/.janitor), so Athena's
# own name stays clean. All profile-data paths resolve through here.
DEFAULT_PROFILE_ROOT = ATHENA_ROOT / "profiles" / ".default"

# The platform config file IS the default profile's config (the Operator's
# 08-10 architecture): .athena/profiles/.default/config.yaml — the
# .janitor/.nurse system agents share it natively unless they get an
# override. authentication.json + .secret remain the only GLOBAL
# credential sources, at the .athena root.
CONFIG_PATH = DEFAULT_PROFILE_ROOT / "config.yaml"
AUTH_PATH = ATHENA_ROOT / "authentication.json"

# The Athena version (shown in the CLI banner).
# THE VERSION (the Operator's release model):
#   1.0.0 = STABLE  — uploadable to GitHub as-is
#   0.1.0 = BETA    — feature-complete, works end-to-end (current)
#   0.0.1 = ALPHA   — core barely works
# Single source of truth — everything reads this one constant.
VERSION = "0.1.0"

# The Athena WIKI — the stable doctrine (the Operator's 08-12 spec).
# The wiki is the known-good reference for how Athena operates. Athena,
# the nurse, and the janitor consult it as the architecture doctrine —
# and when a local optimization diverges from it, the change is
# PROPOSED as a document with a release tier (Stable/Beta/Alpha), never
# silently applied. Only the Operator can green-light a release.
WIKI_URL = "https://github.com/FenrirLupus/Athena-Agent/wiki"
# The LOCAL wiki mirror (the Operator's 08-12 spec): .athena/.wiki/ is a
# clone of the wiki repo — the agents read the known-good doctrine
# OFFLINE instead of using the browser every time. `athena wiki sync`
# pulls the latest from the remote.
WIKI_REPO = "https://github.com/FenrirLupus/Athena-Agent.wiki.git"
WIKI_DIR = ATHENA_ROOT / ".wiki"
# The Athena-SYSTEM repo (the Operator's 08-12 snapshot model): athena-system
# is the ONLY folder that gets uploaded to GitHub, snapshotted, and
# replaced during updates. The PATCH tier clones/pulls this repo.
ATHENA_SYSTEM_REPO = "https://github.com/FenrirLupus/Athena-Agent.git"

DEFAULTS = {
    # (The identity section was REMOVED — the Operator's 08-15 call: the
    # profile's identity lives in ASSISTANT.md + USER.md frontmatter, NOT
    # config.yaml. flow_names() resolves from the identity files only.)
    # Server Loop
    "server": {
        "tick_interval_s": 60,
        "host": "127.0.0.1",
        # THE DEFAULT PORT (the 08-14 fix): 51420, NOT 8080 — a fresh
        # boot reads DEFAULTS before the profile seed lands (a boot
        # race), and the OLD default bound the GUI on 8080 where the
        # operator couldn't find it.
        "port": 51420,
    },
    # Thinking budget — the valve on autonomous provider spend
    "thinking_budget": {
        "max_calls_per_hour": 10,
        "min_priority": 0.5,
        "cooldown_s": 60,
        "fail_closed": True,
    },
    # THE AUTONOMY CATEGORY (the 08-15 audit fix): the background-agent
    # cadence — the nurse's first-check delay + interval. The code reads
    # these from config; they must be SETTABLE (they were hardcoded
    # defaults with no config home before).
    "autonomy": {
        "nurse_first_delay_s": 3600,
        "nurse_interval_s": 7200,
    },
    # Message Loop + Iteration Budget — merged under the BUDGET category
    # (the Operator's 08-15 schema: Category > Section > Setting, max 3).
    # Sections: iteration (the turn caps), message_loop (the loop caps).
    "budget": {
        "iteration": {
            "main_iterations": 100,
            "main_max_tokens": 5120,
            "subagent_iterations": 50,
            "subagent_max_tokens": 2560,
        },
        "message_loop": {
            "max_iterations": 500,
            # THE MAX TOKENS KNOB (the 08-14 fix): the per-turn output cap.
            # The doctor's caps test asserts it; the seed carries it.
            "max_tokens": 5120,
            "recent_window": 10,
        },
    },
    # THE TOOL LOOP GUARDRAILS (the 08-15 schema fix): the loop-guard
    # dials — warnings/hard-stop thresholds. Real defaults; nulls from
    # the settings page never clobber them (the null-skip).
    "tool_loop_guardrails": {
        "warnings_enabled": True,
        "hard_stop_enabled": False,
        "warn_after": {
            "exact_failure": 2,
            "same_tool_failure": 3,
            "idempotent_no_progress": 3,
        },
        "hard_stop_after": {
            "exact_failure": 5,
            "same_tool_failure": 8,
            "idempotent_no_progress": 8,
        },
        "loop_caps": {
            "max_web_searches": 50,
            "max_subagents": 50,
        },
    },
    # Context: compression + retrieval (CONTEXT.md) — merged under the
    # CONTEXT category (the Operator's 08-15 schema: max 3 layers).
    "context": {
        "compression": {
            "context_window": 32000,
            "upper_threshold": 0.8,
            "lower_threshold": 0.4,
        },
        "retrieval": {
            "enabled": True,
            "session_first": True,
            "semantic": True,
            # the Operator's rule (08-10): feature models default to NULL — never
            # auto-selected. No vision/embeddings baked in; when the user has
            # no such models, null means "not configured" (graceful fallback).
            "embedding_model": None,
        },
    },
    # Database files live in the sessions/ directory:
    #   sessions/vault/            — the archive + its backups
    #     vault.db                 — the chronological archive (ALL conversations)
    #     vault-backup-###.db      — rotating backups of the archive
    #   sessions/session-{UUID}.db — one file PER session (its message history)
    "db": {
        "dir": "sessions",
        "vault_dir": "vault",
        "vault": "vault.db",
        "session_prefix": "session-",
    },
    # Provider — the registry reference (base_url, api_key, discovered
    # models live in authentication.json) + the streaming knob.
    "provider": {
        # THE STREAMING KNOB (the Operator's 08-14 spec): streaming is a
        # SETTABLE config setting (provider.streaming: true/false). The
        # GUI chat types replies live when true; false = blocking replies.
        # The default is TRUE — a null/missing value resolves to true
        # (never silently disables live typing).
        "streaming": True,
    },
    # MODELS (the 08-15 schema: the Models tab) — the active provider+model
    # per model type. Category > Section (reason/vision/embedding) > Setting
    # (provider/model/fallback_*). The catalog lives in authentication.json.
    "models": {
        "reason": {
            "provider": None,
            "model": None,
            "fallback_provider": None,
            "fallback_model": None,
        },
        "vision": {
            "provider": None,
            "model": None,
            "fallback_provider": None,
            "fallback_model": None,
        },
        "embedding": {
            "provider": None,
            "model": None,
            "fallback_provider": None,
            "fallback_model": None,
        },
    },
    # Channels — who may speak and what each role may USE (default deny).
    # Code defaults in channels.py; these are the safe baseline.
    # THE 08-15 FIX: the user channel carries the WRITE tools (write_file,
    # append, patch) so the agent can produce files — the PERMISSION
    # engine gates them (the permissions.yaml model).
    "channels": {
        "user": {"tools": ["read_file", "fs_stat", "terminal",
                           "write_file", "append", "patch"], "skills": []},
        "assistant": {"tools": ["read_file", "terminal"], "skills": []},
        "system": {"tools": ["*"], "skills": ["*"], "may_think": True},
    },
    # Workspace — where the profile works on files. NULL (the default)
    # means the profile's OWN workspace/ dir; set a path to point the
    # terminal + file tools at a project elsewhere (the model:
    # every agent works inside its own workspace, settable by nature).
    "workspace": {
        "dir": None,
    },
    # Sandbox — the profile's terminal home base. NULL (the default)
    # means the profile's OWN sandbox/ dir; set a path to relocate it.
    "sandbox": {
        "dir": None,
    },
    # Emotion system (the Operator's 08-11 spec → 08-15 trim): enabled is
    # the MASTER switch — when off, the emotion cycle never runs. The
    # llm_gate + min_chars knobs were REMOVED (the 08-15 call: the emotion
    # word + mood sentence are determined INSIDE the workflow's LLM call —
    # there is no separate emotion call to gate, and the felt word is a
    # single label, not a length-threshold thing).
    "emotion": {
        "enabled": True,
    },
}


# The SYSTEM profiles (dot-prefixed): the internal system agents.
# They share the DEFAULT profile's config natively (one brain, shared
# credentials) unless an override config.yaml exists for them.
SYSTEM_PROFILES = (".default", ".janitor", ".nurse")

# Shared capability roots (the Operator's 08-10 spec): every profile uses
# the SAME plugins/tools/skills — native symlinks from each profile
# dir to these shared homes. WORKFLOWS follow the same model (the
# Operator's 08-12 spec): the shared home is .athena/workflows/, and
# each profile gets a workflows symlink to it.
SHARED_PLUGINS = ATHENA_ROOT / "plugins"
SHARED_TOOLS = ATHENA_ROOT / "tools"
SHARED_SKILLS = ATHENA_ROOT / "skills"
SHARED_WORKFLOWS = ATHENA_ROOT / "workflows"


def profile_config_path(profile: str = "") -> Path:
    """The config.yaml for a profile.

    The Operator's architecture (08-12, per-profile config): the SERVER is
    the shared source of power — authentication.json + .secret are GLOBAL
    (every profile shares the one set of credentials). But each profile
    is its own AGENT and owns its OWN config.yaml — the OPERATOR sets up
    each system profile individually. NO fallback: .nurse/.janitor/named
    profiles each have their own config (materialized at profile creation
    as a setup seed from the default's).

        "" / "default" / ".default"   → profiles/.default/config.yaml
        <system profile> (.janitor/
            .nurse)                    → profiles/<name>/config.yaml (OWN)
        <named profile>                → profiles/<name>/config.yaml
    """
    name = (profile or "").strip()
    if not name or name in ("default", ".default"):
        return CONFIG_PATH
    return ATHENA_ROOT / "profiles" / name / "config.yaml"


def load_config(profile: str = "") -> dict:
    """Load a config.yaml (if present) merged over defaults.

    profile="" (or "default"/".default") → profiles/.default/config.yaml
    (the DEFAULT agent's own config); a system profile (.janitor/.nurse)
    → its own file when present, else the .default's; a NAMED profile →
    that profile's own config.yaml (each agent owns its config; only
    authentication.json + .secret are shared). Simple and flat.

    WIPE RECOVERY (the Operator's 08-12 wipe-test): load_raw_config falls
    back to the ROOT config.yaml when the default profile's config is
    missing — the merged result carries the operator's settings (port,
    identity, provider), not bare defaults.
    """
    cfg = load_raw_config(profile)
    merged = deep_merge(DEFAULTS, cfg)
    # THE LEGACY ALIASES (the Operator's 08-15 schema restructure): the
    # config FILE now uses Category > Section > Setting (budget, context,
    # models — max 3 layers). Existing readers still look up the OLD
    # paths (iteration_budget, message_loop, compression, retrieval,
    # provider.selection). Expose BOTH so no reader breaks.
    _add_legacy_aliases(merged)
    return merged


def _add_legacy_aliases(cfg: dict) -> None:
    """Mirror the new category sections onto the legacy reader paths."""
    budget = cfg.get("budget") or {}
    if budget:
        cfg.setdefault("iteration_budget", budget.get("iteration") or {})
        cfg.setdefault("message_loop", budget.get("message_loop") or {})
    context = cfg.get("context") or {}
    if context:
        cfg.setdefault("compression", context.get("compression") or {})
        cfg.setdefault("retrieval", context.get("retrieval") or {})
    models = cfg.get("models")
    # THE LEGACY ALIAS (the 08-15 fix): mirror models → provider.selection
    # for OLD readers. MODELS IS THE SOURCE OF TRUTH — the alias ALWAYS
    # mirrors it, unconditionally. A legacy-format file that still carries
    # provider.selection with REAL values has those same values in models
    # (the deep_merge put them there); mirroring models keeps them. A
    # stale/empty provider.selection shell never blocks the refresh.
    if models is not None:
        cfg.setdefault("provider", {})
        cfg["provider"]["selection"] = models


def save_config(cfg: dict, profile: str = "") -> bool:
    """Write a config.yaml back (the settings pages use this for their
    editable values — theme, provider, model, profile).

    profile="" (or "default"/".default") → profiles/.default/config.yaml;
    a system profile → its own file when present, else the .default's; a
    NAMED profile → that profile's own config.yaml (each agent owns its
    config; only authentication.json + .secret are shared).

    Expects a RAW config dict (what load_raw_config returns) so defaults
    are never baked in. YAML round-trip: comments are lost, the structure
    is exact.
    """
    import yaml
    path = profile_config_path(profile)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, default_flow_style=False,
                           sort_keys=False, allow_unicode=True)
        return True
    except Exception as exc:
        from core.logging import log_event
        log_event(4, f"config save failed: {exc}", source="core",
                  action="save_config")
        return False


def load_raw_config(profile: str = "") -> dict:
    """The config exactly as written in config.yaml (no defaults merged).

    Settings WRITE endpoints use this + save_config so edits never bake
    the code defaults into the file. profile="" reads the default
    profile's own config (profiles/.default/config.yaml); a NAMED
    profile reads that agent's own config.yaml.

    THE PER-PROFILE MODEL (the Operator's 08-12 spec): there is NO root
    config.yaml — each profile owns its own. A missing config returns {}
    (the caller merges DEFAULTS); the boot's ensure_all re-seeds missing
    profile configs from the .default's.
    """
    path = profile_config_path(profile)
    if path.exists():
        try:
            return yaml_load(path)
        except Exception:
            return {}
    return {}


def profile_schema(profile: str = "") -> dict:
    """The FULL config schema for a profile — 1:1 with the platform config.

    the Operator's rule (08-10): every profile's config.yaml carries ALL the
    same settings — nothing missing, never a partial file. The schema is
    seeded from the platform config (profiles/.default/config.yaml) and
    overlaid with the profile's OWN values where it has them.

    profile="" → the platform config itself (the default profile's own).
    a NAMED profile → the full platform schema + that profile's own file
    values merged on top (its selection, its identity, its settings).

    This is what a NEW profile's config.yaml is seeded with, so a new
    agent is born with the complete shape — populated, 1:1.
    """
    platform_cfg = load_raw_config("")
    own = load_raw_config(profile)
    schema = deep_merge(platform_cfg, own) if isinstance(platform_cfg, dict) else (own or {})
    return schema if isinstance(schema, dict) else {}


def active_profile_name(cfg: dict | None = None) -> str:
    """The ACTIVE profile name — the runtime state variable (profile.active).

    Lives IN config.yaml (the Operator's spec: a single variable flag has a
    single home, not a sidecar file). `athena profile switch <name>`
    updates it; current_profile() consults it at boot. "" / default =
    the default profile.
    """
    cfg = cfg if cfg is not None else load_config()
    return str((cfg.get("profile") or {}).get("active") or "").strip()


def set_active_profile(name: str) -> bool:
    """Update profile.active in config.yaml, preserving everything else.

    The same section-rewrite pattern provider selection uses: find the
    `profile:` block and replace its active value.
    """
    import re
    path = profile_config_path("")
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
        # One line: `  active: <name>` inside the profile: block.
        m = re.search(r"(?ms)^profile:\n(\s+)active:.*?(?=\n\S|\Z)",
                      text)
        if m:
            indent = m.group(1)
            line = f"profile:\n{indent}active: {name}"
            text = text[:m.start()] + line + text[m.end():]
        else:
            text = text.rstrip() + f"\n\nprofile:\n  active: {name}\n"
        path.write_text(text, encoding="utf-8")
        return True
    except Exception as exc:
        from core.logging import log_event
        log_event(4, f"profile active write failed: {exc}",
                  source="core", action="set_active_profile")
        return False


def flow_names(cfg: dict | None = None) -> tuple[str, str]:
    """The (agent_name, operator_name) for the CLI flow.

    THE 08-15 SOURCE OF TRUTH: the profile's identity frontmatter —
    ASSISTANT.md → the agent's name (Athena side), USER.md → the
    operator's name (User side). The config.yaml identity section was
    REMOVED (it duplicated the .md files the Profile tab edits).

    THE 08-16 DEFAULTS (the Operator's spec): when the identity files
    have NO name, the agent defaults to "Athena" (not "Assistant") and
    the operator to "User".
    """
    agent, operator = "Athena", "User"
    try:
        from core.identity import agent_identity, user_identity, display_name
        from intelligence.profiles import get_profile, default_profile
        try:
            profile = get_profile("") or default_profile()
            root = None if profile.is_default else profile.root
        except Exception:
            root = None
        agent = display_name(agent_identity(root), "Athena")
        operator = display_name(user_identity(root), "User")
    except Exception:
        pass
    return agent or "Athena", operator or "User"


def yaml_load(path: Path) -> dict:
    """Load a YAML config file. Falls back to JSON for compatibility."""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"yaml parse failed ({exc}), falling back to json", source="config")
        return json_load(path)


def json_load(path: Path) -> dict:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        # THE NULL-SKIP (the 08-14 fix): a null in config means "use the
        # default" — the seed writes nulls for unconfigured values, and
        # they must NOT clobber the code defaults (the seeded
        # compression: {upper_threshold: None} was overriding 0.8 → the
        # doctor's compression invariant failed). Provider credentials
        # are null until configured; operational tunables keep defaults.
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out

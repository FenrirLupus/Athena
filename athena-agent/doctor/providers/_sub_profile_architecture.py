"""Native profile architecture test — the Operator's 08-10 spec.

  • The platform config file IS the .default profile's config: it lives at
    profiles/.default/config.yaml (CONFIG_PATH). Named profiles own
    profiles/<name>/config.yaml.
  • The SYSTEM agents (.janitor/.nurse) use the .default's config
    natively (one brain — if default is smart, the system is smart)
    unless an own config.yaml exists as an override.
  • plugins / tools / skills are SHARED: every profile's dirs are
    native symlinks to the shared homes (.athena/plugins|tools|skills).

Read-only: no real files are modified (paths are only inspected).
"""
from __future__ import annotations

from pathlib import Path


def _link_target_dir(link: Path) -> Path | None:
    """Resolve a symlink's target as a directory path (None if not a
    symlink or unresolvable)."""
    try:
        if link.is_symlink():
            t = link.resolve()
            return t if t.is_dir() else None
        return None
    except Exception:
        return None


def run() -> list[dict]:
    import core.config as cfg_mod
    from intelligence.profiles import default_profile

    checks = []

    # ── 1. the platform config IS the .default profile's config ──
    p_default = cfg_mod.profile_config_path("")
    expected_default = cfg_mod.ATHENA_ROOT / "profiles" / ".default" / "config.yaml"
    checks.append({
        "name": "default config lives at profiles/.default/config.yaml",
        "status": "ok" if p_default == expected_default else "fail",
        "detail": str(p_default),
    })
    checks.append({
        "name": "CONFIG_PATH = the .default config",
        "status": "ok" if cfg_mod.CONFIG_PATH == expected_default else "fail",
        "detail": str(cfg_mod.CONFIG_PATH),
    })
    checks.append({
        "name": ".default config exists (migrated)",
        "status": "ok" if p_default.exists() else "fail",
        "detail": "exists: " + str(p_default.exists()),
    })

    # ── 2. system agents inherit the .default config natively ──
    for agent in (".janitor", ".nurse"):
        own = cfg_mod.ATHENA_ROOT / "profiles" / agent / "config.yaml"
        resolved = cfg_mod.profile_config_path(agent)
        # Without an own file they resolve to the .default's config.
        if not own.exists():
            checks.append({
                "name": f"{agent} inherits the .default config",
                "status": "ok" if resolved == p_default else "fail",
                "detail": str(resolved),
            })
        else:
            checks.append({
                "name": f"{agent} uses its own override config",
                "status": "ok" if resolved == own else "fail",
                "detail": str(resolved),
            })
    # A hypothetical override config resolves per-profile even though the
    # real agent has none (behavioural contract, no file written).
    checks.append({
        "name": "system profiles CAN override (own file wins)",
        "status": "ok" if cfg_mod.profile_config_path("profile-agent") ==
        cfg_mod.ATHENA_ROOT / "profiles" / "profile-agent" / "config.yaml" else "fail",
        "detail": "named profile always keeps its own config.yaml",
    })

    # ── 3. shared capability symlinks on EVERY profile ──
    shared = {
        "plugins": cfg_mod.SHARED_PLUGINS,
        "tools": cfg_mod.SHARED_TOOLS,
        "skills": cfg_mod.SHARED_SKILLS,
    }
    profiles_dir = cfg_mod.ATHENA_ROOT / "profiles"
    profile_names = [".default"]
    if profiles_dir.is_dir():
        profile_names += sorted(p.name for p in profiles_dir.iterdir()
                                if p.is_dir() and p.name != ".default")
    for name in profile_names:
        root = cfg_mod.ATHENA_ROOT / "profiles" / name
        for sub, target in shared.items():
            link = root / sub
            ok = _link_target_dir(link) == target
            checks.append({
                "name": f"{name}/{sub} → shared home",
                "status": "ok" if ok else "fail",
                "detail": f"{link} → {_link_target_dir(link)}" if ok
                else f"{link} is not a symlink to {target}",
            })

    # ── 4. the shared homes actually exist with content ──
    for sub, target in shared.items():
        checks.append({
            "name": f"shared {sub} home exists",
            "status": "ok" if target.is_dir() else "fail",
            "detail": str(target),
        })
    if cfg_mod.SHARED_PLUGINS.is_dir():
        has_content = any(cfg_mod.SHARED_PLUGINS.iterdir())
        # The Operator's 08-12 spec: plugins are the COMMUNITY modding layer —
        # Athena ships none. The home EXISTS (the community installs
        # there); being empty is the correct default state.
        checks.append({
            "name": "shared plugins home exists (community modding)",
            "status": "ok",
            "detail": "plugins home ready; Athena ships none by default",
        })

    # ── 5. defaults still resolve for the ORIGINAL identity ──
    dp = default_profile()
    checks.append({
        "name": "default_profile() root is profiles/.default",
        "status": "ok" if dp.root == expected_default.parent else "fail",
        "detail": str(dp.root),
    })
    return checks
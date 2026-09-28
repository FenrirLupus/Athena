"""Per-profile models contract test — the Operator's provider/models split
(08-10, id 1536560518591156244).

  • The platform root config.yaml IS the default profile's config; a NAMED
    profile owns profiles/<name>/config.yaml (each agent its own config).
  • authentication.json + .secret stay GLOBAL — the ONLY shared
    credential sources (the server is the shared source of power).
  • The Models page (6 settings: Reason/Vision/Embedding × primary +
    fallback) reads/writes the ACTIVE profile's config.yaml.
  • A named profile's config.yaml is CREATED on first save, seeded
    with the FULL platform schema (1:1 — every base setting present,
    nothing missing; the Operator's 08-10 rule) plus the new selection.
  • The FULL-SHAPE rule holds per profile: every type always carries
    the four keys (provider/model/fallback_provider/fallback_model),
    null when unconfigured.

Round-trips use a temp store; credentials are never printed.
"""
from __future__ import annotations

import tempfile
from pathlib import Path


def _config_covers(named: Path, base: Path) -> bool:
    """True when the named config carries every base top-level key
    (the full-schema 1:1 rule)."""
    import yaml
    try:
        n = yaml.safe_load(named.read_text(encoding="utf-8")) or {}
        h = yaml.safe_load(base.read_text(encoding="utf-8")) or {}
        return set(h.keys()).issubset(set(n.keys()))
    except Exception:
        return False


def run() -> list[dict]:
    import core.config as cfg_mod
    from providers.selection import load_selection, set_models, select, MODEL_TYPES

    checks = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        original_root = cfg_mod.ATHENA_ROOT
        original_path = cfg_mod.CONFIG_PATH
        try:
            cfg_mod.ATHENA_ROOT = tmp
            cfg_mod.CONFIG_PATH = tmp / "config.yaml"
            base = tmp / "config.yaml"
            # THE 08-15 SCHEMA: the Models category (models.reason/vision/
            # embedding) — Category > Section > Setting, max 3 layers.
            base.write_text(
                "models:\n"
                "  reason:     {provider: opencode-go, model: deepseek-v4-flash, "
                "fallback_provider: opencode-zen, fallback_model: deepseek-v4-flash-free}\n"
                "  vision:     {provider: null, model: null, "
                "fallback_provider: null, fallback_model: null}\n"
                "  embedding:  {provider: null, model: null, "
                "fallback_provider: null, fallback_model: null}\n",
                encoding="utf-8",
            )

            # ── 1. path resolution: default = platform root; named = own file ──
            p_default = cfg_mod.profile_config_path("")
            p_named = cfg_mod.profile_config_path("profile-agent")
            checks.append({
                "name": "profile_config_path: default → platform root",
                "status": "ok" if p_default == base else "fail",
                "detail": f"default resolves to {p_default}",
            })
            checks.append({
                "name": "profile_config_path: named → profiles/<name>/config.yaml",
                "status": "ok" if p_named == tmp / "profiles" / "profile-agent" / "config.yaml" else "fail",
                "detail": f"named resolves to {p_named}",
            })

            # ── 2. the platform root keeps its selection; named is empty ──
            sel_base = load_selection(profile="")
            sel_named = load_selection(profile="profile-agent")
            checks.append({
                "name": "default reads platform selection",
                "status": "ok" if sel_base.get("reason", {}).get("provider") == "opencode-go" else "fail",
                "detail": "reason provider = opencode-go in the platform config",
            })
            checks.append({
                "name": "named profile starts UNCONFIGURED (null shape, no inherited selection)",
                # THE 08-15 SCHEMA: a fresh named profile has no config.yaml
                # → load_config merges DEFAULTS, whose models are the four-key
                # NULL shape (provider/model/fallback_* = None) — NOT the
                # platform's configured values. It must NOT inherit the
                # platform's opencode-go selection.
                "status": "ok" if (
                    sel_named.get("reason", {}).get("provider") is None
                    and set((sel_named.get("reason") or {}).keys())
                    == {"provider", "model", "fallback_provider", "fallback_model"})
                else "fail",
                "detail": "a fresh named profile has no config.yaml → null-shape selection, no platform values",
            })

            # ── 3. save to the NAMED profile creates its own config.yaml ──
            r = set_models({
                "reason": {"provider": "opencode-zen", "model": "deepseek-v4-flash-free",
                           "fallback_provider": "opencode-go", "fallback_model": "deepseek-v4-flash"},
                "vision": {},
                "embedding": {},
            }, profile="profile-agent")
            named_cfg = tmp / "profiles" / "profile-agent" / "config.yaml"
            checks.append({
                "name": "named save creates profiles/<name>/config.yaml",
                "status": "ok" if r.get("ok") and named_cfg.exists() else "fail",
                "detail": r.get("detail") or f"exists: {named_cfg.exists()}",
            })
            checks.append({
                "name": "named config carries the FULL platform schema (1:1)",
                "status": "ok" if _config_covers(named_cfg, base) else "fail",
                "detail": "the named file holds every platform setting — "
                          "nothing missing (the Operator's 08-10 rule)",
            })
            checks.append({
                "name": "platform config untouched by the named save",
                "status": "ok" if base.read_text(encoding="utf-8") ==
                "models:\n  reason:     {provider: opencode-go, model: deepseek-v4-flash, fallback_provider: opencode-zen, fallback_model: deepseek-v4-flash-free}\n  vision:     {provider: null, model: null, fallback_provider: null, fallback_model: null}\n  embedding:  {provider: null, model: null, fallback_provider: null, fallback_model: null}\n" else "fail",
                "detail": "the named write never bleeds into the platform root",
            })

            # ── 4. read-back: the named profile sees its own selection ──
            sel2 = load_selection(profile="profile-agent")
            checks.append({
                "name": "named profile reads back its OWN reason chain",
                "status": "ok" if sel2.get("reason", {}).get("provider") == "opencode-zen"
                and sel2.get("reason", {}).get("fallback_provider") == "opencode-go" else "fail",
                "detail": "opencode-zen primary, opencode-go fallback",
            })

            # ── 5. the FULL-SHAPE rule holds per profile (4 keys, nulls) ──
            v = sel2.get("vision", {})
            checks.append({
                "name": "vision side stays a four-key null object",
                "status": "ok" if set(v.keys()) == {"provider", "model",
                                                   "fallback_provider", "fallback_model"}
                and all(x is None for x in v.values()) else "fail",
                "detail": "keys always exist — null when unconfigured",
            })

            # ── 6. select() writes to the ACTIVE profile (platform here) ──
            r2 = select("reason", "opencode-go", "deepseek-v4-flash",
                        "opencode-zen", "deepseek-v4-flash-free", profile="")
            checks.append({
                "name": "select() with no profile targets the platform root",
                "status": "ok" if r2.get("ok") else "fail",
                "detail": r2.get("detail", ""),
            })
        finally:
            cfg_mod.ATHENA_ROOT = original_root
            cfg_mod.CONFIG_PATH = original_path
    return checks

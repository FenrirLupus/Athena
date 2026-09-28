"""Profile config-schema test — the Operator's 08-10 rule (id 1536595172203302972):

  • EVERY profile's config.yaml carries the FULL platform schema — all the
    same settings, populated, nothing missing, 1:1.
  • A named profile's config is seeded from the platform config at creation
    and on first save (never a partial file).
  • .default's config lives at profiles/.default/config.yaml (CONFIG_PATH).
  • System profiles (.janitor/.nurse) inherit it natively — no own file
    unless an override exists (the fallback chain).
"""
from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> list[dict]:
    import core.config as cfg_mod
    from intelligence.profiles import list_profiles

    checks = []

    # ── 1. real layout: every profile's file covers the schema ──
    base_cfg = cfg_mod.profile_config_path("")
    if base_cfg.exists():
        import yaml
        h = yaml.safe_load(base_cfg.read_text(encoding="utf-8")) or {}
        missing = []
        for p in list_profiles():
            path = cfg_mod.profile_config_path(p.name)
            if not path.exists():
                continue  # system agents (fallback chain) — no own file
            own = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            miss = sorted(set(h.keys()) - set(own.keys()))
            if miss:
                missing.append(f"{p.name}:{','.join(miss)}")
        checks.append({
            "name": "every profile config covers the full platform schema",
            "status": "ok" if not missing else "fail",
            "detail": "missing top-level keys: " + ("; ".join(missing))
                      if missing else "all named profiles 1:1 with the platform",
        })
    else:
        checks.append({
            "name": "every profile config covers the full platform schema",
            "status": "fail",
            "detail": f"platform config missing: {base_cfg}",
        })

    # ── 2. CREATE seeds the full schema (temp env, no real writes) ──
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        original_root = cfg_mod.ATHENA_ROOT
        original_path = cfg_mod.CONFIG_PATH
        import intelligence.profiles as iprof
        original_profiles_dir = iprof.PROFILES_DIR
        try:
            cfg_mod.ATHENA_ROOT = tmp
            cfg_mod.CONFIG_PATH = tmp / "profiles" / ".default" / "config.yaml"
            iprof.PROFILES_DIR = tmp / "profiles"
            base_cfg = cfg_mod.CONFIG_PATH
            base_cfg.parent.mkdir(parents=True, exist_ok=True)
            # A realistic multi-section platform config — THE 08-15 SCHEMA:
            # the Models category (models.reason/vision/embedding).
            base_cfg.write_text(
                "identity:\n  agent_name: Athena\n"
                "server:\n  tick_interval_s: 60\n  host: 127.0.0.1\n  port: 51420\n"
                "thinking_budget:\n  max_calls_per_hour: 10\n"
                "context:\n  retrieval:\n    enabled: true\n    embedding_model: null\n"
                "db:\n  dir: sessions\n  vault_dir: vault\n"
                "models:\n"
                "  reason:     {provider: opencode-go, model: deepseek-v4-flash, "
                "fallback_provider: opencode-zen, fallback_model: deepseek-v4-flash-free}\n"
                "  vision:     {provider: null, model: null, "
                "fallback_provider: null, fallback_model: null}\n"
                "  embedding:  {provider: null, model: null, "
                "fallback_provider: null, fallback_model: null}\n"
                "theme:\n  mode: dark\n",
                encoding="utf-8",
            )
            from intelligence.profiles import create_profile
            prof = create_profile("test-agent")
            path = cfg_mod.profile_config_path("test-agent")
            import yaml
            own = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            base_keys = set((yaml.safe_load(base_cfg.read_text(encoding="utf-8")) or {}).keys())
            miss = sorted(base_keys - set(own.keys()))
            checks.append({
                "name": "create_profile seeds config.yaml with the full schema",
                "status": "ok" if path.exists() and not miss else "fail",
                "detail": f"missing: {miss}" if miss
                          else f"born complete at {path.name}",
            })
            checks.append({
                "name": "fresh profile keeps the platform reason chain",
                "status": "ok" if (own.get("models", {})
                                   .get("reason", {}).get("provider") == "opencode-go")
                else "fail",
                "detail": "a new agent inherits the platform selection shape (its own "
                          "config to change later)",
            })
        finally:
            cfg_mod.ATHENA_ROOT = original_root
            cfg_mod.CONFIG_PATH = original_path
            iprof.PROFILES_DIR = original_profiles_dir

    return checks
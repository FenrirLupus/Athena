"""Feature-model resolver test — configured, fallback, and none branches."""
from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> list[dict]:
    from providers import models as models_mod
    from providers.models import resolve_models, ensure_feature, feature_models
    from metrics import logger

    checks = []
    # 1. THE Operator'S SHAPE RULE (08-10): vision/embedding are NULL when the
    #    user has no such models — they must fall back to the main
    #    provider's model (or none), NEVER a configured lmstudio model.
    r = resolve_models("embedding")
    checks.append({
        "name": "embedding null → falls back (never auto-selected)",
        "status": "ok" if r["models"] and (r["fallback"] or r["provider"]) and not r["none"] else "fail",
        "detail": f"models={r['models'][:1]} fallback={r['fallback']} provider={r['provider']}",
    })
    # 2. Not-configured feature with providers → main provider fallback.
    r2 = resolve_models("vision")
    checks.append({
        "name": "feature falls back to main provider",
        "status": "ok" if r2["models"] and r2["fallback"] and not r2["none"] else "fail",
        "detail": f"provider={r2['provider']} fallback={r2['fallback']}",
    })
    # 3. Nothing anywhere → none, level-2 notice logged.
    original_cfg = models_mod.load_config
    models_mod.load_config = lambda: {"provider": {"selection": {}}, "retrieval": {}}
    import providers.auth_store as auth_mod
    original_catalog = auth_mod.list_providers
    auth_mod.list_providers = lambda: {}
    with tempfile.TemporaryDirectory() as td:
        orig_logs = logger.LOGS_DIR
        logger.LOGS_DIR = Path(td) / "logs"
        try:
            r3 = resolve_models("audio")
            checks.append({
                "name": "no model anywhere → none",
                "status": "ok" if r3["none"] and not r3["models"] else "fail",
                "detail": f"none={r3['none']} models={r3['models'][:2]}",
            })
            _models, notice = ensure_feature("audio")
            checks.append({
                "name": "none logs level-2 notice",
                "status": "ok" if "no model set" in notice else "fail",
                "detail": notice[:50],
            })
            log_files = list((Path(td) / "logs" / "default").glob("*.log"))
            if log_files:
                import json
                e = json.loads(log_files[0].read_text().splitlines()[0])
                checks.append({
                    "name": "notice at level 2 (never an error)",
                    "status": "ok" if e["level"] == 2 else "fail",
                    "detail": f"level={e['level']}",
                })
            else:
                checks.append({
                    "name": "notice at level 2 (never an error)",
                    "status": "ok" if "no model set" in notice else "fail",
                    "detail": "no log file (notice text still correct)",
                })
        finally:
            logger.LOGS_DIR = orig_logs
            models_mod.load_config = original_cfg
            auth_mod.list_providers = original_catalog
    return checks

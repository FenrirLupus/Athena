"""Switch test — provider/model switching via the SELECTION (config.yaml)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from core.config import ATHENA_ROOT


def run() -> list[dict]:
    from providers import switch, setup
    from providers.selection import (selection_for, validate, MODEL_TYPES)
    from providers.switch import switch_provider, switch_model
    from providers.provider import Provider

    checks = []
    configured = setup.list_configured()
    if not configured:
        return [{"name": "switch test (no providers)",
                 "status": "ok", "detail": "no providers to test"}]

    # Isolate config writes: redirect CONFIG_PATH to a temp copy so the
    # test never edits the real config.yaml.
    import core.config as cfg_mod
    original_path = cfg_mod.CONFIG_PATH
    with tempfile.TemporaryDirectory() as td:
        tmp_cfg = Path(td) / "config.yaml"
        tmp_cfg.write_text((ATHENA_ROOT / 'profiles' / '.default' / 'config.yaml').read_text())
        cfg_mod.CONFIG_PATH = tmp_cfg
        try:
            # Selection is the source of truth: each model type resolves.
            for t in MODEL_TYPES:
                s = selection_for(t)
                checks.append({
                    "name": f"{t} selection resolves",
                    "status": "ok" if s["source"] in ("selection", "fallback") else "fail",
                    "detail": f"{s['provider']}/{s['model']} ({s['source']})",
                })
            # Bad provider refused.
            r = switch_provider("nonexistent")
            checks.append({
                "name": "bad provider refused",
                "status": "ok" if not r["ok"] and r.get("known") else "fail",
                "detail": r.get("detail", "")[:40],
            })
            # Bad model selection refused (validated against the catalog).
            first = sorted(configured.keys())[0]
            entry = configured[first]
            r = switch_model("vision", first, "not-a-real-model")
            checks.append({
                "name": "bad model selection refused",
                "status": "ok" if not r.get("ok") else "fail",
                "detail": r.get("detail", "")[:50],
            })
            # Good selection persists and drives the provider model order.
            models = entry.get("models") or []
            if models:
                target = models[0]
                r2 = switch_model("vision", first, target)
                s2 = selection_for("vision")
                checks.append({
                    "name": "selection persists",
                    "status": "ok" if r2.get("ok") and s2["model"] == target else "fail",
                    "detail": f"{first} vision → {target}",
                })
                p = Provider(first, entry)
                checks.append({
                    "name": "selected model leads order",
                    "status": "ok" if p.models and p.models[0] == target else "fail",
                    "detail": f"order={p.models[:2]}",
                })
            # Validate against the catalog.
            checks.append({
                "name": "validate checks catalog",
                "status": "ok" if not validate("nope", "x") else "fail",
                "detail": "",
            })
        finally:
            cfg_mod.CONFIG_PATH = original_path
    return checks

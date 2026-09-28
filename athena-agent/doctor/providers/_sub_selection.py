"""Selection contract test — credentials-only auth + model ladder."""
from __future__ import annotations

import tempfile
from pathlib import Path
import re
from core.config import ATHENA_ROOT


def run() -> list[dict]:
    from providers import auth_store
    from providers.selection import (selection_for, model_ladder, validate,
                                     load_selection)
    from core.config import load_config

    checks = []
    # 1. authentication.json stores credentials only — no 'model' key.
    auth = auth_store._load_raw()
    providers = auth.get("providers", {})
    no_model_key = all("model" not in (e or {}) for e in providers.values())
    checks.append({
        "name": "auth stores credentials only",
        "status": "ok" if providers and no_model_key else "fail",
        "detail": "no 'model' key in any provider entry",
    })
    # 2. Each entry has base_url + api_key + discovered models.
    all_ok = all(
        e and e.get("base_url") and e.get("models")
        for e in providers.values()
    )
    checks.append({
        "name": "auth has base_url + models",
        "status": "ok" if providers and all_ok else "fail",
        "detail": "base_url + discovered models present",
    })
    # 3. EVERY model type supports a fallback (the Operator's contract).
    #    Seeded against a temp config so null defaults don't mask it.
    import core.config as cfg_mod
    from providers.selection import _write_selection, select
    original_path = cfg_mod.CONFIG_PATH
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "config.yaml"
        tmp.write_text((ATHENA_ROOT / 'profiles' / '.default' / 'config.yaml').read_text())
        cfg_mod.CONFIG_PATH = tmp
        try:
            # Seed all three types with primary + fallback — the REAL
            # catalog entries (opencode-go / opencode-zen). Never seed
            # lmstudio: it is not configured and must not be referenced.
            select("reason", "opencode-go", "deepseek-v4-flash",
                   fallback_provider="opencode-zen",
                   fallback_model="deepseek-v4-flash-free")
            select("vision", "opencode-go", "deepseek-v4-pro",
                   fallback_provider="opencode-zen",
                   fallback_model="deepseek-v4-flash-free")
            select("embedding", "opencode-go", "glm-5",
                   fallback_provider="opencode-zen",
                   fallback_model="deepseek-v4-flash-free")
            all_fallback = True
            fb_detail = []
            for t in ("reason", "vision", "embedding"):
                s = selection_for(t)
                has = bool(s.get("fallback_provider") and s.get("fallback_model"))
                if not has:
                    all_fallback = False
                fb_detail.append(f"{t}:{s.get('fallback_provider')}/{s.get('fallback_model')}")
            checks.append({
                "name": "all types have fallback",
                "status": "ok" if all_fallback else "fail",
                "detail": "; ".join(fb_detail),
            })
            # 4. The ladder: primary → fallback → 2 extra from primary provider.
            r = selection_for("reason")
            ladder = model_ladder("reason")
            ok_ladder = (
                len(ladder) == 4
                and ladder[0]["provider"] == r["provider"]
                and ladder[1]["provider"] == r["fallback_provider"]
                and ladder[2]["provider"] == r["provider"]
            )
            checks.append({
                "name": "model ladder primary→fallback→extra",
                "status": "ok" if ok_ladder else "fail",
                "detail": str([(s["provider"], s["model"]) for s in ladder]),
            })
            # 5. Validate uses discovered models only.
            checks.append({
                "name": "validate discovered models",
                "status": "ok" if not validate("opencode-go", "not-a-model") else "fail",
                "detail": "",
            })
            # Switching the PRIMARY preserves the existing FALLBACK.
            r2 = select("vision", "opencode-go", "gpt-5.6-luna")  # no fallback given
            checks.append({
                "name": "fallback preserved on primary switch",
                "status": "ok" if r2.get("fallback_provider") == "opencode-zen"
                and r2.get("fallback_model") == "deepseek-v4-flash-free" else "fail",
                "detail": f"fb={r2.get('fallback_provider')}/{r2.get('fallback_model')}",
            })
            # THE 08-15 FIX: find the vision line INSIDE the models block —
            # the config may have OTHER bare `vision:` keys (a YAML anchor
            # section, e.g. models: *id001) whose empty lines would match
            # a naive startswith("vision:") scan.
            text = tmp.read_text()
            m_models = re.search(r"(?ms)^models:.*?(?=^\S|\Z)", text)
            models_block = m_models.group(0) if m_models else ""
            line = next(l for l in models_block.splitlines()
                        if l.strip().startswith("vision:"))
            checks.append({
                "name": "selection one line per type",
                "status": "ok" if line.count("{") == 1
                and "fallback_provider" in line else "fail",
                "detail": line.strip()[:80],
            })
            s = load_selection()
            s["vision"] = {}
            _write_selection(s)
            text2 = tmp.read_text()
            m_models2 = re.search(r"(?ms)^models:.*?(?=^\S|\Z)", text2)
            models_block2 = m_models2.group(0) if m_models2 else ""
            line2 = next(l for l in models_block2.splitlines()
                         if l.strip().startswith("vision:"))
            checks.append({
                # THE Operator'S SHAPE RULE: unset types keep the FULL shape —
                # all four keys present with null values, never a bare
                # `null` and never a missing key.
                "name": "unset type keeps full null shape",
                "status": "ok" if all(k in line2 for k in (
                    "provider: null", "model: null",
                    "fallback_provider: null", "fallback_model: null")) else "fail",
                "detail": line2.strip(),
            })
        finally:
            cfg_mod.CONFIG_PATH = original_path
    return checks

"""Providers surface test — consolidated (the Operator's 08-12 directive).

ONE module per category: this composer runs every providers submodule's
checks and merges them into a single report. Check names are preserved
1:1 — the doctor count and the nurse's failure tracking stay stable
across consolidation.
"""
from __future__ import annotations

from pathlib import Path

def _chk_provider() -> list[dict]:
    from providers.provider import ProviderChain
    from providers import setup
    from providers.provider_catalog import list_catalog, get_catalog_entry

    checks = []

    # -- Chain (was 20_provider.py) -----------------------------------
    chain = ProviderChain()
    ready = chain.ready_provider()
    checks.append({
        "name": "ready provider resolves",
        "status": "ok" if ready else "fail",
        "detail": ready.name if ready else "none ready",
    })

    # -- Setup (was 20_setup.py) --------------------------------------
    configured = setup.list_configured()
    checks.append({
        "name": "list_configured returns dict",
        "status": "ok" if isinstance(configured, dict) else "fail",
        "detail": f"type={type(configured).__name__}",
    })
    checks.append({
        "name": "add_provider signature exists",
        "status": "ok" if callable(getattr(setup, "add_provider", None)) else "fail",
        "detail": "",
    })

    # -- Catalog (was 20_catalog.py) ----------------------------------
    catalog = list_catalog()
    checks.append({
        "name": "catalog non-empty",
        "status": "ok" if catalog else "fail",
        "detail": f"{len(catalog)} providers",
    })
    checks.append({
        "name": "lmstudio present",
        "status": "ok" if "lmstudio" in catalog else "fail",
        "detail": f"keys={sorted(catalog)[:5]}",
    })
    entry = get_catalog_entry("lmstudio")
    checks.append({
        "name": "entry has base_url (local providers may omit model)",
        "status": "ok" if entry and entry.get("base_url") else "fail",
        "detail": f"local={entry.get('local')} model={entry.get('model', '(unset)')}",
    })
    return checks


_SUBMODULES = [
    "auth_store",
    "feature_models",
    "formats",
    "profile_architecture",
    "profile_models",
    "provider",
    "provider_gui",
    "selection",
    "switch",
]


def _load_sub(name: str):
    """Import a submodule by its _sub_* file name (namespace package)."""
    import importlib.util
    here = Path(__file__).parent
    path = here / f"_sub_{name}.py"
    spec = importlib.util.spec_from_file_location(
        f"doctor.providers._sub_{name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod



def run() -> list[dict]:
    checks: list[dict] = []
    for name in _SUBMODULES:
        # Inline (folded) checks run directly; file-backed ones import.
        inline = globals().get(f"_chk_{name}")
        if inline is not None:
            try:
                checks.extend(inline())
            except Exception as exc:
                checks.append({
                    "name": f"providers/{name}",
                    "status": "fail",
                    "detail": f"{type(exc).__name__}: {exc}",
                })
            continue
        try:
            mod = _load_sub(name)
            if callable(getattr(mod, "run", None)):
                checks.extend(mod.run())
        except Exception as exc:
            checks.append({
                "name": f"providers/{name}",
                "status": "fail",
                "detail": f"{type(exc).__name__}: {exc}",
            })
    return checks

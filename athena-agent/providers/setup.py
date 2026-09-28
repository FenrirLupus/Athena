"""Provider setup — the shared logic for CLI setup and the GUI settings page.

Adds a provider to authentication.json (entry + optional chain position)
and probes its models. CLI and GUI both call these functions so the
behavior is identical no matter which door is used.
"""
from __future__ import annotations

from . import auth_store
from core.config import load_config
from .provider_catalog import get_catalog_entry, suggested_model


def add_provider(name: str, api_key: str = "", *, to_chain: bool = True,
                 model: str = "", base_url: str = "") -> dict:
    """Register a provider. Returns the entry written to the store.

    - Uses the catalog defaults when the provider is known.
    - Writes ONLY the credentials (api_key, base_url) into authentication.json
      (the `model` arg is accepted for compatibility but NOT stored — the
      active model choice lives in config.yaml's provider.selection).
    - Probes /models and auto-populates the model list on success.
    - Optionally appends the provider name to config.yaml's provider.chain.
    """
    name = (name or "").strip().lower()
    if not name:
        return {"success": False, "error": "provider name required"}

    catalog = get_catalog_entry(name)
    entry = auth_store.get_provider(name) or {}

    entry.setdefault("base_url", (base_url or (catalog or {}).get("base_url", "")).rstrip("/"))
    # THE .SECRET (the Operator's spec): the api_key lives ONLY in the .secret
    # store (PROVIDER_API_KEY), never in authentication.json. The config
    # entry never carries it.
    if api_key:
        from core.secret_store import set_api_key
        set_api_key(name, api_key.strip())
    # No model stored here: authentication.json is CONFIG only. The
    # model list is probed below; the ACTIVE model choice lives in
    # config.yaml's provider.selection.
    auth_store.save_provider(name, entry)

    # Probe models (best-effort — never fails the add).
    discovered = auth_store.probe_models(
        entry["base_url"], auth_store.get_api_key(name), timeout=12
    )
    if discovered:
        entry["models"] = discovered
        auth_store.save_provider(name, entry)

    if to_chain:
        _append_to_chain(name)

    return {
        "success": True,
        "provider": name,
        "entry": {k: v for k, v in entry.items() if k != "api_key"},
        "models_discovered": len(discovered),
    }


def _append_to_chain(name: str) -> None:
    """Legacy no-op: the chain was removed (the Operator's Option B).

    The catalog order (authentication.json) is the default provider order
    now; the per-type selection drives routing. Kept as a no-op so older
    callers (add_provider to_chain=True) don't break.
    """
    from core.logging import log_event
    log_event(2, f"provider {name} registered in the catalog", source="providers",
              action="add_provider")


def list_configured() -> dict:
    """Current providers in the auth store (secrets redacted)."""
    providers = auth_store.list_providers()
    return {
        name: {k: v for k, v in entry.items() if k != "api_key"}
        for name, entry in providers.items()
    }

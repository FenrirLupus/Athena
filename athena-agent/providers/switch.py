"""Switch — safely change the active provider / model at runtime.

The ONE source of truth is the PROVIDER SELECTION in config.yaml
(provider.selection), which references the catalog in authentication.json.

    provider switch <name>       → set the REASON selection to <name>'s model
    provider model list|switch   → view / set a model type's selection
    model switch <name>          → set the REASON model on its provider

Safety: every switch validates against the catalog before writing; a bad
selection never touches config.
"""
from __future__ import annotations

from core.config import active_profile_name, load_config

from providers import auth_store
from providers import setup as setup_mod
from providers.selection import select, selection_for, load_selection, MODEL_TYPES


def _active_profile_name() -> str:
    """The profile the current call targets: profile.active in the platform
    config when set, else \"\" (the default profile / platform root)."""
    try:
        return active_profile_name()
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"active-profile lookup failed: {exc}", source="providers",
                  action="active_profile")
        return ""


def _secret_provider_names() -> list[str]:
    """The configured providers, driven by .secret (the Operator's 08-10
    rule): a provider is configured only when its credential EXISTS in
    .secret. Env-style keys (OPENCODE_GO_API_KEY) map back to names."""
    from core import secret_store
    out = []
    for k in secret_store.keys():
        if k.endswith("_API_KEY"):
            out.append(k[: -len("_API_KEY")].lower().replace("_", "-"))
    return sorted(out)


def list_providers() -> dict:
    """The provider landscape, driven by .secret: the CONFIGURED set is
    the providers with a REAL key in .secret (never entries that only
    sit in authentication.json). auth_store enriches base_url + models;
    the key itself never leaves the store.

    The 'primary' flag follows the ACTIVE profile's reason selection
    (each profile owns its config.yaml; which provider is ACTIVE is
    per-agent — the Operator's provider/models split)."""
    cfg = load_config(profile=_active_profile_name())
    auth = auth_store.list_providers()
    sel = load_selection(cfg)
    reason = selection_for("reason", cfg)
    out = {"providers": [], "selection": {t: sel.get(t) for t in MODEL_TYPES}}
    for name in _secret_provider_names():
        entry = auth.get(name, {}) or {}
        models = entry.get("models") or []
        out["providers"].append({
            "name": name,
            "primary": name == reason.get("provider"),
            "model": models[0] if models else "",
            "models": models,
            "base_url": entry.get("base_url", ""),
            "has_key": True,  # every listed provider HAS a key by definition
        })
    return out


def switch_provider(name: str) -> dict:
    """Make <name> the REASON provider: set the selection + reorder chain.

    The provider must be configured with models. The chain is reordered
    (switched provider first) as the fallback; the SELECTION records the
    active choice — the true source of truth.
    """
    configured = setup_mod.list_configured()
    if name not in configured:
        return {"ok": False, "detail": f"provider not configured: {name}",
                "known": sorted(configured.keys())}
    entry = configured[name]
    if not entry.get("models"):
        return {"ok": False, "detail": f"provider has no models: {name}"}

    # The reason selection: the provider's first discovered model. The
    # selection is the source of truth (no chain to reorder).
    models = entry.get("models") or []
    model = models[0]
    r = select("reason", name, model)
    if not r.get("ok"):
        return r
    return {"ok": True, "detail": f"reason → {name}/{model}"}


def switch_model(model_type: str, provider: str, model: str) -> dict:
    """Set the selection for a model type (reason|vision|embedding)."""
    return select(model_type, provider, model)


def switch_reason_model(model: str) -> dict:
    """Set the REASON model on its currently selected provider."""
    cur = selection_for("reason")
    if cur["source"] == "none":
        return {"ok": False, "detail": "no provider selected for reason"}
    r = select("reason", cur["provider"], model)
    if r.get("ok"):
        return {"ok": True, "detail": f"reason → {cur['provider']}/{model}",
                "provider": cur["provider"], "model": model}
    return r


def active_model_for(provider: str) -> str | None:
    """The effective active model for a provider (from the selection)."""
    for t in MODEL_TYPES:
        s = selection_for(t)
        if s["provider"] == provider and s["source"] == "selection":
            return s["model"]
    configured = setup_mod.list_configured()
    entry = configured.get(provider) or {}
    models = entry.get("models") or []
    return models[0] if models else None

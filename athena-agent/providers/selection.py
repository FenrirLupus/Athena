"""Selection — the ONE source of truth for which provider/model is active.

The Operator's design — the selection lives in the PROVIDER SECTION of
config.yaml, and authentication.json stays the CATALOG:

    config.yaml
        provider:
            chain: [...]            # fallback order (used when no selection)
            selection:
                reason:     {provider, model}   ← ACTIVE choice per type
                vision:     {provider, model}
                embedding:  {provider, model}

    authentication.json
        providers: {name: {base_url, api_key, models}}   ← the catalog

Every model type has exactly one selection (provider + model). The
ProviderChain and feature resolvers read THIS first; the chain order is
the fallback when a selection is missing.
"""
from __future__ import annotations

from core.config import load_config, CONFIG_PATH

# The model types with a selection.
MODEL_TYPES = ("reason", "vision", "embedding")


def _target_profile(profile: str = "") -> str:
    """The profile whose config the selection reads/writes: an explicit
    profile name, else the ACTIVE profile (profile.active in the platform
    config when set, else \"\" = the default profile's platform config)."""
    if profile:
        return profile
    try:
        from core.config import active_profile_name
        return active_profile_name()
    except Exception:
        return ""


def load_selection(cfg: dict | None = None, profile: str = "") -> dict:
    """The current selection block: {type: {provider, model}}.

    profile=\"\" → the default profile's config (platform root values);
    a NAMED profile → that agent's OWN config.yaml selection.
    An explicit cfg wins over any file read.
    """
    if cfg is None:
        from core.config import load_config
        cfg = load_config(profile=_target_profile(profile))
    sel = cfg.get("provider", {}).get("selection", {}) or {}
    return {k: v for k, v in sel.items() if isinstance(v, dict)}


def _catalog() -> dict:
    """The provider catalog from authentication.json (never the selection)."""
    from providers import setup
    try:
        return setup.list_configured()
    except Exception:
        return {}


def _write_selection(selection: dict, profile: str = "") -> bool:
    """Rewrite provider.selection in a profile's config.yaml, preserving
    everything else.

    profile="" → the platform root config.yaml (the default profile's);
    a NAMED profile → that agent's own config.yaml (the Operator's split:
    each profile owns its config; credentials stay GLOBAL in
    authentication.json + .secret).

    A named profile's config.yaml is CREATED on first save — seeded
    with the FULL platform schema (the Operator's 08-10 rule: every profile's
    config.yaml carries all the same settings, populated, nothing
    missing, 1:1) plus this selection.
    """
    import core.config as cfg_mod
    path = cfg_mod.profile_config_path(profile)
    if not path.exists():
        # First save for a named profile: build the FULL schema (the
        # Operator's 1:1 rule — the platform config's complete shape, nothing
        # missing) and overlay this selection on it.
        schema = cfg_mod.profile_schema(profile=profile) or {}
        # THE 08-15 SCHEMA: the Models category (models.reason/vision/
        # embedding) — the old provider.selection wrapper was dropped.
        schema.setdefault("models", {}).update(selection)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return cfg_mod.save_config(schema, profile=profile)
        except Exception as exc:
            from core.logging import log_event
            log_event(4, f"selection write failed: {exc}", source="providers",
                      action="write_selection")
            return False
    try:
        text = path.read_text(encoding="utf-8")
        import re
        # ONE LINE per model type (the Operator's format): a compact flow map,
        # or null when nothing is selected. THE 08-15 SCHEMA: the Models
        # category is `models:` (Category > Section > Setting — the old
        # provider.selection wrapper was dropped).
        lines = ["models:"]
        for t in ("reason", "vision", "embedding"):
            entry = selection.get(t) or {}
            if not entry.get("provider") or not entry.get("model"):
                # THE Operator'S SHAPE RULE (08-10): the four keys ALWAYS
                # exist — provider, model, fallback_provider,
                # fallback_model — with null values when unconfigured.
                # Nothing missing, nothing auto-selected.
                lines.append(
                    "  " + t + ":     {provider: null, model: null, "
                    "fallback_provider: null, fallback_model: null}"
                )
                continue
            parts = [f"provider: {entry.get('provider')}",
                     f"model: {entry.get('model')}"]
            if entry.get("fallback_provider") and entry.get("fallback_model"):
                parts.append(f"fallback_provider: {entry.get('fallback_provider')}")
                parts.append(f"fallback_model: {entry.get('fallback_model')}")
            lines.append(f"  {t}:     {{" + ", ".join(parts) + "}")
        block = "\n".join(lines)
        # Replace from the existing "models:" line to the end of its block
        # (the next top-level key, or EOF).
        m = re.search(r"(?ms)^models:.*?(?=^\S|\Z)", text)
        if not m:
            return False
        text = text[:m.start()] + block + "\n" + text[m.end():]
        path.write_text(text, encoding="utf-8")
        return True
    except Exception as exc:
        from core.logging import log_event
        log_event(4, f"selection write failed: {exc}", source="providers",
                  action="write_selection")
        return False


def validate(provider: str, model: str) -> bool:
    """Is {provider, model} a real entry in the catalog?"""
    catalog = _catalog()
    entry = catalog.get(provider)
    if not entry:
        return False
    models = entry.get("models") or []
    return model in models


def select(model_type: str, provider: str, model: str,
           fallback_provider: str = "", fallback_model: str = "",
           profile: str = "") -> dict:
    """Set the selection for a model type. Validated against the catalog.

    fallback_provider/fallback_model: the SECOND choice — tried first when
    the primary fails (the Operator's fallback contract).

    PRESERVATION: when a new primary is chosen but no fallback is given,
    the EXISTING fallback is kept (switching the primary must not wipe
    the configured fallback).

    profile="" → the ACTIVE profile's config (default: platform root);
    a NAMED profile → that agent's own config.yaml.
    """
    if model_type not in MODEL_TYPES:
        return {"ok": False, "detail": f"model type must be one of {MODEL_TYPES}"}
    if not validate(provider, model):
        return {"ok": False, "detail": f"{provider}/{model} not in the catalog"}
    if fallback_provider and not validate(fallback_provider, fallback_model):
        return {"ok": False, "detail": f"fallback {fallback_provider}/{fallback_model} not in the catalog"}
    sel = load_selection(profile=profile)
    entry = {"provider": provider, "model": model}
    if fallback_provider and fallback_model:
        entry["fallback_provider"] = fallback_provider
        entry["fallback_model"] = fallback_model
    else:
        # Preserve the existing fallback for this type.
        existing = sel.get(model_type) or {}
        if existing.get("fallback_provider") and existing.get("fallback_model"):
            entry["fallback_provider"] = existing["fallback_provider"]
            entry["fallback_model"] = existing["fallback_model"]
    sel[model_type] = entry
    if not _write_selection(sel, profile):
        return {"ok": False, "detail": "could not write config.yaml"}
    return {"ok": True, "model_type": model_type, "provider": provider,
            "model": model, "fallback_provider": entry.get("fallback_provider", ""),
            "fallback_model": entry.get("fallback_model", "")}


def set_models(entries: dict, profile: str = "") -> dict:
    """Set ALL six model settings at once (the Models tab save).

    entries = {
        "reason":    {"provider": ..., "model": ..., "fallback_provider": ..., "fallback_model": ...},
        "vision":    {...} | None,
        "embedding": {...} | None,
    }

    Each side is validated against the catalog; anything absent stays a
    FOUR-KEY NULL object (the Operator's shape rule: keys always exist, null
    when unconfigured). profile="" → active profile (platform root default);
    a NAMED profile → that agent's own config.yaml.
    """
    if not isinstance(entries, dict):
        return {"ok": False, "detail": "entries must be an object"}
    sel = load_selection(profile=profile)
    for t in MODEL_TYPES:
        e = entries.get(t) or {}
        entry = {
            "provider": (e.get("provider") or "").strip() or None,
            "model": (e.get("model") or "").strip() or None,
            "fallback_provider": (e.get("fallback_provider") or "").strip() or None,
            "fallback_model": (e.get("fallback_model") or "").strip() or None,
        }
        if entry["provider"] and entry["model"]:
            if not validate(entry["provider"], entry["model"]):
                return {"ok": False,
                        "detail": f"{t}: {entry['provider']}/{entry['model']} not in the catalog"}
            if entry["fallback_provider"] and not validate(
                    entry["fallback_provider"], entry["fallback_model"]):
                return {"ok": False,
                        "detail": f"{t} fallback: {entry['fallback_provider']}/{entry['fallback_model']} not in the catalog"}
        sel[t] = entry
    if not _write_selection(sel, profile):
        return {"ok": False, "detail": "could not write config.yaml"}
    return {"ok": True, "selection": sel}


def selection_for(model_type: str, cfg: dict | None = None) -> dict:
    """The active selection for a model type.

    Returns {provider, model, fallback_provider, fallback_model, source}:
        'selection' — from provider.selection in config.yaml (the chosen one)
        'fallback'  — the first ready provider in the chain (nothing chosen)
        'none'      — no selection AND no ready provider
    """
    cfg = cfg if cfg is not None else load_config()
    if model_type not in MODEL_TYPES:
        model_type = "reason"
    sel = load_selection(cfg).get(model_type)
    if sel and sel.get("provider") and sel.get("model"):
        return {
            "provider": sel["provider"], "model": sel["model"],
            "fallback_provider": sel.get("fallback_provider", ""),
            "fallback_model": sel.get("fallback_model", ""),
            "source": "selection",
        }
    # Fallback: the first ready provider in the CATALOG order (no chain —
    # authentication.json's key order is the default when nothing chosen).
    try:
        from providers import auth_store
        from providers.provider import ProviderChain
        chain = ProviderChain(cfg)
        for p in chain.providers:
            if p.ready and p.models:
                return {"provider": p.name, "model": p.models[0],
                        "fallback_provider": "", "fallback_model": "",
                        "source": "fallback"}
        # If the chain didn't produce ready providers, walk the catalog.
        catalog = auth_store.list_providers()
        for name in catalog:
            entry = catalog[name]
            models = entry.get("models") or []
            if models:
                return {"provider": name, "model": models[0],
                        "fallback_provider": "", "fallback_model": "",
                        "source": "fallback"}
    except Exception:
        pass
    return {"provider": None, "model": None,
            "fallback_provider": "", "fallback_model": "", "source": "none"}


def model_ladder(model_type: str, cfg: dict | None = None,
                 extra: int = 2) -> list[dict]:
    """The ordered ladder a type tries (the Operator's contract):

        1. the primary selection {provider, model}
        2. the FALLBACK selection {provider, model} (tried first when the
           primary fails — the config's fallback fields)
        3. `extra` more models from the primary provider (default 2)

    Returns [{provider, model}, ...] in try order. Never empty when a
    selection exists.
    """
    s = selection_for(model_type, cfg)
    if s["source"] == "none":
        return []
    ladder = [{"provider": s["provider"], "model": s["model"]}]
    if s.get("fallback_provider") and s.get("fallback_model"):
        ladder.append({"provider": s["fallback_provider"],
                       "model": s["fallback_model"]})
    # Extra models from the primary provider (skip duplicates).
    try:
        from providers import setup
        catalog = setup.list_configured()
        entry = catalog.get(s["provider"], {})
        for mid in (entry.get("models") or []):
            if mid != s["model"] and all(m != mid for m in ladder):
                ladder.append({"provider": s["provider"], "model": mid})
                if len(ladder) >= 2 + extra:
                    break
    except Exception:
        pass
    return ladder


def summary(cfg: dict | None = None) -> dict:
    """The full selection picture for the CLI: each type + its choice."""
    out = {"types": {}}
    for t in MODEL_TYPES:
        out["types"][t] = selection_for(t, cfg)
    return out

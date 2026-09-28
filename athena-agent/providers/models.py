"""Feature models — embedding, vision, thinking, etc.

The Operator's contract for specialized models:

    1. If MULTIPLE feature models are configured → they are all used
       (the chain tries them in order).
    2. If NOTHING is set for the feature → the MAIN provider's model is
       used as the fallback.
    3. If NO model is available at all → the action logs at level 1/2
       (GOOD/NOTICE, never an error): "no model set for this action —
       needs setup to use this feature properly."

The resolver returns what's available; the caller logs the notice when
nothing is. The intent: a missing feature model NEVER fails loudly — it
falls back, and if there's truly nothing, it says so at low severity.
"""
from __future__ import annotations

from core.config import load_config

# The features that can have their own models.
FEATURES = ("embedding", "vision", "thinking", "audio")


def feature_models(feature: str, cfg: dict | None = None) -> list[str]:
    """The configured models for a feature (0..N).

    Config shape (retrieval/embedding_model, vision.model, etc.):
        feature: {models: [...]}        → list of explicit models
        feature: {model: "x"}           → single model
        feature: "model-name"           → bare string
    """
    cfg = cfg if cfg is not None else load_config()
    value = _find_feature_cfg(cfg, feature)
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        models = value.get("models") or []
        if isinstance(models, str):
            models = [models]
        single = value.get("model")
        out = list(models) if models else ([single] if single else [])
        return [m for m in out if m]
    if isinstance(value, list):
        return [m for m in value if m]
    return []


def _find_feature_cfg(cfg: dict, feature: str):
    """Locate a feature's config wherever it lives in the tree."""
    key = f"{feature}_model"  # e.g. embedding_model
    direct = cfg.get(feature)
    if isinstance(direct, (str, list, dict)):
        return direct
    for section in cfg.values():
        if isinstance(section, dict) and key in section:
            return section[key]
        if isinstance(section, dict) and feature in section:
            return section[feature]
    # Bare key at root: embedding_model: "..."
    if key in cfg:
        return cfg[key]
    return None


def resolve_models(feature: str, cfg: dict | None = None) -> dict:
    """Resolve the models for a feature, with the fallback contract.

    Returns:
        {
          feature: str,
          models: [..],       # the ordered candidates to try
          provider: str|None, # where the fallback came from
          fallback: bool,     # True when using the main provider's model
          none: bool,         # True when NO model is available
        }
    """
    cfg = cfg if cfg is not None else load_config()
    models = feature_models(feature, cfg)
    if models:
        return {"feature": feature, "models": models, "provider": None,
                "fallback": False, "none": False}

    # Fallback: the main provider chain's first ready model.
    try:
        from providers.provider import ProviderChain
        chain = ProviderChain(cfg)
        for p in chain.providers:
            if p.ready and p.models:
                return {"feature": feature, "models": list(p.models),
                        "provider": p.name, "fallback": True, "none": False}
    except Exception:
        pass

    return {"feature": feature, "models": [], "provider": None,
            "fallback": False, "none": True}


def ensure_feature(feature: str, cfg: dict | None = None,
                   source: str = "providers") -> tuple[list[str], str]:
    """Resolve AND log the feature-model status.

    The Operator's contract: when nothing is set, log at level 2 (NOTICE) —
    "no model set for this action; needs setup" — never a loud error.
    Returns (models, notice) so the caller can still proceed gracefully.
    """
    from core.logging import log_event

    r = resolve_models(feature, cfg)
    if r["none"]:
        log_event(2,
                  f"no model set for {feature} — needs setup to use this feature properly",
                  source=source, action=f"{feature}_model")
        return [], "no model set for this action; needs setup"
    if r["fallback"]:
        log_event(2, f"{feature} uses the main provider's model ({r['provider']})",
                  source=source, action=f"{feature}_model")
        return r["models"], f"fallback to {r['provider']}"
    log_event(1, f"{feature} model ready: {r['models'][0]}", source=source,
              action=f"{feature}_model")
    return r["models"], ""

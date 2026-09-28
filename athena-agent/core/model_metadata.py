"""Model metadata — the per-model registry (the Operator's right-sizing spec).

model metadata adapted: each model knows its context window and
recommended output cap, so Athena can right-size every call instead of
hardcoding one window for everything. The registry has the common models
built in, falls back to the configured compression window, and resolves
the ACTIVE model per profile.
"""
from __future__ import annotations

# name → {context_window, max_output}
# The built-in registry covers the common providers Athena talks to.
MODELS: dict[str, dict] = {
    # opencode-go / DeepSeek family
    "deepseek-v4-flash": {"context_window": 32768, "max_output": 8192},
    "deepseek-chat": {"context_window": 65536, "max_output": 8192},
    "deepseek-reasoner": {"context_window": 65536, "max_output": 8192},
    # Qwen family
    "qwen3.5-4b": {"context_window": 32768, "max_output": 8192},
    "qwen2.5-72b": {"context_window": 131072, "max_output": 8192},
    "qwen3-32b": {"context_window": 131072, "max_output": 8192},
    # LM Studio / local
    "lmstudio-community/qwen3.5-4b": {"context_window": 32768,
                                      "max_output": 8192},
    # OpenAI
    "gpt-4o": {"context_window": 128000, "max_output": 16384},
    "gpt-4o-mini": {"context_window": 128000, "max_output": 16384},
    "gpt-4.1": {"context_window": 1048576, "max_output": 32768},
    # Anthropic
    "claude-3-5-sonnet": {"context_window": 200000, "max_output": 8192},
    "claude-sonnet-4": {"context_window": 200000, "max_output": 8192},
    "claude-haiku-4": {"context_window": 200000, "max_output": 8192},
}

DEFAULT_WINDOW = 32768
DEFAULT_OUTPUT = 5120


def lookup(model: str) -> dict | None:
    """The metadata for a model name (exact or suffix match)."""
    if not model:
        return None
    if model in MODELS:
        return dict(MODELS[model])
    # Suffix match: "deepseek-v4-flash" variants, "gpt-4o-2024-08-06", etc.
    for name, meta in MODELS.items():
        if name in model or model in name:
            return dict(meta)
    return None


def context_window(model: str, default: int = DEFAULT_WINDOW) -> int:
    """The right context window for a model (fallback to default)."""
    meta = lookup(model)
    if meta:
        return int(meta.get("context_window", default))
    # Config fallback lives at the call site; here we return the default.
    return default




def active_model_context(cfg: dict | None = None) -> int:
    """The ACTIVE model's context window — the right size for compression.

    Resolution: the active reason model (config selection) → the metadata
    registry → the configured compression window → the hard default.
    """
    try:
        from core.config import load_config
        c = cfg if cfg is not None else load_config()
        sel = (c.get("provider") or {}).get("selection", {}).get("reason") or {}
        model = sel.get("model") or ""
        win = context_window(model)
        comp = c.get("compression", {})
        configured = int(comp.get("context_window", 0) or 0)
        # If the registry knows the model, ITS window wins (right-sized).
        if lookup(model):
            return win
        # Unknown model → the configured window is the source of truth.
        return configured if configured else win
    except Exception as exc:
        _log(3, f"active model context failed: {exc}",
             source="model_metadata")
        return DEFAULT_WINDOW


def _log(level: int, msg: str, source: str = "model_metadata") -> None:
    """The model-metadata resolver is operational — failures are logged."""
    try:
        from metrics.logger import log
        log(level, msg, source=source)
    except Exception:
        pass

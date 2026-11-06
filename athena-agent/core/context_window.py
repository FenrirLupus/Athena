"""Which context window is in force, and why.

Two facts, deliberately separate: the floor the operator sets, and the figure a provider publishes. **The published
figure has priority** — it is the model's real limit — and it applies only to the model it was published for, which is
why `context_window_provider_model` is stored beside it. The local floor is the fallback for providers that publish
nothing, and a figure below 32,768 is refused rather than adopted.

`source` names which one is in force, so the interface can say "provider" or "config" instead of leaving a bare number.
"""
from __future__ import annotations

import re
from core import durable
from core import trace

FLOOR = 32768


def resolve(model_cfg: dict | None) -> dict:
    """The effective window and where it came from, from a `model:` config block."""
    model = model_cfg or {}
    local = model.get("context_window_local")
    if not local:                    # absent, 0 and "" all mean "no floor of its own set"
        local = model.get("window")
        local_rule = "legacy-window-key" if local else "default-floor"
    else:
        local_rule = "context-window-local"
    local = int(local or FLOOR)
    if local < FLOOR:
        trace.failure("window.refused", None, rule="local-below-floor", local=local, floor=FLOOR,
                      error=f"ValueError: context_window_local is {local}; the minimum is {FLOOR} tokens")
        raise ValueError(f"context_window_local is {local}; the minimum is {FLOOR} tokens")
    trace.event("window.local", level="debug", rule=local_rule, local=local, floor_applied=FLOOR,
                over_floor=local > FLOOR)

    provider = model.get("context_window_provider")
    try:
        provider = int(provider) if provider not in (None, "", 0) else None
    except (TypeError, ValueError):
        trace.event("window.provider", level="debug", rule="not-a-number", published=provider,
                    effect="ignored")
        provider = None

    # a published figure belongs to one model. Without that check the installation-wide value leaked into every
    # lane: a local 32K model inherited a 1M figure published for a remote one and sized its stack at 1M.
    published_for = str(model.get("context_window_provider_model") or "").strip()
    name = str(model.get("name") or "").strip()
    if provider is not None and published_for and name and published_for != name:
        trace.event("window.provider", level="debug", rule="published-for-another-model",
                    published=provider, published_for=published_for, model=name, effect="ignored")
        provider = None

    # the provider's figure is authoritative when it publishes one: it is the model's real limit, and the config
    # value is only the fallback for providers that publish nothing. The floor applies to whichever is chosen.
    if provider is not None:
        if provider < FLOOR:
            trace.failure("window.refused", None, rule="provider-below-floor", published=provider,
                          floor=FLOOR,
                          error=f"ValueError: the provider published {provider}; the minimum is {FLOOR} tokens")
            raise ValueError(f"the provider published {provider}; the minimum is {FLOOR} tokens")
        trace.event("window.resolve", level="info", source="provider", window=provider, local=local)
        return {"window": provider, "source": "provider", "local": local, "provider": provider}
    trace.event("window.resolve", level="info", source="config", window=local, local=local,
                provider_published=False)
    return {"window": local, "source": "config", "local": local, "provider": None}


def effective_window(cfg: dict) -> int:
    """The window in force, for readers that hold a whole config rather than a model block."""
    try:
        return resolve((cfg or {}).get("model") or {})["window"]
    except ValueError as exc:
        # sizing at the floor is a degradation, and a silent one would look like a deliberate 32K model
        trace.failure("window.clamped", exc, rule="invalid-window", floor=FLOOR)
        return FLOOR


def adoption(model_cfg: dict, published: dict[str, int] | None) -> dict:
    """Whether a published figure should be written into the config, and why not when it should not.

    The running model has to be one of the ids the provider published — a provider that lists
    `deepseek-flash` says nothing about a config naming `deepseek-chat`, and adopting a figure for a model it never
    mentioned would be invention.
    """
    model = model_cfg or {}
    name = str(model.get("name") or "").strip()
    local = int(model.get("context_window_local") or model.get("window") or FLOOR)
    figure = (published or {}).get(name)
    if not name:
        trace.event("window.adopt", level="debug", adopt=False, rule="no-model-name", local=local)
        return {"adopt": False, "reason": "no model name in the config", "local": local}
    if figure in (None, "", 0):
        trace.event("window.adopt", level="debug", adopt=False, rule="provider-published-nothing",
                    model=name, local=local)
        return {"adopt": False, "reason": f"the provider published nothing for {name}", "local": local}
    figure = int(figure)
    if figure < FLOOR:
        trace.event("window.adopt", level="debug", adopt=False, rule="published-below-floor",
                    model=name, published=figure, floor=FLOOR)
        return {"adopt": False, "reason": f"{name} published {figure}, below the {FLOOR} floor",
                "local": local}
    trace.event("window.adopt", level="info", adopt=True, model=name, window=figure, local=local)
    return {"adopt": True, "window": figure, "model": name, "local": local}


def write_provider_window(config_path, value: int, model_name: str = "") -> bool:
    """Set `model.context_window_provider` in a config file, atomically. False when the key is not there to set."""
    from pathlib import Path as _Path

    path = _Path(config_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        trace.failure("window.write.failed", exc, path=str(path), rule="unreadable")
        return False
    if "context_window_provider:" not in text:
        trace.event("window.write", level="debug", path=str(path), rule="no-key-to-set",
                    value=int(value))
        return False
    new = re.sub(r"^(\s*context_window_provider:)\s*.*$", rf"\g<1> {int(value)}", text, count=1, flags=re.M)
    if model_name:
        if re.search(r"^\s*context_window_provider_model:", new, re.M):
            new = re.sub(r"^(\s*context_window_provider_model:)\s*.*$", rf'\g<1> "{model_name}"', new, count=1, flags=re.M)
        else:
            new = re.sub(r"^(\s*context_window_provider:.*)$", r'\g<1>\n\1'.replace("\\1", "\1"), new, count=1, flags=re.M)
    if new == text:
        return True
    durable.write_state(path, new)
    return True

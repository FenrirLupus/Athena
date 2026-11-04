"""Which context window is in force, and why.

Two facts, deliberately separate: the floor the operator sets, and the figure a provider publishes. **The published
figure has priority** — it is the model's real limit — and it applies only to the model it was published for, which is
why `context_window_provider_model` is stored beside it. The local floor is the fallback for providers that publish
nothing, and a figure below 32,768 is refused rather than adopted.

`source` names which one is in force, so the interface can say "provider" or "config" instead of leaving a bare number.
"""
from __future__ import annotations

import re

FLOOR = 32768


def resolve(model_cfg: dict | None) -> dict:
    """The effective window and where it came from, from a `model:` config block."""
    model = model_cfg or {}
    local = model.get("context_window_local")
    if not local:                    # absent, 0 and "" all mean "no floor of its own set"
        local = model.get("window")
    local = int(local or FLOOR)
    if local < FLOOR:
        raise ValueError(f"context_window_local is {local}; the minimum is {FLOOR} tokens")

    provider = model.get("context_window_provider")
    try:
        provider = int(provider) if provider not in (None, "", 0) else None
    except (TypeError, ValueError):
        provider = None

    # a published figure belongs to one model. Without that check the installation-wide value leaked into every
    # lane: a local 32K model inherited a 1M figure published for a remote one and sized its stack at 1M.
    published_for = str(model.get("context_window_provider_model") or "").strip()
    name = str(model.get("name") or "").strip()
    if provider is not None and published_for and name and published_for != name:
        provider = None

    # the provider's figure is authoritative when it publishes one: it is the model's real limit, and the config
    # value is only the fallback for providers that publish nothing. The floor applies to whichever is chosen.
    if provider is not None:
        if provider < FLOOR:
            raise ValueError(f"the provider published {provider}; the minimum is {FLOOR} tokens")
        return {"window": provider, "source": "provider", "local": local, "provider": provider}
    return {"window": local, "source": "config", "local": local, "provider": None}


def effective_window(cfg: dict) -> int:
    """The window in force, for readers that hold a whole config rather than a model block."""
    try:
        return resolve((cfg or {}).get("model") or {})["window"]
    except ValueError as exc:
        # degrading quietly here is how a bad figure reaches the prompt stack unnoticed: say so, once per process
        import sys

        print(f"context window: {exc} — sizing at the {FLOOR} floor", file=sys.stderr)
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
        return {"adopt": False, "reason": "no model name in the config", "local": local}
    if figure in (None, "", 0):
        return {"adopt": False, "reason": f"the provider published nothing for {name}", "local": local}
    figure = int(figure)
    if figure < FLOOR:
        return {"adopt": False, "reason": f"{name} published {figure}, below the {FLOOR} floor",
                "local": local}
    return {"adopt": True, "window": figure, "model": name, "local": local}


def write_provider_window(config_path, value: int, model_name: str = "") -> bool:
    """Set `model.context_window_provider` in a config file, atomically. False when the key is not there to set."""
    import os
    from pathlib import Path as _Path

    path = _Path(config_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if "context_window_provider:" not in text:
        return False
    new = re.sub(r"^(\s*context_window_provider:)\s*.*$", rf"\g<1> {int(value)}", text, count=1, flags=re.M)
    if model_name:
        if re.search(r"^\s*context_window_provider_model:", new, re.M):
            new = re.sub(r"^(\s*context_window_provider_model:)\s*.*$", rf'\g<1> "{model_name}"', new, count=1, flags=re.M)
        else:
            new = re.sub(r"^(\s*context_window_provider:.*)$", rf'\g<1>\n\1'.replace("\\1", "\1"), new, count=1, flags=re.M)
    if new == text:
        return True
    tmp = path.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(new)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return True

"""Can this lane start? — one answer, in one place.

The failure this exists for: a lane whose `config.yaml` did not parse, which `agentconfig.read()` reported as `{}`.
The runtime then said "no model named for this channel" about a file that named one, and nothing anywhere said the
file was not YAML. The wake check asks the same four questions, but only when a lane is asleep and only as a
pass/fail buried in an exception.

So: config parses · a model is named · the key resolves · the window holds the stack. Print it for one lane or
for all, exit non-zero when any lane cannot start, and never drive anything — diagnosing a lane must not wake it,
write to it, or spend credit.
"""
from __future__ import annotations

from pathlib import Path

from core import agentconfig
from core import channelstate
from core import context_window
from providers import base as providers_base

__all__ = ["diagnose", "diagnose_all"]

MIN_WINDOW = 32768


def diagnose(profile_root, athena_root=None) -> dict:
    """One lane's startup facts. Never raises; `ok` is False when the lane cannot answer a turn."""
    root = Path(profile_root)
    lane = root.name
    status = agentconfig.read_status(root)
    model = (status.get("data") or {}).get("model") or {}
    name = str(model.get("name") or "")
    local = str(agentconfig.local_model_name(model) or "")
    provider = str(model.get("provider") or "")
    problems: list[str] = []

    if status["present"] and not status["ok"]:
        where = f"line {status['line']}: " if status.get("line") else ""
        problems.append(f"config does not parse — {where}{str(status['error'])[:120]}")
    if status["present"] and status["ok"] and not (name or local):
        problems.append("no model named (and no local model)")

    window = 0
    try:
        window = context_window.effective_window(model or agentconfig.as_cfg(status.get("data") or {}))
    except Exception:                                 # noqa: BLE001 — a window that cannot resolve is a problem
        window = 0
    if window and window < MIN_WINDOW:
        problems.append(f"window {window} below the {MIN_WINDOW} floor")

    resolved = {"name": "", "value": "", "source": "none", "looked": []}
    if provider in ("openai", "anthropic", "deepseek", "openrouter"):
        resolved = providers_base.resolve_key(model, root=root, lane=lane)
        if not resolved.get("value"):
            problems.append(f"no key — {resolved.get('source')} tried {', '.join(resolved.get('looked') or []) or 'nothing'}")

    state = channelstate.read(root).get("state") or channelstate.AVAILABLE
    return {"lane": lane, "ok": not problems, "problems": problems, "state": str(state),
            "config": {"present": status["present"], "ok": status["ok"], "line": status.get("line")},
            "model": name or local, "provider": provider, "window": window,
            "key": {"name": resolved.get("name") or "", "source": resolved.get("source") or "none",
                    "found": bool(resolved.get("value"))},
            "path": status.get("path") or str(root)}


def diagnose_all(agents_dir, athena_root=None) -> list[dict]:
    """Every profile on disk, sorted by name. A directory with no sessions still gets a row."""
    base = Path(agents_dir)
    rows: list[dict] = []
    try:
        entries = sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return rows
    for entry in entries:
        rows.append(diagnose(entry, athena_root))
    return rows

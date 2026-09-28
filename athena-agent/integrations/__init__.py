"""Integrations — third-party connections (the Operator's spec).

Integrations are THIRD-PARTY categories — message platforms, voice, etc.
Plugins, tools, and skills are SEPARATE: integrations connect Athena to
the outside world; plugins/tools/skills are capability content.

Each integration category is a subdirectory under integrations/:
    integrations/message_platform/   — Discord, Telegram, Slack, ...
    integrations/voice/              — (future)
    ...

Each integration registers via a manifest + an activate() that wires it
to the gateway (the server's parent routing). The registry lists what
is available and what is connected.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.config import ATHENA_ROOT, DEFAULT_PROFILE_ROOT

INTEGRATIONS_DIR = ATHENA_ROOT / "athena-system" / "integrations"
STATE_FILE = DEFAULT_PROFILE_ROOT / "operations" / "integrations.json"

# Categories are directory names under integrations/.
CATEGORIES = ("message_platform", "voice")


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"connected": {}}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def discover() -> list[dict]:
    """Every integration found in the integrations/ tree."""
    out = []
    for cat in CATEGORIES:
        cat_dir = INTEGRATIONS_DIR / cat
        if not cat_dir.is_dir():
            continue
        for integ_dir in sorted(cat_dir.iterdir()):
            if not integ_dir.is_dir():
                continue
            manifest = integ_dir / "manifest.json"
            if not manifest.exists():
                continue
            try:
                m = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append({
                "name": m.get("name", integ_dir.name),
                "category": cat,
                "description": m.get("description", ""),
                "path": str(integ_dir),
                "connected": _is_connected(m.get("name", integ_dir.name)),
            })
    return out


def _is_connected(name: str) -> bool:
    state = _load_state()
    return bool(state["connected"].get(name, {}).get("active"))


def connect(name: str) -> dict:
    """Connect an integration: load its module + run activate()."""
    for integ in discover():
        if integ["name"] != name:
            continue
        try:
            mod = _load_module(integ["path"], integ["name"])
            if mod is None or not callable(getattr(mod, "activate", None)):
                return {"ok": False, "detail": "integration has no activate()"}
            result = mod.activate()
            state = _load_state()
            state["connected"][name] = {"active": True}
            _save_state(state)
            return {"ok": True, "name": name, "result": result}
        except Exception as exc:
            return {"ok": False, "name": name, "detail": str(exc)}
    return {"ok": False, "name": name, "detail": "not found"}


def disconnect(name: str) -> dict:
    """Disconnect an integration (its deactivate, if any)."""
    for integ in discover():
        if integ["name"] != name:
            continue
        try:
            mod = _load_module(integ["path"], integ["name"])
            if mod is not None and callable(getattr(mod, "deactivate", None)):
                mod.deactivate()
        except Exception:
            pass
        state = _load_state()
        state["connected"].pop(name, None)
        _save_state(state)
        return {"ok": True, "name": name}
    return {"ok": False, "name": name, "detail": "not found"}


def _load_module(path: str, name: str):
    import importlib.util
    import sys
    py = Path(path) / "main.py"
    if not py.exists():
        return None
    spec = importlib.util.spec_from_file_location(
        f"integrations_{name}", str(py))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def status() -> dict:
    """All integrations with connection state."""
    return {"integrations": discover(),
            "connected": _load_state().get("connected", {})}

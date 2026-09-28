"""Version registry — profile version binding (the Operator's spec).

Every profile REGISTERS with a version, bound by the code naturally:

  • ATHENA_VERSION — the code's version (athena.py / package metadata).
  • Each profile's version is recorded when it runs (operations/versions.json).
  • If a profile's version is LOWER than Athena's → the child runtime
    does NOT start (code mismatch — it would run stale logic).
  • If a NEWER Athena version is available → the system reports
    "update available" (auto-update is TOGGLEABLE, DEFAULT FALSE, and
    is a GitHub operation — for when the project is public).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from core.config import ATHENA_ROOT, VERSION, DEFAULT_PROFILE_ROOT

# The code version — SINGLE SOURCE: core.config.VERSION (the Operator's
# release model: 1.0.0 stable / 0.1.0 beta / 0.0.1 alpha). Every
# component imports this one constant — bumping config.VERSION updates
# the whole operational surface (version gate, snapshots, updates).
ATHENA_VERSION = VERSION

VERSIONS_STATE = DEFAULT_PROFILE_ROOT / "operations" / "versions.json"
_lock = threading.Lock()

# Auto-update is OFF by default (the Operator's spec: toggleable, default
# false; GitHub-based once public).
_AUTO_UPDATE = False




def auto_update_enabled() -> bool:
    return _AUTO_UPDATE


def _load() -> dict:
    try:
        if VERSIONS_STATE.exists():
            return json.loads(VERSIONS_STATE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"profiles": {}}


def _save(data: dict) -> None:
    VERSIONS_STATE.parent.mkdir(parents=True, exist_ok=True)
    try:
        VERSIONS_STATE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8")
    except Exception as exc:
        _log(4, f"versions state write failed: {exc}",
             source="version_registry")


def _log(level: int, msg: str, source: str = "version_registry") -> None:
    """The version registry is operational — failures are logged."""
    try:
        from metrics.logger import log
        log(level, msg, source=source)
    except Exception:
        pass


def register(profile: str, version: str = ATHENA_VERSION) -> None:
    """A profile registers the version it runs under."""
    with _lock:
        data = _load()
        data["profiles"][profile] = {
            "version": version,
            "athena_version": ATHENA_VERSION,
        }
        _save(data)


def registered_version(profile: str) -> str:
    data = _load()
    return str(data["profiles"].get(profile, {}).get("version", ""))


def check(profile: str) -> dict:
    """The gate the supervisor consults before starting a child.

    Returns {ok, reason, version, athena_version, update_available}:
      • ok=False when the profile's version is LOWER than Athena's
        (stale profile — the child must not run).
      • update_available=True when Athena's version is newer than the
        registered profile version (a normal upgrade case).
    """
    pv = registered_version(profile)
    if not pv:
        # Not registered yet — allow (it registers on first run).
        return {"ok": True, "reason": "unregistered",
                "version": pv, "athena_version": ATHENA_VERSION,
                "update_available": False}
    try:
        stale = _version_lt(pv, ATHENA_VERSION)
    except Exception:
        stale = pv != ATHENA_VERSION
    return {
        "ok": not stale,
        "reason": "version mismatch: profile is older than Athena"
        if stale else "version match",
        "version": pv,
        "athena_version": ATHENA_VERSION,
        "update_available": stale and _AUTO_UPDATE,
    }


def _version_lt(a: str, b: str) -> bool:
    """Semver-ish compare: "0.2.0" < "0.10.0"."""
    def parts(v):
        out = []
        for chunk in v.replace("-", ".").split("."):
            try:
                out.append(int(chunk))
            except ValueError:
                out.append(0)
        return out

    return parts(a) < parts(b)


def status() -> dict:
    data = _load()
    return {
        "athena_version": ATHENA_VERSION,
        "auto_update": _AUTO_UPDATE,
        "profiles": data.get("profiles", {}),
    }

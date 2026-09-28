"""Supervisor — the server's process manager for profile runtimes.

The Operator's architecture: the SERVER is the main gateway + session. The
default profile (Athena) is embedded — always on, the administrator.
Every other profile runs as its OWN process (a child runtime), spawned
by the server and supervised: start/stop/status/restart, heartbeat
health, and crash recovery (dead child → investigate → doctor/nurse →
restart).

State: operations/runtimes.json — one file for the process-manager
state (the Operator's spec: one file per operation).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from core.config import ATHENA_ROOT, DEFAULT_PROFILE_ROOT

# The supervisor's state file — inside operations/ (system state).
RUNTIMES_STATE = DEFAULT_PROFILE_ROOT / "operations" / "runtimes.json"

# The heartbeat file each child writes (its liveness proof).
HEARTBEAT_DIR = DEFAULT_PROFILE_ROOT / "operations" / "heartbeats"

# How many seconds without a heartbeat before a child is considered dead.
# 30s = 3 missed 10s beats — tolerates a slow tick without false alarms.
HEARTBEAT_TTL_S = 30.0

# The child's heartbeat WRITE cadence — 10s, independent of the 60s tick
# (the Operator's near-realtime spec: write 3× faster than the TTL).
HEARTBEAT_INTERVAL_S = 10.0

# RESTART LOOP PROTECTION (the Operator's spec): if a runtime restarts 3 times
# within 5 seconds, it is a restart LOOP — the child dies on boot. It is
# DISABLED (not restarted again) until the doctor/nurse diagnoses it.
RESTART_WINDOW_S = 5.0
RESTART_MAX = 3

# THE 3 AGENT STATES (the Operator's 08-12 dynamic-cost spec):
#   WAKE      — process running, heartbeat live, ticking, working (full cost)
#   HIBERNATE — process parked: heartbeat alive, tick PAUSED (ms-wake)
#   SLEEP     — no process: state preserved, cold-start ~3s
WAKE = "wake"
HIBERNATE = "hibernate"
SLEEP = "sleep"
AGENT_STATES = (WAKE, HIBERNATE, SLEEP)

# The config dials for the idle lifecycle (overridable in config.yaml):
#   autonomy.idle_hibernate_min  — no board work for N min → hibernate
#   autonomy.hibernate_sleep_min — hibernated for M min → sleep (stop)
IDLE_HIBERNATE_MIN = 10.0
HIBERNATE_SLEEP_MIN = 60.0
# The queen (.default) is ALWAYS ON — never auto-hibernated/slept.
ALWAYS_ON_PROFILES = {".default", ""}


# -- State --------------------------------------------------------------

# The REAL supervisor state path. A test (or leak) must NEVER redirect
# the live supervisor to a tempdir — that is how the 08-12 'loop-test'
# re-enable loop started (the service kept re-enabling a test runtime).
_REAL_STATE = DEFAULT_PROFILE_ROOT / "operations" / "runtimes.json"


def _state_path() -> Path:
    """The EFFECTIVE state path. The seam every read/write goes through —
    tests patch THIS (mock.patch.object) for full isolation; the live
    supervisor uses the real profile operations dir. No redirect: a
    deliberately-set path is honored, which is what makes test
    isolation reliable (a redirect would write test state into the real
    tree — the 08-12 'loop-test' mistake)."""
    return RUNTIMES_STATE


def _load_state() -> dict:
    try:
        if _state_path().exists():
            return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"runtimes": {}}


def _save_state(state: dict) -> None:
    target = _state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def runtime_status(profile: str) -> dict:
    """The registered state + live heartbeat for a profile runtime."""
    state = _load_state()
    entry = state["runtimes"].get(profile, {})
    live = _heartbeat_alive(profile)
    entry["profile"] = profile
    entry["live"] = live
    entry.setdefault("state", WAKE if live else SLEEP)
    return entry


def list_runtimes() -> dict:
    """Every registered runtime with its live status."""
    state = _load_state()
    out = {}
    for profile, entry in state["runtimes"].items():
        out[profile] = runtime_status(profile)
    return out


# -- Heartbeats ---------------------------------------------------------

def _heartbeat_path(profile: str) -> Path:
    return HEARTBEAT_DIR / f"{profile}.heartbeat"


def _touch_heartbeat(profile: str) -> None:
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    _heartbeat_path(profile).write_text(
        json.dumps({"at": time.time(), "profile": profile}),
        encoding="utf-8")


def _heartbeat_alive(profile: str, ttl: float = HEARTBEAT_TTL_S) -> bool:
    p = _heartbeat_path(profile)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            age = time.time() - float(data.get("at", 0))
            return age < ttl
    except Exception:
        pass
    return False


def start_heartbeat(profile: str,
                    interval: float = HEARTBEAT_INTERVAL_S,
                    stop_event=None) -> threading.Thread:
    """The child's OWN heartbeat thread — writes every `interval` seconds,
    independent of the 60s tick loop (near-realtime liveness)."""
    def _beat():
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                _touch_heartbeat(profile)
            except Exception:
                pass
            time.sleep(interval)

    t = threading.Thread(target=_beat, daemon=True,
                         name=f"heartbeat-{profile}")
    t.start()
    return t


def _log(level: int, msg: str, source: str = "supervisor") -> None:
    """The supervisor logs — it is an operational module (its events are
    system operations: starts, stops, crash recovery)."""
    try:
        from metrics.logger import log
        log(level, msg, source=source)
    except Exception:
        pass


# -- Child lifecycle ----------------------------------------------------

def _runtime_command(profile: str) -> list[str]:
    """The command that launches a headless runtime for a profile."""
    # The `athena` launcher (a thin bash wrapper → athena.py) is the
    # canonical entry — it works from anywhere and resolves the system.
    # Profiles are passed via --profile (the launcher's convention).
    import shutil
    launcher = shutil.which("athena")
    if launcher:
        return [launcher, "--profile", profile, "runtime"]
    # Fallback: run athena.py directly with the athena-system dir.
    here = Path(__file__).resolve().parent.parent  # athena-system/
    return [sys.executable, str(here / "athena.py"),
            "--profile", profile, "runtime"]


def start_runtime(profile: str) -> dict:
    """Spawn a child runtime process for a profile (idempotent).

    RESTART LOOP PROTECTION: a runtime DISABLED by the guard (3 restarts
    in the window) refuses to start until re-enabled — the supervisor
    must not burn resources restarting a child that dies on boot.
    """
    state = _load_state()
    existing = state["runtimes"].get(profile) or {}
    if existing.get("disabled"):
        return {"ok": False, "profile": profile,
                "detail": existing.get("disabled_reason", "disabled by "
                                       "restart-loop guard")}
    if existing and _process_alive(existing.get("pid")):
        return {"ok": True, "profile": profile,
                "detail": "already running", "pid": existing["pid"]}
    # VERSION GATE (the Operator's spec): a profile whose version is LOWER
    # than Athena's does NOT start — it would run stale logic.
    try:
        from core.version_registry import check as version_check
        v = version_check(profile)
        if not v.get("ok"):
            _log(3, f"runtime {profile} refused: {v['reason']} "
                    f"(profile {v.get('version')} vs "
                    f"athena {v.get('athena_version')})",
                 source="supervisor")
            return {"ok": False, "profile": profile, "detail": v["reason"],
                    "version": v}
    except Exception:
        pass
    try:
        proc = subprocess.Popen(
            _runtime_command(profile),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # own process group — parent death ≠ child death
        )
    except Exception as exc:
        return {"ok": False, "profile": profile, "detail": str(exc)}
    state["runtimes"][profile] = {
        "pid": proc.pid,
        "started_at": time.time(),
        "status": "running",
        "state": WAKE,
        "state_changed_at": time.time(),
        "restarts": existing.get("restarts", 0),
        "restart_times": existing.get("restart_times", []),
        "disabled": existing.get("disabled", False),
        "disabled_reason": existing.get("disabled_reason", ""),
    }
    _save_state(state)
    _log(2, f"runtime started: {profile} (pid {proc.pid}) state=wake",
         source="supervisor")
    return {"ok": True, "profile": profile, "pid": proc.pid,
            "detail": "started", "state": WAKE}


def stop_runtime(profile: str) -> dict:
    """Stop a child runtime (SIGTERM, then SIGKILL after a grace)."""
    state = _load_state()
    entry = state["runtimes"].get(profile)
    if not entry:
        return {"ok": False, "profile": profile, "detail": "not running"}
    pid = entry.get("pid")
    if pid and _process_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            # Grace period, then force.
            for _ in range(10):
                if not _process_alive(pid):
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    state["runtimes"][profile]["status"] = "stopped"
    state["runtimes"][profile]["stopped_at"] = time.time()
    state["runtimes"][profile]["state"] = SLEEP
    state["runtimes"][profile]["state_changed_at"] = time.time()
    _save_state(state)
    # Clear the heartbeat — a stopped runtime is not live.
    try:
        _heartbeat_path(profile).unlink(missing_ok=True)
    except Exception:
        pass
    _log(2, f"runtime stopped: {profile} state=sleep", source="supervisor")
    return {"ok": True, "profile": profile, "detail": "stopped",
            "state": SLEEP}


def _set_agent_state(profile: str, new_state: str, detail: str = "") -> dict:
    """Transition a runtime's agent state (wake/hibernate/sleep) with the
    audit trail (the Operator's 08-12 dynamic-cost spec)."""
    state = _load_state()
    entry = state["runtimes"].setdefault(profile, {})
    entry["state"] = new_state
    entry["state_changed_at"] = time.time()
    if detail:
        entry["state_detail"] = detail
    _save_state(state)
    _log(2, f"runtime {profile} state → {new_state}{(' — ' + detail) if detail else ''}",
         source="supervisor")
    return {"ok": True, "profile": profile, "state": new_state,
            "detail": detail}


def hibernate_runtime(profile: str) -> dict:
    """PARK a child runtime: the process stays alive (heartbeat continues)
    but its tick is paused — the child skips work until woken. The
    mid-cost state (the Operator's dynamic-cost spec): minimal resource
    use, millisecond wake."""
    if profile in ALWAYS_ON_PROFILES:
        return {"ok": False, "profile": profile,
                "detail": "the queen is always on — cannot hibernate"}
    entry = _load_state()["runtimes"].get(profile) or {}
    if entry.get("state") == SLEEP or not _process_alive(entry.get("pid")):
        return {"ok": False, "profile": profile,
                "detail": "not running — nothing to hibernate"}
    # The child picks up the pause via its state file on its next tick.
    return _set_agent_state(profile, HIBERNATE,
                            detail="idle — tick paused")


def wake_runtime(profile: str) -> dict:
    """Bring a child runtime to full WAKE. If it is SLEEPING (no process),
    spawn it; if HIBERNATING, resume its tick."""
    state = _load_state()
    entry = state["runtimes"].get(profile) or {}
    if entry.get("state") == WAKE and _process_alive(entry.get("pid")):
        return {"ok": True, "profile": profile, "state": WAKE,
                "detail": "already awake"}
    if entry.get("state") == HIBERNATE and _process_alive(entry.get("pid")):
        return _set_agent_state(profile, WAKE, detail="resumed by wake")
    # SLEEP (or unknown) — spawn the process.
    return start_runtime(profile)


def sleep_runtime(profile: str) -> dict:
    """Fully stop a child runtime — the SLEEP state (no process, state
    preserved, cold-start ~3s)."""
    if profile in ALWAYS_ON_PROFILES:
        return {"ok": False, "profile": profile,
                "detail": "the queen is always on — cannot sleep"}
    return stop_runtime(profile)


def _idle_config() -> tuple[float, float]:
    """The idle lifecycle dials from config (the Operator's cost spec):
    (idle_hibernate_min, hibernate_sleep_min)."""
    try:
        from core.config import load_config
        a = (load_config().get("autonomy", {}) or {})
        hi = float(a.get("idle_hibernate_min", IDLE_HIBERNATE_MIN) or IDLE_HIBERNATE_MIN)
        hs = float(a.get("hibernate_sleep_min", HIBERNATE_SLEEP_MIN) or HIBERNATE_SLEEP_MIN)
        return hi, hs
    except Exception:
        return IDLE_HIBERNATE_MIN, HIBERNATE_SLEEP_MIN


def manage_states() -> dict:
    """THE DYNAMIC-COST PASS (the Operator's 08-12 spec): called by the
    server loop each tick. Walks the worker runtimes:
      - a WAKE worker with NO board activity for idle_hibernate_min →
        HIBERNATE (park the tick)
      - a HIBERNATE worker parked for hibernate_sleep_min → SLEEP (stop
        the process — the deepest cost save)
    The queen (.default) is exempt — always on.
    Returns {hibernated: [], slept: []}."""
    from autonomy.kanban import list_tasks
    hib_min, sleep_min = _idle_config()
    state = _load_state()
    now = time.time()
    hibernated, slept = [], []
    for profile, entry in (state.get("runtimes") or {}).items():
        if profile in ALWAYS_ON_PROFILES:
            continue
        if entry.get("disabled"):
            continue
        st = entry.get("state")
        if st == WAKE:
            # how long since the board had ANY open work?
            open_n = 0
            try:
                open_n = len(list_tasks(assignee=profile,
                                        status="todo")) + len(
                    list_tasks(assignee=profile, status="in_progress"))
            except Exception:
                open_n = 0
            idle_since = entry.get("state_changed_at", now)
            if open_n == 0 and (now - idle_since) > hib_min * 60:
                r = hibernate_runtime(profile)
                if r.get("ok"):
                    hibernated.append(profile)
        elif st == HIBERNATE:
            parked_since = entry.get("state_changed_at", now)
            if (now - parked_since) > sleep_min * 60:
                r = sleep_runtime(profile)
                if r.get("ok"):
                    slept.append(profile)
    return {"hibernated": hibernated, "slept": slept}


def restart_runtime(profile: str) -> dict:
    """Stop then start a child runtime (the supervisor's restart).

    Records the restart in the loop guard — if the child restarts too
    often (3 in 5s), it is DISABLED and the restart is refused until the
    doctor/nurse diagnoses and re-enables it (the Operator's spec).
    """
    stop_runtime(profile)
    time.sleep(0.2)
    # Loop-guard: record THIS restart attempt BEFORE starting.
    guard = record_restart(profile)
    if guard.get("disabled"):
        _log(4, f"runtime {profile} DISABLED by restart-loop guard: "
                f"{guard['detail']}", source="supervisor")
        return {"ok": False, "profile": profile, "detail": guard["detail"]}
    r = start_runtime(profile)
    if r.get("ok"):
        state = _load_state()
        entry = state["runtimes"].get(profile)
        if entry:
            entry["restarts"] = entry.get("restarts", 0) + 1
            _save_state(state)
    return r


def record_restart(profile: str) -> dict:
    """Record a restart attempt in the loop guard.

    Returns {"ok", "disabled", "detail"}. When 3 restarts happen within
    RESTART_WINDOW_S (5s), the runtime is DISABLED — the supervisor
    refuses further starts until enable_runtime() (the doctor/nurse's
    diagnosis pass).
    """
    state = _load_state()
    entry = state["runtimes"].setdefault(profile, {})
    now = time.time()
    times = [t for t in entry.get("restart_times", [])
             if now - t <= RESTART_WINDOW_S]
    times.append(now)
    if len(times) >= RESTART_MAX:
        entry["disabled"] = True
        entry["disabled_reason"] = (
            f"restart loop: {len(times)} restarts in "
            f"{RESTART_WINDOW_S:.0f}s — waiting for diagnosis")
        entry["restart_times"] = []
        _save_state(state)
        return {"ok": False, "disabled": True,
                "detail": entry["disabled_reason"]}
    entry["restart_times"] = times
    _save_state(state)
    return {"ok": True, "disabled": False,
            "detail": f"restart {len(times)}/{RESTART_MAX} in window"}


def enable_runtime(profile: str) -> dict:
    """Re-enable a disabled runtime (the doctor/nurse's diagnosis clears it).

    The nurse's consultation or the doctor's pass calls this after the
    root cause is fixed.
    """
    state = _load_state()
    entry = state["runtimes"].get(profile)
    if entry:
        entry["disabled"] = False
        entry["disabled_reason"] = ""
        entry["restart_times"] = []
        entry["restarts"] = 0
        _save_state(state)
        _log(2, f"runtime {profile} re-enabled after diagnosis",
             source="supervisor")
        return {"ok": True, "profile": profile}
    return {"ok": False, "profile": profile, "detail": "not registered"}


def _process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# -- Supervision --------------------------------------------------------

def check_heartbeats() -> dict:
    """The supervisor sweep: every registered runtime that should be up
    but has no live heartbeat is DEAD. Returns the dead profiles."""
    dead = []
    for profile, entry in list_runtimes().items():
        if entry.get("status") == "running" and not entry.get("live"):
            dead.append(profile)
    return {"dead": dead, "checked": len(list_runtimes())}


def supervise(recover: bool = True) -> dict:
    """The supervisor pass. When recover=True (the server's default),
    dead children are auto-restarted — the Operator's crash-recovery spec:
    a crashed profile runtime gets investigated and brought back.

    Returns {checked, dead, restarted, failed}.
    """
    sweep = check_heartbeats()
    result = {"checked": sweep["checked"], "dead": sweep["dead"],
              "classified": [], "restarted": [], "failed": [],
              "intentional": []}
    if not recover:
        return result
    for prof in sweep["dead"]:
        # READINESS (the Operator's spec): a child that is SHUTTING DOWN is
        # stopping INTENTIONALLY — the supervisor must NOT restart it
        # (lifecycle, not a crash). Only crash-dead children recover.
        try:
            from core.readiness import is_shutting_down
            if is_shutting_down(f"runtime:{prof}"):
                result["intentional"].append(prof)
                continue
        except Exception:
            pass
        # CLASSIFY the failure (the Operator's error-classifier spec): the
        # nurse knows WHAT KIND of failure before repairing.
        try:
            from core.error_classifier import describe
            logs = _recent_child_logs(prof)
            packet = describe(logs or "no logs (unexpected exit)",
                              context=f"runtime {prof}")
            result["classified"].append({**packet, "profile": prof})
        except Exception:
            pass
        r = restart_runtime(prof)
        if r.get("ok"):
            result["restarted"].append(prof)
        else:
            result["failed"].append(prof)
    return result


def _recent_child_logs(profile: str, limit: int = 3) -> str:
    """The child's most recent metric log lines (for triage)."""
    from core.config import ATHENA_ROOT, DEFAULT_PROFILE_ROOT
    import glob
    log_dir = ATHENA_ROOT / "athena-system" / "logs" / profile
    files = sorted(glob.glob(str(log_dir / "*_metric.log")))
    if not files:
        return ""
    out = []
    try:
        with open(files[-1], encoding="utf-8", errors="replace") as f:
            for line in f.readlines()[-limit:]:
                out.append(line.strip())
    except Exception:
        pass
    return " | ".join(out)

"""Lifecycle — the four server methods (the Operator's spec).

    1. Startup   — HARD: bring everything up (online or offline).
    2. Shutdown  — HARD: kill everything (online or offline).
    3. Restart   — SOFT: graceful stop of everything, then start (online/offline).
    4. Refresh   — SOFT: reload commands/plugins/skills/config WITHOUT killing.

Hard vs soft (the space Restart Theory):
    hard = force (kill processes, clear state, rebuild from scratch)
    soft = graceful (let the loop finish its turn, then stop/start)

Every method works whether the server process is currently RUNNING or not
(online = a server process exists; offline = none). A hard kill also reaps
stray processes so "kill everything" really means everything.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from core.config import load_config


def _server_procs() -> list[int]:
    """PIDs of athena server processes (this or siblings).

    Matches the REAL server invocation (python .../athena.py server) and
    excludes this process and its parent — a bare 'athena.*server' pattern
    also matches the shell that typed the command (self-kill bug).
    """
    pids = []
    me = os.getpid()
    parent = os.getppid()
    try:
        out = subprocess.run(
            ["pgrep", "-f", "athena.py.*server"],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.splitlines():
            pid = int(line.strip())
            if pid not in (me, parent):
                pids.append(pid)
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"process scan failed: {exc}", source="autonomy",
                  action="running_pids")
    return pids


def _spawn_server() -> int:
    """Start a detached server process. Returns its PID."""
    cfg = load_config()
    root = Path(__file__).parent.parent  # athena-system/
    launcher = root / "athena.py"
    proc = subprocess.Popen(
        [sys.executable, str(launcher), "server"],
        cwd=str(root.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid




# -- HARD ---------------------------------------------------------------

def startup() -> str:
    """Method 1 — HARD START: bring everything up."""
    existing = _server_procs()
    if existing:
        return f"startup: already online (pids {existing}) — use restart for a clean boot"
    pid = _spawn_server()
    # Give it a moment to boot.
    time.sleep(2)
    procs = _server_procs()
    if procs:
        return f"startup: online (pid {pid}, confirmed {procs})"
    return f"startup: spawned pid {pid} but not confirmed — check logs"


def shutdown() -> str:
    """Method 2 — HARD KILL: kill everything, online or offline."""
    procs = _server_procs()
    killed = []
    for pid in procs:
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except ProcessLookupError:
            pass
    # Also reap any stragglers.
    time.sleep(0.5)
    remaining = _server_procs()
    if killed:
        msg = f"shutdown: hard-killed {len(killed)} process(es) {killed}"
    else:
        msg = "shutdown: nothing was running"
    if remaining:
        msg += f"; {len(remaining)} still alive (stubborn)"
    else:
        msg += "; everything down"
    return msg


# -- SOFT ---------------------------------------------------------------

def restart() -> str:
    """Method 3 — SOFT RESTART: graceful stop, then start."""
    procs = _server_procs()
    stopped = []
    for pid in procs:
        try:
            os.kill(pid, signal.SIGTERM)  # graceful — loop finishes its turn
            stopped.append(pid)
        except ProcessLookupError:
            pass
    time.sleep(1.5)
    # Reap anything that ignored SIGTERM with a hard kill (space rule:
    # a change requiring restart MUST be restarted — no stragglers).
    stragglers = _server_procs()
    for pid in stragglers:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(0.5)
    pid = _spawn_server()
    time.sleep(2)
    confirmed = _server_procs()
    parts = []
    if stopped:
        parts.append(f"stopped {len(stopped)} gracefully")
    if stragglers:
        parts.append(f"hard-reaped {len(stragglers)} stragglers")
    if not stopped and not stragglers:
        parts.append("nothing was running")
    parts.append(f"started pid {pid}")
    if confirmed:
        parts.append(f"confirmed {confirmed}")
    else:
        parts.append("NOT confirmed — check logs")
    return "restart: " + "; ".join(parts)


def refresh() -> str:
    """Method 4 — SOFT REFRESH: reload commands/plugins/skills, no kill.

    Online: signals the running server to reload its registries.
    Offline: reloads in-place (the next server boot picks up the changes).
    """
    from autonomy.commands import register_core_commands, refresh_commands
    from intelligence.plugins import load_all
    from intelligence.skills import load_skills

    register_core_commands()  # ensure the core surface is registered
    cmd_count = refresh_commands()
    plugin_summary = load_all()
    skills = load_skills()
    plugin_count = len(plugin_summary["plugins"])
    skill_count = len(skills)

    # Online: poke the server to reload (SIGHUP = reload convention).
    procs = _server_procs()
    for pid in procs:
        try:
            os.kill(pid, signal.SIGHUP)
        except ProcessLookupError:
            pass

    return (f"refresh: {cmd_count} commands, {plugin_count} plugins, "
            f"{skill_count} skills reloaded"
            + (f"; signaled {len(procs)} running server(s)" if procs else
               " (offline — next boot picks it up)"))


def run(method: str) -> str:
    """Dispatch a lifecycle method by name."""
    method = method.lower()
    if method in ("start", "startup", "boot", "up"):
        return startup()
    if method in ("shutdown", "down", "halt", "stop-hard", "kill"):
        return shutdown()
    if method in ("restart", "reboot"):
        return restart()
    if method in ("refresh", "reload"):
        return refresh()
    return ("lifecycle: start|shutdown|restart|refresh — "
            "start=hard start, shutdown=hard kill, restart=soft restart, refresh=soft reload")

"""Resource Manager — monitors and manages the system's resources.

the Operator's spec: the memory manager is really a RESOURCE MANAGER with
its own RESOURCE MONITOR. It watches:
  - system memory (RSS / total)
  - disk usage (the Athena root)
  - the context window (how full the current conversation is)
  - the subagent pool (how many workers are alive)

The monitor samples on a schedule; the manager acts on thresholds:
  - context over upper → suggests compression (the loop already does it)
  - disk over threshold → warns the curator/backup
  - memory pressure   → logs a warning so the nurse can investigate

Pure read + advisory: the manager NEVER kills anything on its own — it
reports so the system can decide (the nurse/supervisor act).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from core.config import ATHENA_ROOT

# Thresholds (the Operator's simple-yet-efficient ethos).
DISK_WARN_PCT = 85.0          # warn when the root disk is >85% full
MEMORY_WARN_PCT = 90.0         # warn when RSS >90% of total
CONTEXT_UPPER = 0.8            # the compression trigger (matches config)
CONTEXT_LOWER = 0.2            # the compression floor (matches config)

_lock = threading.Lock()
_snapshot: dict = {}
_last_sample = 0.0
_SAMPLE_INTERVAL = 30.0


# -- Sampling -----------------------------------------------------------

def _mem() -> dict:
    try:
        rss = 0
        with open("/proc/self/statm") as f:
            parts = f.read().split()
            rss_pages = int(parts[1])
            page = os.sysconf("SC_PAGE_SIZE")
            rss = rss_pages * page
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        return {"rss_bytes": rss, "total_bytes": total,
                "percent": round(rss / total * 100.0, 1) if total else 0.0}
    except Exception:
        return {"rss_bytes": 0, "total_bytes": 0, "percent": 0.0}


def _cpu() -> dict:
    """CPU usage: process % (from /proc/self/stat) + a load reading."""
    try:
        with open("/proc/self/stat") as f:
            parts = f.read().split()
        # utime+stime (fields 14,15) vs the system clock ticks.
        utime = int(parts[13])
        stime = int(parts[14])
        ticks = os.sysconf("SC_CLK_TCK")
        cpu_s = (utime + stime) / ticks
        try:
            load1 = float(open("/proc/loadavg").read().split()[0])
        except Exception:
            load1 = 0.0
        return {"cpu_seconds": round(cpu_s, 2), "load1": load1}
    except Exception:
        return {"cpu_seconds": 0.0, "load1": 0.0}


def _vram() -> dict:
    """VRAM: GPU memory via nvidia-smi (when present) — the generalized
    resource the Operator named. Missing GPU = zeros (not an error)."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            used, total = (x.strip() for x in r.stdout.split(",")[:2])
            used_mb, total_mb = int(used), int(total)
            return {"used_mb": used_mb, "total_mb": total_mb,
                    "percent": round(used_mb / total_mb * 100.0, 1)
                    if total_mb else 0.0}
    except Exception:
        pass
    return {"used_mb": 0, "total_mb": 0, "percent": 0.0}


def _disk() -> dict:
    try:
        total, used, free = shutil.disk_usage(str(ATHENA_ROOT))
        return {"total": total, "used": used, "free": free,
                "percent": round(used / total * 100.0, 1) if total else 0.0}
    except Exception:
        return {"total": 0, "used": 0, "free": 0, "percent": 0.0}


def _context() -> dict:
    """The current conversation's context fullness (0..1)."""
    try:
        from context import compression
        from core.config import load_config
        cfg = load_config()
        comp = cfg.get("compression", {})
        window = int(comp.get("context_window", 32768))
        # Estimate from the most recent session's history.
        from core import db as db_layer
        from intelligence.profiles import current_profile
        prof = current_profile().name
        sid = db_layer.find_last_session(profile=prof) or ""
        history = db_layer.get_session_history(sid, limit=200, profile=prof) \
            if sid else []
        st = compression.context_status(
            history, context_window=window,
            upper_threshold=float(comp.get("upper_threshold", CONTEXT_UPPER)))
        return {"used_tokens": st["used_tokens"],
                "window_tokens": st["window_tokens"],
                "utilization": st["utilization"],
                "over_upper": st["over_upper"]}
    except Exception:
        return {"used_tokens": 0, "window_tokens": 0,
                "utilization": 0.0, "over_upper": False}


def _subagents() -> dict:
    try:
        from autonomy.kanban import list_subagents
        subs = list_subagents()
        by_status = {}
        for s in subs:
            by_status[s["status"]] = by_status.get(s["status"], 0) + 1
        return {"count": len(subs), "by_status": by_status}
    except Exception:
        return {"count": 0, "by_status": {}}


def sample() -> dict:
    """Take a full resource snapshot (all six resources)."""
    snap = {
        "at": time.time(),
        "memory": _mem(),
        "cpu": _cpu(),
        "vram": _vram(),
        "disk": _disk(),
        "context": _context(),
        "subagents": _subagents(),
    }
    with _lock:
        global _snapshot, _last_sample
        _snapshot = snap
        _last_sample = time.time()
    return snap


def latest() -> dict:
    """The most recent snapshot (samples lazily if stale)."""
    with _lock:
        if _snapshot and (time.time() - _last_sample) < _SAMPLE_INTERVAL:
            return dict(_snapshot)
    return sample()


def status() -> dict:
    """The monitor's advisory status: each resource + its health."""
    snap = latest()
    issues = []
    disk = snap["disk"]
    mem = snap["memory"]
    ctx = snap["context"]
    if disk.get("percent", 0) >= DISK_WARN_PCT:
        issues.append(f"disk {disk['percent']}% ≥ {DISK_WARN_PCT}%")
    if mem.get("percent", 0) >= MEMORY_WARN_PCT:
        issues.append(f"memory {mem['percent']}% ≥ {MEMORY_WARN_PCT}%")
    if ctx.get("over_upper"):
        issues.append(f"context {ctx.get('utilization', 0)*100:.0f}% over upper")
    return {
        "resources": snap,
        "healthy": not issues,
        "issues": issues,
        "thresholds": {
            "disk_warn_pct": DISK_WARN_PCT,
            "memory_warn_pct": MEMORY_WARN_PCT,
            "context_upper": CONTEXT_UPPER,
        },
    }


def start_monitor(interval: float = 60.0) -> threading.Thread:
    """The Resource Monitor loop — samples on a schedule (advisory only).

    THE 08-15 CHANGE-TRIGGER FIX (the Operator's audit): the monitor
    logged `resource attention: disk 87.4%` EVERY sample (60s) while the
    disk stayed ≥85% — the same value repeated forever. Now it logs only
    when the ISSUE SET CHANGES: a new issue appears, an old one clears,
    or a value crosses a threshold boundary. A stable 87.4% logs ONCE.
    """
    def _loop():
        _last_logged = None
        while True:
            try:
                snap = sample()
                # Log only when something needs attention.
                issues = []
                if snap["disk"]["percent"] >= DISK_WARN_PCT:
                    issues.append(f"disk {snap['disk']['percent']}%")
                if snap["memory"]["percent"] >= MEMORY_WARN_PCT:
                    issues.append(f"memory {snap['memory']['percent']}%")
                if snap["context"]["over_upper"]:
                    issues.append("context over upper")
                msg = "; ".join(issues)
                # THE CHANGE-TRIGGER (the 08-15 fix): log only when the
                # issue set changed (appeared/cleared/threshold-crossed).
                if msg and msg != _last_logged:
                    from metrics.logger import log
                    log(3, "resource attention: " + msg, source="resource")
                _last_logged = msg if issues else None
            except Exception:
                pass  # the monitor must never break the system
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="resource-monitor")
    t.start()
    return t

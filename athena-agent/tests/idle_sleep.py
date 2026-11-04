"""Idle sleep: parks a quiet channel, and refuses to park one that is busy or waiting on a peer.

Deterministic — the clock is passed in, so nothing sleeps for real.
"""
import copy
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import loader
from providers.mock import MockProvider
from server.channels import ChannelManager
from tools import registry as treg

cfg = copy.deepcopy(loader.load())
cfg.setdefault("agents", {})["idle_sleep_minutes"] = 15
cfg["agents"]["sweep_seconds"] = 3600          # the background thread must not interfere

checks = {}

with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "profiles"
    root.mkdir()
    mgr = ChannelManager(cfg, MockProvider(), treg.default_registry(cfg), root)

    quiet = mgr.channel("quiet", ensure=True)
    busy = mgr.channel("busy", ensure=True)
    waiting = mgr.channel("waiting", ensure=True)

    for channel in (quiet, busy, waiting):
        mgr.set_state(channel, "idle")

    # quiet: 16 minutes since its last activity
    quiet.last_seen = time.time() - 16 * 60
    # busy: also stale, but it is mid-turn
    busy.last_seen = time.time() - 16 * 60
    mgr.set_state(busy, "working")
    # waiting: stale, idle, but an answer from a peer is outstanding
    waiting.last_seen = time.time() - 16 * 60
    waiting.awaiting = {"peer": 1}

    slept = mgr.sweep_idle()

    checks["quiet_was_parked"] = quiet.state == "sleeping" and "quiet" in slept
    checks["busy_untouched"] = busy.state == "working"
    checks["waiting_untouched"] = waiting.state == "idle" and "waiting" not in slept

    # a channel back under the threshold is left alone
    fresh = mgr.channel("fresh", ensure=True)
    mgr.set_state(fresh, "idle")
    fresh.last_seen = time.time() - 60
    mgr.sweep_idle()
    checks["recent_untouched"] = fresh.state == "idle"

    # disabled entirely when the window is 0
    off = copy.deepcopy(cfg)
    off["agents"]["idle_sleep_minutes"] = 0
    mgr_off = ChannelManager(off, MockProvider(), treg.default_registry(off), root)
    idle_off = mgr_off.channel("stays-awake", ensure=True)
    mgr_off.set_state(idle_off, "idle")
    idle_off.last_seen = time.time() - 999 * 60
    checks["zero_disables"] = mgr_off.sweep_idle() == [] and idle_off.state == "idle"

print("slept:", slept if 'slept' in dir() else "?")
for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

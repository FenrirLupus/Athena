"""`waking` is a state with a duration, not a flicker between two assignments.

The wake runs three checks; this test makes them slow and watches the state from another thread.
"""
import copy
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import loader
from providers.mock import MockProvider
from server.channels import ChannelManager
from tools import registry as treg

cfg = copy.deepcopy(loader.load())
cfg.setdefault("agents", {})["idle_sleep_minutes"] = 0
checks = {}

with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "profiles"
    root.mkdir()
    mgr = ChannelManager(cfg, MockProvider(), treg.default_registry(cfg), root)
    channel = mgr.channel("probe", ensure=True)
    # a channel with no model named cannot be woken — the check is right to refuse, so name one
    (root / "probe" / "config.yaml").write_text(
        "model:\n  provider: mock\n  name: mock-1\n", encoding="utf-8")
    mgr._providers.pop("probe", None)
    mgr.set_state(channel, "sleeping")

    # slow the provider check down so the state has a visible lifetime
    original = mgr.provider_for
    def slow_provider(name, root=None):
        time.sleep(0.6)
        return original(name, root)
    mgr.provider_for = slow_provider

    seen = []
    def watch():
        deadline = time.time() + 5
        while time.time() < deadline:
            seen.append(channel.state)
            time.sleep(0.05)

    watcher = threading.Thread(target=watch)
    watcher.start()
    result = mgr.wake("probe")
    watcher.join()

checks["wake_succeeded"] = result["awake"] is True
checks["saw_waking"] = "waking" in seen
checks["settled_idle"] = channel.state == "idle"
checks["waking_was_not_instant"] = seen.count("waking") >= 3

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("states seen:", sorted(set(seen)))
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

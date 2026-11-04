"""Lifecycle: sleep, wake, wake-on-delivery, and the idle sweep.

The state machine is the most-tested thing in the project, so every rule here is pinned before the code moves
(doc 19 §2, doc 22). Written against `ChannelManager` first, then re-run through the extracted collaborator, so
"behaviour preserved" is a measurement rather than a claim.
"""
import copy
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import loader
from core import channelstate
from providers.mock import MockProvider
from server.channels import ChannelAsleep, ChannelManager
from tools import registry as treg

cfg = copy.deepcopy(loader.load())
cfg.setdefault("agents", {})["idle_sleep_minutes"] = 15
cfg["agents"]["sweep_seconds"] = 3600
cfg["metrics"] = {"enabled": False}
checks = {}


def force(channel, state):
    """Set a state for SETUP only.

    `set_state` enforces the legal transitions (a sleeping channel may only go to `waking`), so a test that
    wants to start from "working" cannot get there by asking nicely — and silently stays where it was, which
    is what made the first version of this file fail against correct code.
    """
    channelstate.write(channel.root, state)
    channel.state = state
    return channel


def make(mgr, root, name, model=True):
    mgr.create(name)
    if model:
        (root / name / "config.yaml").write_text("model:\n  provider: mock\n  name: mock-1\n", encoding="utf-8")
        mgr._providers.pop(name, None)
    mgr.set_state(mgr.channel(name), "idle")
    return mgr.channel(name)


with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "profiles"
    root.mkdir()
    mgr = ChannelManager(cfg, MockProvider(), treg.default_registry(cfg), root)
    alpha = make(mgr, root, "alpha")
    blind = make(mgr, root, "blind", model=False)          # no model named: cannot pass a wake check

    # --- sleep --------------------------------------------------------------
    result = mgr.sleep("alpha")
    checks["sleep_parks_idle"] = result["state"] == "sleeping" and alpha.state == "sleeping"
    checks["sleep_persisted"] = channelstate.read(root / "alpha").get("state") == "sleeping"
    checks["sleep_is_idempotent"] = mgr.sleep("alpha").get("already") is True

    force(alpha, "working")
    try:
        mgr.sleep("alpha")
        checks["sleep_refuses_busy"] = False
    except ValueError:
        checks["sleep_refuses_busy"] = True
    checks["sleep_left_state_alone"] = alpha.state == "working"

    # --- wake ---------------------------------------------------------------
    force(alpha, "sleeping")
    woke = mgr.wake("alpha")
    checks["wake_reports_three_checks"] = set(woke["checks"]) == {"channel", "provider", "model"}
    checks["wake_succeeds"] = woke["awake"] is True and woke["state"] == "idle"
    checks["wake_persisted"] = channelstate.read(root / "alpha").get("state") == "idle"
    checks["wake_all_checks_ok"] = all(c["ok"] for c in woke["checks"].values())

    force(blind, "sleeping")
    refused = mgr.wake("blind")
    checks["wake_fails_without_model"] = refused["awake"] is False
    checks["wake_failure_lands_in_error"] = refused["state"] == "error"
    checks["wake_names_the_failing_check"] = (
        not refused["checks"]["model"]["ok"] and "no model named" in refused["checks"]["model"]["detail"])

    # --- wake on delivery ---------------------------------------------------
    force(alpha, "sleeping")
    mgr.ensure_awake(alpha)
    checks["delivery_wakes"] = alpha.state in ("idle", "working", "queued")

    force(blind, "sleeping")
    try:
        mgr.ensure_awake(blind)
        checks["delivery_refuses_unwakeable"] = False
        checks["delivery_names_reason"] = False
    except ChannelAsleep as exc:
        checks["delivery_refuses_unwakeable"] = True
        checks["delivery_names_reason"] = "no model named" in str(exc)

    # --- the idle sweep -----------------------------------------------------
    force(alpha, "idle")
    alpha.last_seen = time.time() - 16 * 60
    checks["sweep_parks_stale_idle"] = mgr.sweep_idle() == ["alpha"] and alpha.state == "sleeping"

    force(alpha, "idle")
    alpha.last_seen = time.time() - 16 * 60
    alpha.awaiting = {"someone": 1}                        # waiting on a peer is not idle
    checks["sweep_skips_awaiting"] = mgr.sweep_idle() == [] and alpha.state == "idle"
    alpha.awaiting = {}

    alpha.last_seen = time.time()
    checks["sweep_skips_recent"] = mgr.sweep_idle() == []

    alpha.last_seen = time.time() - 16 * 60
    force(alpha, "working")
    checks["sweep_skips_working"] = mgr.sweep_idle() == [] and alpha.state == "working"

    off = copy.deepcopy(cfg)
    off["agents"]["idle_sleep_minutes"] = 0
    mgr_off = ChannelManager(off, MockProvider(), treg.default_registry(off), root)
    idle = force(mgr_off.channel("alpha", ensure=True), "idle")
    idle.last_seen = time.time() - 999 * 60
    checks["sweep_zero_disables"] = mgr_off.sweep_idle() == [] and idle.state == "idle"

# --- the collaborator on its own ------------------------------------------
# This section could not exist before the extraction: it drives ChannelLifecycle with a stub registry and no
# manager, no worker thread, no queue drain. Being testable alone is the actual payoff of splitting it out.
from core import profile as profile_mod
from server.lifecycle import ChannelLifecycle, ChannelAsleep as LifecycleAsleep


class FakeSession:
    def history(self):
        return [{"role": "user", "content": "hi"}]


class FakeChannel:
    def __init__(self, root, name="solo"):
        self.root = root
        self.name = name
        self.state = "idle"
        self.last_seen = 0.0
        self.queue = type("Q", (), {"qsize": lambda self: 0})()
        self.awaiting = {}
        self.session = FakeSession()
        self._lock = __import__("threading").Lock()


class StubRegistry:
    def __init__(self, channel, cfg):
        self.one = channel
        self.cfg = cfg

    def channel(self, name):
        return self.one

    def channels_snapshot(self):
        return [self.one]

    def set_state(self, channel, state, **extra):
        channelstate.write(channel.root, state)
        channel.state = state

    def provider_for(self, name, root=None):
        class P:
            def describe(self):
                return {"provider": "mock", "model": "mock-1"}
        return P()


with tempfile.TemporaryDirectory() as td2:
    solo_root = Path(td2) / "solo"
    profile_mod.ensure(solo_root)
    (solo_root / "config.yaml").write_text("model:\n  provider: mock\n  name: mock-1\n", encoding="utf-8")
    solo = FakeChannel(solo_root)
    life = ChannelLifecycle(StubRegistry(solo, cfg))

    checks["alone_wake_verifies"] = life.wake("solo")["awake"] is True and solo.state == "idle"
    solo.state = "sleeping"
    life.ensure_awake(solo)
    checks["alone_delivery_wakes"] = solo.state == "idle"

    solo.state = "working"
    try:
        life.sleep("solo")
        checks["alone_sleep_refuses_busy"] = False
    except ValueError:
        checks["alone_sleep_refuses_busy"] = True

    solo.state = "idle"
    solo.last_seen = time.time() - 16 * 60
    checks["alone_sweep_parks"] = life.sweep_idle() == ["solo"] and solo.state == "sleeping"

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

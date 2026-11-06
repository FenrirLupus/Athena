"""Routing: who gets a message, and what the roster says.

Written *before* the router was extracted out of `ChannelManager` so the move could be proven
behaviour-preserving rather than "looks fine". Every case here is a rule the docs state in words
(documentation/architecture/19-messaging-and-lifecycle.md §3).
"""
import copy
import sys
import tempfile
import time
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from config import loader
from core import mentions
from providers.mock import MockProvider
# imported from `server.channels` on purpose: the constants move to `server/instructions.py` during the
# extraction, and channels re-exports them, so this test proves the same rule either side of the move
from server.channels import DEFAULT_INSTRUCTIONS, ChannelManager
from tools import registry as treg

cfg = copy.deepcopy(loader.load())
cfg.setdefault("agents", {})["idle_sleep_minutes"] = 0
cfg["metrics"] = {"enabled": False}
checks = {}


def wait_until(predicate, seconds=8.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def got_from(channel, author, needle=""):
    """A peer's message arrives as a user row whose text is prefixed `[from @author]`.

    The row does not carry an `author` field — the HTTP layer derives that from the channel's last result —
    so the only place the sender is recorded is that prefix. Asserting on a field that does not exist made
    this test fail against correct code.
    """
    with channel._lock:
        history = channel.session.history()
    return any(m.get("role") == "user"
               and f"[from @{author}]" in (m.get("content") or "")
               and needle in (m.get("content") or "")
               for m in history)


# the manager's daemon workers outlive this block, so a strict teardown raises "Directory not empty"; the
# assertions below already ran, and a leaked temp directory is cheaper than a flaky gate
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
    root = Path(td) / "profiles"
    root.mkdir()
    mgr = ChannelManager(cfg, MockProvider(), treg.default_registry(cfg), root)
    for name in ("alpha", "beta"):
        mgr.create(name)
        # a profile with no model named cannot be woken (the wake check verifies the model resolves), so the
        # "a mention wakes a sleeping peer" case would fail for a reason that has nothing to do with routing
        (root / name / "config.yaml").write_text("model:\n  provider: mock\n  name: mock-1\n", encoding="utf-8")
        mgr._providers.pop(name, None)
        mgr.set_state(mgr.channel(name), "idle")

    # --- roster -------------------------------------------------------------
    text = mgr.roster("alpha")
    checks["roster_excludes_self"] = "@alpha" not in text and "@beta" in text
    checks["roster_carries_state"] = "idle" in text

    instructions = mgr.instructions_for(mgr.channel("alpha", ensure=True))
    checks["instructions_include_roster"] = "@beta" in instructions and instructions.startswith(DEFAULT_INSTRUCTIONS)

    # --- turn 1: this agent addressed a peer ---------------------------------
    # hold beta's lock so its worker cannot start the turn yet: `awaiting` is set at admission, and with a
    # fast mock the answer comes home before a poll can observe it (measured, not assumed)
    beta = mgr.channel("beta", ensure=True)
    beta._lock.acquire()
    try:
        mgr._route(mgr.channel("alpha", ensure=True), {"status": "completed", "reply": "@beta: what is 2+2?"},
                   {"hops": 0})
        with mgr.channel("alpha", ensure=True)._lock:
            checks["asker_marked_awaiting"] = mgr.channel("alpha", ensure=True).awaiting.get("beta") == 1
    finally:
        beta._lock.release()
    checks["mention_delivered"] = wait_until(lambda: got_from(beta, "alpha", "2+2"))

    # --- turn 3: the answer comes home --------------------------------------
    peer_answer = {"status": "completed", "reply": "4"}
    mgr._route(mgr.channel("beta", ensure=True), peer_answer, {"hops": 1, "reply_to": "alpha"})
    checks["answer_returned"] = wait_until(lambda: got_from(mgr.channel("alpha", ensure=True), "beta", "4"))
    checks["asker_no_longer_awaiting"] = wait_until(
        lambda: not mgr.channel("alpha", ensure=True).awaiting.get("beta"), seconds=3)

    # --- the hop cap --------------------------------------------------------
    # assert on the *content* that would have been delivered, not on history length: beta is still draining
    # earlier turns, so its history grows for reasons unrelated to this dispatch
    mgr._route(mgr.channel("alpha", ensure=True), {"status": "completed", "reply": "@beta: one more"},
               {"hops": mentions.MAX_HOPS})
    time.sleep(0.8)
    checks["hop_cap_stops_dispatch"] = not any(
        "one more" in (m.get("content") or "") for m in mgr.channel("beta", ensure=True).session.history())

    # --- a sleeping peer is woken by the mention ---------------------------
    mgr.set_state(mgr.channel("beta", ensure=True), "sleeping")
    mgr._route(mgr.channel("alpha", ensure=True), {"status": "completed", "reply": "@beta: are you up?"},
               {"hops": 0})
    checks["mention_wakes_peer"] = wait_until(
        lambda: mgr.channel("beta", ensure=True).state in ("working", "idle", "queued")
        and got_from(mgr.channel("beta", ensure=True), "alpha", "are you up"))

    # --- a failed delivery tells the asker, rather than letting it wait ----
    original = mgr.enqueue

    def failing(name, *args, **kwargs):
        if name == "beta":
            raise ValueError("beta is unreachable")
        return original(name, *args, **kwargs)

    mgr.enqueue = failing
    try:
        mgr._route(mgr.channel("alpha", ensure=True), {"status": "completed", "reply": "@beta: anyone there?"},
                   {"hops": 0})
    finally:
        mgr.enqueue = original
    checks["failed_delivery_notice"] = wait_until(
        lambda: any("[delivery failed]" in (m.get("content") or "")
                    for m in mgr.channel("alpha", ensure=True).session.history()))

    # --- a failed turn still clears the asker's pending state --------------
    mgr.set_state(mgr.channel("beta", ensure=True), "idle")
    with mgr.channel("alpha", ensure=True)._lock:
        mgr.channel("alpha", ensure=True).awaiting = {"beta": 1}
    mgr._route(mgr.channel("beta", ensure=True), {"status": "error_api", "reply": "boom"},
               {"hops": 1, "reply_to": "alpha"})
    with mgr.channel("alpha", ensure=True)._lock:
        checks["failed_answer_clears_awaiting"] = "beta" not in mgr.channel("alpha", ensure=True).awaiting

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

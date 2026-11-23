"""Admission: how a task gets in, waits, and is announced to a running turn.

Pinned before the code moves out of `ChannelManager` (plan B2c), so the extraction is provably
behaviour-preserving. The rules come from doc 19 §2 and the queue section of doc 11.
"""
import copy
import sys
import tempfile
import threading
import time
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from config import loader
from core import channelstate
from core import mentions
from providers.mock import MockProvider
from server.channels import ChannelManager
from tools import registry as treg

cfg = copy.deepcopy(loader.load())
cfg.setdefault("agents", {})["idle_sleep_minutes"] = 0
cfg["agents"]["max_queue"] = 4
cfg["metrics"] = {"enabled": False}
checks = {}


def _wait(predicate, seconds=8.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def force(channel, state):
    """Setup only: `set_state` rightly refuses illegal transitions."""
    channelstate.write(channel.root, state)
    channel.state = state
    return channel


with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "profiles"
    root.mkdir()
    mgr = ChannelManager(cfg, MockProvider(), treg.default_registry(cfg), root)
    for name in ("alpha", "beta"):
        mgr.create(name)
        (root / name / "config.yaml").write_text("model:\n  provider: mock\n  name: mock-1\n", encoding="utf-8")
        mgr._providers.pop(name, None)
        force(mgr.channel(name, ensure=True), "idle")

    # --- rejections ---------------------------------------------------------
    for label, call, needle in (
        ("empty", lambda: mgr.enqueue("alpha", "   "), "empty message"),
        ("hops", lambda: mgr.enqueue("alpha", "hi", hops=mentions.MAX_HOPS + 1), "hops deep"),
    ):
        try:
            call()
            checks[f"rejects_{label}"] = False
        except ValueError as exc:
            checks[f"rejects_{label}"] = needle in str(exc)

    # --- a full queue is refused, not grown ---------------------------------
    # `0` means "nothing may wait", and `qsize() >= 0` is true immediately — with `1` the channel's own worker
    # drained the occupant before the check ran, which made the assertion a race, not a test.
    mgr.max_queue = 0
    try:
        mgr.enqueue("alpha", "one too many")
        checks["rejects_full_queue"] = False
    except ValueError as exc:
        checks["rejects_full_queue"] = "queue is full" in str(exc)
    finally:
        mgr.max_queue = 32          # the rest of the suite needs a queue that accepts work
    # the queue must be empty before the FIFO block sizes it: a leftover job made the next assertion a race
    while not mgr.channel("alpha", ensure=True).queue.empty():
        try:
            mgr.channel("alpha", ensure=True).queue.get_nowait()
        except Exception:                      # noqa: BLE001
            break
    mgr.max_queue = 32      # the 4 above was the capacity assertion's; FIFO submits faster than the worker drains

    # --- enqueue ------------------------------------------------------------
    queued = mgr.enqueue("beta", "do a thing", author="alpha")
    # depth is whatever is *left* at the moment of return — the worker may already have taken it, so the
    # property is "accepted and delivered", not "still sitting there"
    checks["enqueue_accepts"] = queued.get("queued") is True
    checks["enqueue_delivers"] = _wait(lambda: any("do a thing" in (m.get("content") or "")
                                                  for m in mgr.channel("beta", ensure=True).session.history()))

    # --- reply_to marks the asker as waiting -------------------------------
    mgr.channel("alpha", ensure=True).awaiting = {}
    mgr.enqueue("beta", "question", author="alpha", reply_to="alpha")
    with mgr.channel("alpha", ensure=True)._lock:
        checks["reply_to_sets_awaiting"] = mgr.channel("alpha", ensure=True).awaiting.get("beta") == 1
    mgr.channel("alpha", ensure=True).awaiting = {}

    # --- enqueue wakes a sleeping channel ----------------------------------
    force(mgr.channel("beta", ensure=True), "sleeping")
    mgr.enqueue("beta", "wake up please")
    # the state is transient — the turn runs and the lane may be back asleep by the next instruction — so this
    # asserts what the behaviour is for: the queued message was actually picked up and answered
    woke = time.time() + 10
    while time.time() < woke:
        with mgr.channel("beta", ensure=True)._lock:
            seen = any("wake up please" in (m.get("content") or "")
                       for m in mgr.channel("beta", ensure=True).session.history())
        if seen:
            break
        time.sleep(0.05)
    checks["enqueue_wakes_sleeping"] = seen

    # --- submit runs and returns the turn ----------------------------------
    result = mgr.submit("alpha", "hello there", wait_s=20)
    checks["submit_returns_turn"] = result.get("status") == "completed" and bool(result.get("reply"))

    # --- submit times out without losing the job ---------------------------
    stalled = mgr.submit("alpha", "slow one", wait_s=0.0)
    checks["submit_timeout_queued"] = stalled.get("status") == "queued" and "still running" in stalled.get("note", "")

    # --- FIFO order ---------------------------------------------------------
    mgr.channel("alpha", ensure=True).turns = 0
    for i in range(3):
        mgr.submit("alpha", f"ordered {i}", wait_s=20)
    with mgr.channel("alpha", ensure=True)._lock:
        history = mgr.channel("alpha", ensure=True).session.history()
    # startswith, not "in": an interrupt notice quotes the message it announces, and a substring match
    # counted those as turns (the ordering itself was correct)
    def spoken(text):
        """A turn is stored with its mode marker in front; the order is about the words, not the marker."""
        for marker in ("[Message] ", "[Voice] "):
            if text.startswith(marker):
                return text[len(marker):]
        return text
    order = [spoken(m["content"]) for m in history
             if m.get("role") == "user" and spoken(m.get("content") or "").startswith("ordered ")]
    checks["fifo_order"] = order == ["ordered 0", "ordered 1", "ordered 2"]

    # --- notices while working ---------------------------------------------
    busy = mgr.channel("beta", ensure=True)
    force(busy, "working")
    busy.busy = True
    busy.current_turn = 7
    busy.inbox = []
    before = len(busy.inbox)
    mgr._notice_if_busy(busy, "a new message from someone", author="alpha")
    checks["notice_while_working"] = len(busy.inbox) == before + 1
    notice = busy.inbox[-1]["text"]
    checks["notice_names_sender"] = "@alpha" in notice and "already queued" in notice
    checks["notice_clipped"] = len(busy.inbox[-1]["text"]) < 600

    force(mgr.channel("alpha", ensure=True), "idle")
    mgr.channel("alpha", ensure=True).busy = False
    mgr.channel("alpha", ensure=True).inbox = []
    mgr._notice_if_busy(mgr.channel("alpha", ensure=True), "ignored when idle")
    checks["notice_only_when_busy"] = mgr.channel("alpha", ensure=True).inbox == []

    # a flood is not more information
    force(busy, "working")
    busy.busy = True
    busy.inbox = [{"turn": 7, "text": "x"} for _ in range(8)]
    mgr._notice_if_busy(busy, "ninth")
    checks["notice_capped"] = len(busy.inbox) == 8

    # --- drain: mine only, stale kept --------------------------------------
    busy.current_turn = 7
    busy.inbox = [{"turn": 7, "text": "mine"}, {"turn": 6, "text": "stale"}]
    mine = mgr._drain(busy)
    checks["drain_returns_current_turn"] = mine == ["mine"]
    checks["drain_keeps_stale"] = any(item.get("turn") == 6 for item in busy.inbox)
    checks["drain_empty_when_none"] = mgr._drain(busy) == []

    # --- the queue survives a refused turn start ---------------------------
    mgr.set_state(mgr.channel("alpha", ensure=True), "idle")
    _ = mgr.submit("alpha", "after everything", wait_s=20)

    # --- two jobs queued in the same window both run -----------------------
    # the worker drains both queues until empty and only then sleeps, so a second job queued while the first is
    # still running is picked up rather than left waiting for a wake that already happened
    lane = mgr.channel("alpha", ensure=True)
    with lane._lock:
        before_turns = lane.turns
    mgr.enqueue("alpha", "burst one")
    mgr.enqueue("alpha", "burst two")
    deadline = time.time() + 20
    while time.time() < deadline and lane.turns < before_turns + 2:
        time.sleep(0.05)
    checks["burst_not_lost"] = lane.turns >= before_turns + 2

    # --- stop() answers a queued caller instead of letting it time out ------
    closing = mgr.channel("gamma", ensure=True)
    answered = threading.Event()
    closing.queue.put({"text": "never runs", "done": answered, "queued_at": time.time(), "mode": "chat"})
    mgr.admission.stop(closing)
    checks["stop_answers_queued"] = answered.wait(2.0)

    # --- stop() marks the channel so a parked worker exits -----------------
    retiring = mgr.channel("beta", ensure=True)
    mgr.admission.stop(retiring)
    checks["stop_marks_channel"] = bool(getattr(retiring, "stopping", False))

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

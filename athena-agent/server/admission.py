"""Admission — how work gets into a channel: the queue, the wait, and the interruption notice.

Extracted from `ChannelManager` (optimization plan B2c). The manager keeps the channel registry; routing is
`server/routing.py`, lifecycle is `server/lifecycle.py`, and admission is this.

Two invariants drive it:

- **Each subchannel is a dumb FIFO; the agent is the state machine.** A task is queued the moment it arrives
  and starts only when the channel is idle. Typed turns keep their order and spoken turns keep theirs; the worker
  serves voice ahead of messages so a question asked out loud is not held behind a backlog of typing.
- **The caller waits on its own job, never on the channel.** A lane-wide event once woke whoever happened to be
  waiting, so two concurrent submits returned each other's turn; each job carries its own `done`.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Protocol

from core import channelstate
from core import mentions
from core import trace


# A sentinel, not a flag: the worker is blocked inside `queue.get()`, so the only way to tell it anything is to put
# something in the queue. Without this every delete/recreate cycle parked one more daemon thread for ever.
_STOP = object()


class AdmissionRegistry(Protocol):
    """What admission needs from the thing that owns the channels."""

    max_queue: int

    def channel(self, name: str): ...

    def set_state(self, channel, state: str, **extra) -> None: ...

    def ensure_awake(self, channel) -> None: ...

    def do_turn(self, channel, job: dict) -> None: ...


class Admission:
    def __init__(self, registry: AdmissionRegistry):
        self.registry = registry

    # -- the interruption notice -------------------------------------------
    def notice_if_busy(self, channel, text: str, author: str = "") -> None:
        """While a turn runs, tell it that new input arrived — *without* letting it answer twice.

        The message is already queued as its next task, so the notice says so: answer it when you get
        there, but if it changes what you are doing, adapt now. That is the difference between an
        interruption and a second conversation.
        """
        if channel.state not in (channelstate.WORKING, channelstate.QUEUED) or channel.busy is False:
            if trace.enabled("trace"):
                trace.event("admission.notice", level="trace", channel=channel.name, sent=False,
                            reason=f"idle (state={channel.state}, busy={channel.busy})")
            return
        who = f"@{author}" if author else "the operator"
        clipped = " ".join(text.split())[:280]
        with channel._lock:
            if len(channel.inbox) >= 8:                  # a flood is not more information
                if trace.enabled("trace"):
                    trace.event("admission.notice", level="trace", channel=channel.name, sent=False,
                                reason="inbox full", inbox=len(channel.inbox))
                return
            # a notice belongs to the turn that was running when it arrived: if that turn ends before a
            # boundary, the notice is stale and must not be handed to the NEXT turn, which is already
            # the task the notice announced (measured: turn 2 received a notice about turn 2)
            channel.inbox.append({"turn": channel.current_turn, "text": (
                f"[interrupt] {who} sent a new message while you were working: \"{clipped}\" — "
                f"it is already queued as your next task. Acknowledge it in ONE short line, then finish "
                f"what you were doing; change course only if it makes the current work pointless. "
                f"Do not answer the new message in full yet.")})
        if trace.enabled("trace"):
            trace.event("admission.notice", level="trace", channel=channel.name, sent=True,
                        turn=channel.current_turn, inbox=len(channel.inbox), chars=len(clipped))

    def drain(self, channel) -> list[str]:
        """Take the notices addressed to the running turn; drop any left over from a finished one."""
        with channel._lock:
            if not channel.inbox:
                return []
            current = channel.current_turn
            mine = [item["text"] for item in channel.inbox if item.get("turn") == current]
            stale = [item for item in channel.inbox if item.get("turn") != current]
            channel.inbox = stale
        if trace.enabled("trace"):
            trace.event("admission.drain", level="trace", channel=channel.name, turn=current,
                        delivered=len(mine), stale_dropped=len(stale))
        return mine

    # -- putting work in ---------------------------------------------------
    def enqueue(self, name: str, text: str, *, author: str = "", hops: int = 0,
                reply_to: str | None = None, mode: str = "chat") -> dict:
        """Queue a turn without waiting for it. Used by mentions and by the /ask endpoint."""
        registry = self.registry
        if not text.strip():
            raise ValueError("an empty message has nothing to answer")
        if hops > mentions.MAX_HOPS:
            raise ValueError(f"mention chain is {hops} hops deep (max {mentions.MAX_HOPS})")
        channel = registry.channel(name)

        registry.ensure_awake(channel)       # a mention wakes a sleeping peer the same way
        channel.last_seen = time.time()

        # Admission: a task is *queued* whenever it arrives; the channel only *starts* one when it is idle.
        # The queue depth is capped so a flood is refused rather than growing without bound.
        self._check_capacity(name, channel, mode)
        target = self._queue_for(channel, mode)
        target.put({"text": text, "author": author, "hops": hops, "reply_to": reply_to,
                    "queued_at": time.time(), "mode": mode})
        self._wake(channel)                            # the worker sleeps on this event, so every put must set it
        trace.event("admission.put", level="debug", channel=name, mode=mode,
                    queue="voice" if target is channel.call_queue else "message",
                    depth=target.qsize(), max_queue=self.registry.max_queue,
                    hops=hops, reply_to=reply_to or "", chars=len(text), work_ready=True)
        self.notice_if_busy(channel, text, author)
        if reply_to and reply_to != name:
            # the asker now has an outstanding question: this is what the interface shows on the right
            # of its thread ("waiting on Scout"), which is otherwise unanswerable from the outside
            asker = registry.channel(reply_to)
            with asker._lock:
                asker.awaiting[name] = asker.awaiting.get(name, 0) + 1
        self._mark_queued(channel)
        return {"queued": True, "channel": name, "author": author, "hops": hops,
                "queue_depth": self._queue_for(channel, mode).qsize()}

    def submit(self, name: str, text: str, wait_s: float = 120.0, mode: str = "chat") -> dict:
        """Queue a turn and wait for *this* job to finish (or for the wait to expire)."""
        registry = self.registry
        channel = registry.channel(name)
        registry.ensure_awake(channel)       # the message itself is the wake signal
        channel.last_seen = time.time()
        with channel._lock:
            before = channel.turns
        self._check_capacity(name, channel, mode)
        done = threading.Event()
        job = {"text": text, "done": done, "queued_at": time.time(), "mode": mode}
        target = self._queue_for(channel, mode)
        target.put(job)
        self._wake(channel)
        trace.event("admission.put", level="debug", channel=name, mode=mode,
                    queue="voice" if target is channel.call_queue else "message",
                    depth=target.qsize(), max_queue=self.registry.max_queue,
                    wait_s=wait_s, chars=len(text), work_ready=True)
        self.notice_if_busy(channel, text)
        self._mark_queued(channel)
        started = time.time()
        finished = done.wait(timeout=wait_s)
        if job.get("cancelled"):
            # the queue answered before the worker could take the job, so the wait ends here rather than after
            # the timeout with a "still queued" report for work that will never run
            trace.event("admission.result", level="debug", channel=name, mode=mode, outcome="cancelled",
                        waited_s=round(time.time() - started, 3))
            return {"channel": name, "status": "cancelled", "reply": "",
                    "note": job.get("note") or "the channel was closed before this turn ran"}
        with channel._lock:
            result = dict(channel.last_result or {})
            advanced = channel.turns > before
        if not finished or not advanced:
            # the caller gives up before the turn does: the job is still in the queue, and this line is the
            # only evidence that a timeout was a wait and not a lost turn
            trace.event("admission.result", level="debug", channel=name, mode=mode, outcome="timeout",
                        waited_s=round(time.time() - started, 3), finished=finished, advanced=advanced,
                        depth=target.qsize())
            return {"channel": name, "status": "queued",
                    "reply": "", "queue_depth": self._queue_for(channel, mode).qsize(),
                    "note": f"turn still running after {wait_s:.0f}s; it will finish in order"}
        trace.event("admission.result", level="debug", channel=name, mode=mode, outcome="completed",
                    waited_s=round(time.time() - started, 3), status=result.get("status"))
        return result

    # -- the worker --------------------------------------------------------
    @staticmethod
    def _queue_for(channel, mode: str):
        """A voice turn has its own FIFO, so a call is never stuck behind a queue of typed turns."""
        return channel.call_queue if (mode or "chat") == "call" else channel.queue

    def _next_job(self, channel):
        """Voice first, then message: each subchannel keeps its own order, calls are served before waiting chats."""
        for source in (channel.call_queue, channel.queue):
            try:
                return source.get_nowait(), source
            except queue.Empty:
                continue
        return None, None

    def worker(self, channel) -> None:
        """Drain this channel's two queues forever — one turn at a time, each queue in its own order."""
        while True:
            if channel.stopping:                      # the profile was deleted: nothing more to serve
                return
            job, source = self._next_job(channel)
            if job is None:
                # the wait is a decision too: at this point the worker owns nothing and the queues hold only
                # what arrives next, so the depths printed here are what a "stuck" channel is stuck behind
                trace.event("admission.wait", level="debug", channel=channel.name, outcome="idle",
                            chat=channel.queue.qsize(), voice=channel.call_queue.qsize())
                channel.work_ready.wait()
                channel.work_ready.clear()
                job, source = self._next_job(channel)
                if job is None:
                    # woken with nothing to take: the flag was set by work already served, and the turn that
                    # set it is the one whose take never appeared — this line is where a turn goes missing
                    trace.event("admission.wake", level="debug", channel=channel.name, found=False,
                                chat=channel.queue.qsize(), voice=channel.call_queue.qsize(),
                                work_ready=False)
                    continue
            if job is _STOP:                          # the profile was deleted; this worker has nothing to serve
                source.task_done()
                trace.event("admission.stop", level="debug", channel=channel.name,
                            queue="voice" if source is channel.call_queue else "message",
                            reason="sentinel" if channel.stopping else "unexpected")
                return
            queued_at = float(job.get("queued_at") or 0)
            trace.event("admission.take", level="debug", channel=channel.name,
                        queue="voice" if source is channel.call_queue else "message",
                        turn=channel.turns + 1, mode=job.get("mode") or "chat",
                        waited_s=round(max(0.0, time.time() - queued_at), 3) if queued_at else "-")
            try:
                self.registry.do_turn(channel, job)
            finally:
                source.task_done()

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _wake(channel) -> None:
        """Lift the worker off `wait()` — the flag, not the put, is what it is actually blocked on."""
        channel.work_ready.set()
        if trace.enabled("trace"):
            trace.event("admission.work_ready", level="trace", channel=channel.name, flag=True)

    def _check_capacity(self, name: str, channel, mode: str = "chat") -> None:
        """Cap the queue this turn will actually join: each subchannel has its own backlog."""
        target = self._queue_for(channel, mode)
        if target.qsize() >= self.registry.max_queue:
            label = "voice" if (mode or "chat") == "call" else "message"
            trace.event("admission.refused", level="error", channel=name, queue=label,
                        depth=target.qsize(), max_queue=self.registry.max_queue, rule="queue full")
            raise ValueError(f"{name} {label} queue is full ({self.registry.max_queue} waiting) — "
                             f"try again when it is idle")

    def _mark_queued(self, channel) -> None:
        if channel.state in (channelstate.IDLE, channelstate.ERROR):
            self.registry.set_state(channel, channelstate.QUEUED, pending=channel.queue.qsize())

    def stop(self, channel) -> None:
        """Wake a worker so it can exit — called when a channel is dropped because its profile is gone."""
        # the flag is what the worker checks; the sentinel is also left in both queues so a worker parked in
        # either one wakes up, and one queued behind voice turns is not left to drain them first
        channel.stopping = True
        # a queued caller is waiting on its own done event, so a job that will never run must be answered rather
        # than left to time out and be reported as still queued
        cancelled = 0
        for source in (channel.call_queue, channel.queue):
            while True:
                try:
                    job = source.get_nowait()
                except queue.Empty:
                    break
                if isinstance(job, dict) and job.get("done") is not None:
                    try:
                        job["cancelled"] = True
                        job["note"] = "the channel was closed before this turn ran"
                        job["done"].set()
                        cancelled += 1
                    except Exception:                # noqa: BLE001 — a waiter that has gone is not an error
                        pass
                source.task_done()
        trace.event("admission.stop", level="debug", channel=channel.name, cancelled=cancelled,
                    reason="profile deleted")
        try:
            channel.call_queue.put(_STOP)
            channel.queue.put(_STOP)
            channel.work_ready.set()
        except Exception:                            # noqa: BLE001 — a closed queue means it is already gone
            pass

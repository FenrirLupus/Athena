"""The FIFO request queue — the Operator's ordering + isolation guarantee.

THE 08-15 PARALLEL QUEUE (the Operator's spec): each PROFILE owns its
own lane (a FIFOQueue instance). Within a lane:

  • DISPATCH order = arrival order (oldest → newest) — the queue is FIFO.
  • EXECUTION = PARALLEL — a dynamic pool spawns workers ON DEMAND (up to
    the cap = cores ÷ 2) when requests arrive; workers idle-exit after a
    timeout. The cap is a CEILING, not an allocation — agents/subagents
    run on their own terms (delegation spawns a lane worker the moment a
    call arrives, never a pre-allocated slot).
  • COMMIT order = arrival order (the reorder buffer): a request's result
    is PUBLISHED only when every OLDER request in the lane is done. A
    newer task that finishes early waits for the older ones. No races, no
    reordering — callers poll last_result() and see results in order.

This mirrors the doctrine: serialized results, no races — but the WORK
itself overlaps so one slow provider call never blocks the whole lane.
"""
from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Request:
    kind: str                    # "chat" | "chat_stream" | "tool" | ...
    payload: dict
    created: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    # THE EVENT SINK (the Operator's 08-12 queue spec): for streaming
    # requests (chat_stream), the worker pushes LIVE on_event entries
    # here as the turn runs; the SSE generator reads them as they fire.
    event_sink: "queue.Queue | None" = None

    @property
    def age_ms(self) -> float:
        return (time.time() - self.created) * 1000.0


class FIFOQueue:
    """One lane, strict order, a DYNAMIC parallel worker pool.

    The reorder buffer: requests execute concurrently (up to the cap),
    but their results PUBLISH in arrival order.
    """

    # The idle-exit timeout (the 08-15 spec): a worker with no work for
    # this long exits — the lane shrinks back toward zero when idle.
    IDLE_EXIT_S = 60.0

    def __init__(self, worker: callable, *, interval_s: float = 0.05,
                 max_workers: int | None = None):
        self._q: queue.Queue = queue.Queue()
        self._worker = worker
        self._interval_s = interval_s
        # THE CAP (the CEO's cores rule): max(1, cores // 2) — a CEILING,
        # not an allocation. Workers spawn on demand up to this.
        if max_workers is None:
            max_workers = max(1, (os.cpu_count() or 2) // 2)
        self._max_workers = max(1, int(max_workers))
        self._stop = threading.Event()
        self._processed = 0
        self._pending = 0
        self._lock = threading.Lock()      # pool + commit state
        self._workers: list[threading.Thread] = []   # live workers
        self._active = 0                   # workers currently running
        # THE REORDER BUFFER (the 08-15 spec): request_id -> {done, result}.
        # A result is published only when every OLDER request is done.
        self._order: list[dict] = []       # arrival order: [{id, done, result}]
        self._order_index: dict[str, dict] = {}  # id -> the entry
        self._committed = 0                # how many published (prefix done)
        self._results: dict[str, dict] = {}
        self._results_lock = threading.Lock()

    # -- The pool ------------------------------------------------

    def start(self) -> None:
        """The lane is lazy: workers spawn on demand. No boot threads."""
        self._stop.clear()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            workers = list(self._workers)
            self._workers.clear()
        for w in workers:
            w.join(timeout=2.0)

    def submit(self, kind: str, payload: dict,
               event_sink: "queue.Queue | None" = None) -> str:
        """Enqueue a request. Returns its id. Order = arrival order.

        THE DYNAMIC SPAWN (the 08-15 spec): when a request arrives and no
        worker is free, a NEW worker thread spawns (up to the cap). A
        request beyond the cap waits in the queue (the oldest first).
        """
        req = Request(kind=kind, payload=payload, event_sink=event_sink)
        with self._lock:
            self._pending += 1
            entry = {"id": req.id, "done": False, "result": None}
            self._order.append(entry)
            self._order_index[req.id] = entry
        self._q.put(req)
        self._ensure_worker_locked()
        return req.id

    def _ensure_worker_locked(self) -> None:
        """Spawn a worker if a request is waiting and the pool has room.

        Called with the lock held. A live worker already waiting on the
        queue will grab the request — only spawn when NO idle worker is
        blocked on the queue.
        """
        if self._stop.is_set():
            return
        # Idle workers are the ones NOT currently active. A worker
        # waiting in _run (q.get with timeout) counts as live but idle.
        idle = len(self._workers) - self._active
        if idle > 0:
            return  # an existing worker will pick it up
        if len(self._workers) >= self._max_workers:
            return  # at the cap — the queue waits (FIFO)
        t = threading.Thread(target=self._run, daemon=True,
                             name="fifo-lane")
        self._workers.append(t)
        t.start()

    # -- The worker loop -----------------------------------------

    def _run(self) -> None:
        """One worker: pull the OLDEST request, run it, mark done, then
        commit in order. Idles on an empty queue; EXITS after IDLE_EXIT_S
        of no work (the lane shrinks when idle)."""
        while not self._stop.is_set():
            try:
                req = self._q.get(timeout=self._interval_s)
            except queue.Empty:
                # THE IDLE EXIT (the 08-15 spec): no work for a while →
                # this worker exits; the lane shrinks.
                with self._lock:
                    if len(self._workers) > 1 and self._q.empty():
                        # Another worker is live — this one may exit.
                        try:
                            self._workers.remove(threading.current_thread())
                        except ValueError:
                            pass
                        return
                    if self._q.empty():
                        # The LAST worker stays (the lane is warm) but
                        # honors the idle timeout by checking age.
                        if time.monotonic() - getattr(
                                self, "_last_work", time.monotonic()) > self.IDLE_EXIT_S:
                            try:
                                self._workers.remove(threading.current_thread())
                            except ValueError:
                                pass
                            return
                continue
            self._last_work = time.monotonic()
            with self._lock:
                self._active += 1
            try:
                result = self._worker(req)
            except Exception:
                # A failing request never stalls the lane — it fills its
                # order slot with the error so later requests publish.
                from core.logging import log_event
                log_event(4, f"fifo worker failed on {req.id}",
                          source="platform", action="fifo_worker")
                result = {"ok": False, "error": "fifo worker failed"}
            finally:
                with self._lock:
                    self._active -= 1
                    self._pending = max(0, self._pending - 1)
                    self._processed += 1
                    self._mark_done_locked(req.id, result)
                self._q.task_done()

    # -- The reorder buffer (commit in arrival order) ------------

    def _mark_done_locked(self, req_id: str, result) -> None:
        """Mark a request done + publish results whose order-turn arrived."""
        entry = self._order_index.get(req_id)
        if entry is None:
            return
        entry["done"] = True
        entry["result"] = result
        # Publish every DONE request at the head of the order (the
        # oldest not-yet-published done chain).
        while (self._committed < len(self._order)
               and self._order[self._committed]["done"]):
            e = self._order[self._committed]
            with self._results_lock:
                self._results[e["id"]] = e["result"]
            self._committed += 1

    # -- The result API (callers unchanged) ----------------------

    def set_result(self, req_id: str, result: dict) -> None:
        """Legacy direct set (no ordering) — kept for compatibility."""
        with self._results_lock:
            self._results[req_id] = result

    def last_result(self, req_id: str, default=None) -> dict | None:
        """The completed result for a request, or None if its order-turn
        hasn't arrived yet (the reorder buffer: a fast newer request is
        held until every older request completes)."""
        with self._results_lock:
            return self._results.get(req_id, default)

    def stats(self) -> dict:
        with self._lock:
            return {
                "processed": self._processed,
                "pending": self._pending,
                "queue_size": self._q.qsize(),
                "workers": len(self._workers),
                "active": self._active,
                "cap": self._max_workers,
                "committed": self._committed,
            }

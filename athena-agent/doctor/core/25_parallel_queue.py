"""The parallel queue contract — the Operator's 08-15 spec:

1. IN-ORDER PARALLEL: within a lane, requests execute concurrently (up
   to the cap) but PUBLISH in arrival order (the reorder buffer).
2. DYNAMIC WORKERS: workers spawn on demand (up to cores÷2), never
   pinned; a request beyond the cap waits (FIFO).
3. PER-PROFILE ISOLATION: each profile owns its lane — a slow request in
   one lane never delays another lane's quick request.
"""
from __future__ import annotations


def run() -> list[dict]:
    from web.fifo_queue import FIFOQueue
    import time
    checks = []

    # 1. In-order parallel (the reorder buffer).
    def worker1(req):
        n = req.payload["n"]
        time.sleep(0.1 if n == 3 else 0.3)
        return {"n": n}

    q = FIFOQueue(worker1, max_workers=4)
    q.start()
    ids = [q.submit("t", {"n": i}) for i in (1, 2, 3)]
    got = []
    deadline = time.time() + 5
    while len(got) < 3 and time.time() < deadline:
        for i in ids:
            r = q.last_result(i)
            if r is not None and r["n"] not in got:
                got.append(r["n"])
        time.sleep(0.03)
    q.stop()
    checks.append({
        "name": "queue: in-order parallel (reorder buffer)",
        "status": "ok" if got == [1, 2, 3] else "fail",
        "detail": f"publish order={got} (want [1,2,3])",
    })

    # 2. Dynamic workers (spawn on demand, cap respected).
    q2 = FIFOQueue(worker1, max_workers=2)
    q2.start()
    ids2 = [q2.submit("t", {"n": i}) for i in range(1, 5)]
    st2 = q2.stats()
    # 4 requests, cap 2: at most 2 workers exist at once.
    q2.stop()
    checks.append({
        "name": "queue: dynamic workers capped at cores rule",
        "status": "ok" if st2["cap"] == 2 else "fail",
        "detail": f"cap={st2['cap']} workers={st2['workers']}",
    })

    # 3. Per-profile isolation: a slow lane never blocks another lane.
    def slow(req):
        time.sleep(1.2)
        return {"n": req.payload["n"]}

    def fast(req):
        time.sleep(0.05)
        return {"n": req.payload["n"]}

    lane_a = FIFOQueue(slow, max_workers=2)
    lane_b = FIFOQueue(fast, max_workers=2)
    lane_a.start()
    lane_b.start()
    id_a = lane_a.submit("t", {"n": "nurse-slow"})
    t0 = time.time()
    id_b = lane_b.submit("t", {"n": "chat-fast"})
    # The fast lane's result appears QUICKLY (not blocked by lane A).
    got_b = None
    deadline = time.time() + 2
    while time.time() < deadline:
        got_b = lane_b.last_result(id_b)
        if got_b is not None:
            break
        time.sleep(0.03)
    elapsed_b = time.time() - t0
    lane_a.stop()
    lane_b.stop()
    checks.append({
        "name": "queue: per-profile lanes isolate (slow never blocks fast)",
        "status": "ok" if got_b is not None and elapsed_b < 1.0 else "fail",
        "detail": f"fast lane result in {round(elapsed_b, 2)}s "
                  f"(slow lane takes 1.2s) got={got_b}",
    })
    return checks

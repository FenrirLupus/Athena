"""Two agents, two providers, one multiplexer — fired at the same time.

The question this answers: does per-agent routing actually run concurrently, or does one slow channel
(local model, 18 CPU layers) block a fast one (hosted API)?
"""
import json
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
results = {}


def turn(channel: str, text: str, timeout: int) -> None:
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(f"{BASE}/agents/{channel}/turn", data=body,
                                 headers={"content-type": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        results[channel] = {"status": data.get("status"), "calls": data.get("calls"),
                         "reply": (data.get("reply") or "")[:120],
                         "server_seconds": data.get("seconds"),
                         "wall": round(time.time() - started, 1),
                         "done_at": time.strftime("%H:%M:%S")}
    except Exception as exc:                                  # noqa: BLE001
        results[channel] = {"status": "client_error", "reply": str(exc)[:160],
                         "wall": round(time.time() - started, 1),
                         "done_at": time.strftime("%H:%M:%S")}


threads = [
    threading.Thread(target=turn, args=("default", "reply with exactly: hosted ok", 240)),
    threading.Thread(target=turn, args=("scout", "reply with exactly: local ok", 600)),
]
print("start:", time.strftime("%H:%M:%S"))
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()

print()
for channel in ("default", "scout"):
    row = results.get(channel, {})
    print(f"{channel:<8} {row.get('status'):<12} wall={row.get('wall')}s "
          f"server={row.get('server_seconds')}s calls={row.get('calls')} "
          f"finished={row.get('done_at')}")
    print(f"         reply={row.get('reply')!r}")

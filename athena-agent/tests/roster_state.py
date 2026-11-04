"""Does the prompt show a peer's state, live?

scout runs a slow local model; while it is working, default is asked what the roster says about scout.
If the roster is resolved per iteration (not frozen at turn start), default answers "busy/working".
"""
import json
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8765"


def ask(channel, text, timeout=600):
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(f"{BASE}/agents/{channel}/turn", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def state_of(channel):
    with urllib.request.urlopen(f"{BASE}/agents", timeout=10) as resp:
        for row in json.loads(resp.read())["agents"]:
            if row["channel"] == channel:
                return row["state"]
    return "?"


busy = {}
slow = threading.Thread(target=lambda: busy.update(ask("scout", "count from one to ten, one number per line")))
slow.start()
time.sleep(3)
print("scout state:", state_of("scout"))

answer = ask("default", "According to your instructions, is @scout idle or busy right now? "
                        "Answer in one short sentence, quoting the roster line.")
print("default says:", repr((answer.get("reply") or "")[:220]))
slow.join()
time.sleep(1)
print("scout state after:", state_of("scout"))

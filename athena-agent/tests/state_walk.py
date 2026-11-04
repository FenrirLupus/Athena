"""Watch a channel walk the state machine while two turns are queued back to back.

Proves three things the badge cannot: the state is written to disk, it moves queued → working → idle,
and a second turn that arrives mid-turn is visible as `queued` rather than being invisible.
"""
import json
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
CHANNEL = "scout"          # the slow one: a local model gives us time to observe


def post(text):
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(f"{BASE}/agents/{CHANNEL}/turn", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read())


def state_of(channel=CHANNEL):
    with urllib.request.urlopen(f"{BASE}/agents", timeout=10) as resp:
        for row in json.loads(resp.read())["agents"]:
            if row["channel"] == channel:
                return row.get("state"), row.get("queue_depth")
    return "?", 0


print("before:", state_of())

first = threading.Thread(target=post, args=("reply with: one",))
first.start()
time.sleep(2.5)
print("during turn 1:            ", state_of())

# a second turn arrives WHILE the first runs: both fire-and-forget so the queue is observable
second = threading.Thread(target=post, args=("reply with: two",))
second.start()
time.sleep(1.0)
print("second queued behind it:  ", state_of())

first.join()
second.join()
time.sleep(1.0)
print("after both finished:      ", state_of())

with open(Path(__file__).resolve().parents[1].parent / "profiles" / "scout" / "state.json", encoding="utf-8") as fh:
    print("state.json on disk:", json.load(fh))

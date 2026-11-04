"""A turn that gets interrupted while it is still running.

scout runs a local model slowly, so a second message can arrive mid-turn. Expected:

  turn 1  starts ("say ok"), state working
  message while working → an [interrupt] notice is injected at the next iteration boundary
           and the message itself is queued as the next task
  turn 2  runs after turn 1 finishes, from idle (admission)
"""
import json
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
CHANNEL = "scout"

results = {}


def turn(tag, text):
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(f"{BASE}/agents/{CHANNEL}/turn", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as resp:
        results[tag] = json.loads(resp.read())


def state():
    with urllib.request.urlopen(f"{BASE}/agents", timeout=10) as resp:
        for row in json.loads(resp.read())["agents"]:
            if row["channel"] == CHANNEL:
                return row["state"], row["queue_depth"]
    return "?", 0


first = threading.Thread(target=turn, args=("first", "count slowly from one to five"))
first.start()
time.sleep(3)
print("during turn 1:", state())

second = threading.Thread(target=turn, args=("second", "now name a colour"))
second.start()
time.sleep(2)
print("after the interrupt arrives:", state())

first.join()
second.join()
time.sleep(1)

print("after both:", state())
for tag in ("first", "second"):
    row = results.get(tag, {})
    print(f"{tag:<7} status={row.get('status'):<10} interruptions={row.get('interruptions')} "
          f"calls={row.get('calls')} reply={ (row.get('reply') or '')[:70]!r}")

with open(str(Path(__file__).resolve().parents[1].parent / "profiles" / CHANNEL / "sessions" / "LATEST.jsonl"), encoding="utf-8") as fh:
    lines = [json.loads(line) for line in fh if line.strip()]

print("\ntranscript (last 6 messages):")
for msg in lines[-6:]:
    print(f"  {msg['role']:<9} {repr((msg.get('content') or '')[:110])}")

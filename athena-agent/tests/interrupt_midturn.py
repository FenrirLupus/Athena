"""An interruption that lands INSIDE the running turn.

A single-call turn has no boundary to land on, so the notice is dropped (correctly) rather than handed to
the next task. To see it land mid-turn, the running turn needs a second iteration — a tool round gives it
one. scout runs a local model slowly, so there is time to interject.

  turn 1  "echo exactly: hello"        → call 1 (tool call) … call 2 (answer)
  inject  while call 1 or 2 is running → notice applied before the next call
  expect  turn 1 reports interruptions=1 and the transcript shows the notice in the middle
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


def session_len():
    with open(str(Path(__file__).resolve().parents[1].parent / "profiles" / CHANNEL / "sessions" / "LATEST.jsonl"), encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


start_len = session_len()
first = threading.Thread(target=turn, args=("first", "echo exactly: hello there"))
first.start()
time.sleep(3)
print("during turn 1:", state(), "| messages so far:", session_len() - start_len)

second = threading.Thread(target=turn, args=("second", "then name a colour"))
second.start()
time.sleep(1)
first.join()
second.join()
time.sleep(1)

print("after both:", state())
for tag in ("first", "second"):
    row = results.get(tag, {})
    print(f"{tag:<7} calls={row.get('calls')} interruptions={row.get('interruptions')} "
          f"status={row.get('status')} reply={(row.get('reply') or '')[:60]!r}")

with open(str(Path(__file__).resolve().parents[1].parent / "profiles" / CHANNEL / "sessions" / "LATEST.jsonl"), encoding="utf-8") as fh:
    lines = [json.loads(line) for line in fh if line.strip()]

print("\ntranscript written by this run:")
for msg in lines[start_len:]:
    marker = " <-- interrupt" if msg.get("content", "").startswith("[interrupt]") else ""
    print(f"  {msg['role']:<9} {repr((msg.get('content') or '')[:100])}{marker}")

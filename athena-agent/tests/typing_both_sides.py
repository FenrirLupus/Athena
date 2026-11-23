"""Both indicator lines, observed through a real delegation.

  left  "Default is thinking"        while default works
  right "waiting on Scout"           while default's question is outstanding
  both                               if default is working and waiting at the same time
"""
import json
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8765"


def turn(channel, text, timeout=900):
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(f"{BASE}/agents/{channel}/turn", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def row(channel):
    with urllib.request.urlopen(f"{BASE}/agents", timeout=10) as resp:
        for item in json.loads(resp.read())["agents"]:
            if item["channel"] == channel:
                return item
    return {}


slow = threading.Thread(target=lambda: turn(
    "default", "Use the @ syntax to ask another agent to reply with exactly the token WAVE42."))
slow.start()
time.sleep(4)
print("while default works  → default:", row("default")["state"],
      "| awaiting:", row("default").get("awaiting"))
slow.join()
time.sleep(2)
print("after default's turn → default:", row("default")["state"],
      "| awaiting:", row("default").get("awaiting"))

# the peer is now working on it; default should still be waiting
deadline = time.time() + 240
seen = []
while time.time() < deadline:
    state = row("default")
    snapshot = (state["state"], tuple(state.get("awaiting") or []))
    if not seen or seen[-1] != snapshot:
        seen.append(snapshot)
    if not state.get("awaiting"):
        break
    time.sleep(2)

for item in seen:
    print("  observed:", item)
print("scout:", row("scout")["state"], "| default awaiting cleared:", not row("default")["awaiting"])

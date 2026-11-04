"""The two sides, from the RECEIVER's point of view.

operator → default → @scout

  default's pane, while composing    LEFT   "Default is thinking"
                                     RIGHT  (nothing yet — it has not asked anyone)
  scout's pane, once the message      LEFT   "Scout is thinking"
  arrived                             RIGHT  "Default is responding …"     ← inbound names the sender
  default's pane, after its turn      RIGHT  "waiting on Scout …"
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


threading.Thread(target=lambda: turn(
    "default", "Use the @ syntax to ask another agent to reply with exactly SIDES1.")).start()

seen = []
deadline = time.time() + 180
while time.time() < deadline:
    d, s = row("default"), row("scout")
    snap = {
        "default": (d["state"], d.get("awaiting") or [], d.get("inbound_from") or ""),
        "scout": (s["state"], s.get("awaiting") or [], s.get("inbound_from") or ""),
    }
    if not seen or seen[-1] != snap:
        seen.append(snap)
    if d["state"] == "idle" and s["state"] == "idle" and not d.get("awaiting"):
        break
    time.sleep(2)

for snap in seen:
    d, s = snap["default"], snap["scout"]
    print(f"default state={d[0]:<8} awaiting={d[1]} inbound={d[2] or '-'}")
    print(f"scout   state={s[0]:<8} awaiting={s[1]} inbound={s[2] or '-'}")
    print()

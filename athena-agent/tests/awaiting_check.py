"""Does `awaiting` track pending asks correctly — including two at once?

The first version stored a peer as a flag, so a second ask to the same peer was invisible: default could be
waiting while showing nothing. It now counts unanswered asks and exposes one entry per ask.
"""
import json
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
checks = {}


def post(path, payload=None, method="POST", timeout=600):
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def rows():
    with urllib.request.urlopen(f"{BASE}/agents", timeout=10) as resp:
        return {a["channel"]: a for a in json.loads(resp.read())["agents"]}


for channel in ("default", "scout"):
    post(f"/agents/{channel}/clear")

print("start:", {name: (r["state"], r["awaiting"]) for name, r in rows().items()})

# one delegation: default asks scout, and the ask must be visible until the answer lands
threading.Thread(target=lambda: post("/agents/default/turn",
                                     {"text": "Use the @ syntax to ask another agent to reply with "
                                              "exactly ASK1."})).start()

seen_waiting = False
deadline = time.time() + 200
while time.time() < deadline:
    table = rows()
    if table["default"]["awaiting"]:
        seen_waiting = True
        print("while unanswered -> default.awaiting:", table["default"]["awaiting"],
              "| scout.inbound:", table["scout"]["inbound_from"] or "-")
        break
    time.sleep(2)

# wait for it all to settle
deadline = time.time() + 240
while time.time() < deadline:
    table = rows()
    if table["default"]["state"] == "idle" and table["scout"]["state"] == "idle" \
            and not table["default"]["awaiting"]:
        break
    time.sleep(3)

final = rows()
checks["asked_was_visible"] = seen_waiting
checks["cleared_after_answer"] = not final["default"]["awaiting"]
checks["both_idle"] = final["default"]["state"] == "idle" and final["scout"]["state"] == "idle"
checks["no_stale_inbound"] = not final["scout"]["inbound_from"]

print("\nfinal:", {name: (r["state"], r["awaiting"], r["inbound_from"] or "-")
                   for name, r in final.items()})
for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

"""What does default's prompt say about scout WHILE scout is working?

Ground truth only: read the system block the server assembled, not the model's account of it.
"""
import json
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8765"


def turn(channel, text, timeout=600):
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(f"{BASE}/agents/{channel}/turn", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def system_of(channel):
    with urllib.request.urlopen(f"{BASE}/agents/{channel}/resources", timeout=30) as resp:
        return json.loads(resp.read()).get("system_prompt") or ""


def roster_line(prompt):
    marker = "Peers right now:"
    return prompt.split(marker, 1)[1].split("\n", 1)[0].strip() if marker in prompt else "(absent)"


slow = threading.Thread(target=lambda: turn("scout", "count to eight, one per line"))
slow.start()
time.sleep(3)

turn("default", "say ok")
print("while scout is busy → default's roster:", roster_line(system_of("default")))

slow.join()
time.sleep(1)
turn("default", "say ok")
print("after scout is free  → default's roster:", roster_line(system_of("default")))

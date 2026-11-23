"""Watch the typing indicator appear, wave, and stop — driven by the channel's real state."""
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


slow = threading.Thread(target=lambda: turn("scout", "count from one to six, one per line"))
slow.start()
time.sleep(4)
print("turn running — inspect the page now")
slow.join()
print("turn finished")

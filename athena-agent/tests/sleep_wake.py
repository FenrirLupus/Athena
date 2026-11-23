"""Sleep: parked channels wake on delivery, and an explicit wake verifies.

Covers the three paths that matter:
  1 · a message to a sleeping channel wakes it and runs           (delivery is the wake signal)
  2 · an explicit wake verifies channel / provider / model        (and lands in idle)
  3 · a mention from another agent wakes a sleeping peer          (agents get the same treatment)
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"
checks = {}


def call(path, payload=None, method="POST", timeout=300):
    data = json.dumps(payload).encode() if payload is not None else b"{}"
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body or b"{}")
        except json.JSONDecodeError:
            return exc.code, {"detail": body.decode("utf-8", "replace")[:200]}


def state_of(channel):
    with urllib.request.urlopen(f"{BASE}/agents", timeout=10) as resp:
        for row in json.loads(resp.read())["channels"]:
            if row["channel"] == channel:
                return row["state"]
    return "?"


def wait_until(predicate, seconds=150):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(2)
    return False


for channel in ("default", "scout"):
    call(f"/agents/{channel}/clear")

# ---- 1 · delivery wakes ---------------------------------------------------
status, body = call("/agents/scout/sleep")
checks["sleep_accepts"] = status == 200 and body.get("state") == "sleeping"
checks["reports_sleeping"] = state_of("scout") == "sleeping"

status, body = call("/agents/scout/turn", {"text": "say ok"})
print("turn sent to a sleeping channel:", status, body.get("status"), repr((body.get("reply") or "")[:30]))
checks["delivery_wakes_and_runs"] = status == 200 and body.get("status") == "completed"
checks["idle_after_delivery"] = wait_until(lambda: state_of("scout") == "idle")

# ---- 2 · explicit wake verifies ------------------------------------------
call("/agents/scout/sleep")
status, body = call("/agents/scout/wake")
print("explicit wake:", status, "awake:", body.get("awake"), "state:", body.get("state"))
for name, item in (body.get("checks") or {}).items():
    print(f"   {name:<9} {'ok' if item['ok'] else 'FAILED'}  {item['detail']}")
checks["wake_checks_present"] = set((body.get("checks") or {})) == {"channel", "provider", "model"}
checks["wake_verifies"] = body.get("awake") is True
checks["wake_reports_idle"] = body.get("state") == "idle"

# ---- 3 · a mention wakes a sleeping peer ---------------------------------
call("/agents/scout/clear")
call("/agents/scout/sleep")
checks["peer_starts_asleep"] = state_of("scout") == "sleeping"

status, body = call("/agents/default/turn",
                    {"text": "Use the @ syntax to ask another agent to reply with exactly WAKE3."})
print("default mention while peer sleeps:", status, repr((body.get("reply") or "")[:60]))


def peer_got_it():
    with urllib.request.urlopen(f"{BASE}/agents/scout/messages?limit=20", timeout=20) as resp:
        messages = json.loads(resp.read())["messages"]
    return any(m["role"] == "user" and m.get("author") == "default" for m in messages)


checks["mention_woke_peer"] = wait_until(peer_got_it)
checks["peer_awake_after_mention"] = state_of("scout") in ("idle", "working", "queued")

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

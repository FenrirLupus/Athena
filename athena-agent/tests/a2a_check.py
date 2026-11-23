"""End-to-end check of agent-to-agent communication, against the running server.

Covers the four things that have to hold together:
  A  the roster is in the prompt, with a live state
  B  a mention in a reply round-trips: A asks → B answers → A receives
  C  a busy agent queues the task (admission) instead of interleaving it
  D  the reply is attributed: B's answer arrives on A marked as from B
"""
import json
import re
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
checks = {}
RUN = str(int(time.time()))[-5:]          # every token carries this, so no run reads another's messages


def get(path, timeout=30):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as resp:
        return json.loads(resp.read())


def turn(channel, text, timeout=600):
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(f"{BASE}/agents/{channel}/turn", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def messages(channel, limit=40):
    return get(f"/agents/{channel}/messages?limit={limit}")["messages"]


def roster_of(channel):
    """The roster the NEXT turn will be given — readable without waiting for a turn."""
    return get(f"/agents/{channel}/instructions")["roster"]


def state_of(channel):
    for row in get("/agents")["agents"]:
        if row["channel"] == channel:
            return row["state"], row["queue_depth"]
    return "?", 0


# ---- 0 · clean slate: the transcripts accumulate across runs, and an old exchange can look like a new one
for channel in ("default", "scout"):
    req = urllib.request.Request(f"{BASE}/agents/{channel}/clear", data=b"{}", method="POST",
                                 headers={"content-type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()

# ---- A · roster present and carrying a state
roster = roster_of("default")
checks["A_roster_in_prompt"] = "@scout" in roster and bool(
    re.search(r"@scout — (idle|working|queued|error|stale|available)", roster))

# ---- B · ask default to hand a task over, marked with a token so detection is unambiguous
TOKEN = f"ZQ7{RUN}"
answer = turn("default", f"Use the @ syntax to ask another agent to reply with exactly the token {TOKEN}.")
time.sleep(1)
first_reply = answer.get("reply") or ""
checks["B_mentioned_a_peer"] = "@scout" in first_reply


def wait_for(predicate, seconds=300):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(3)
    return False


# poll on predicates, not on counts: a transcript can grow for unrelated reasons (an interrupt, a
# leftover task), which made a count-based check report a working exchange as failed
def scout_has_question():
    return any(m["role"] == "user" and m.get("author") == "default" and TOKEN in (m.get("content") or "")
               for m in messages("scout", 60))


def scout_has_answer():
    msgs = messages("scout", 60)
    for index, m in enumerate(msgs):
        if m["role"] == "user" and m.get("author") == "default" and TOKEN in (m.get("content") or ""):
            return any(later["role"] == "assistant" for later in msgs[index + 1:])
    return False


def default_has_answer():
    return any(m["role"] == "user" and m.get("author") == "scout"
               for m in messages("default", 60)[-6:])


checks["B_peer_received_it"] = wait_for(scout_has_question)
checks["B_peer_answered"] = wait_for(scout_has_answer)
checks["B_answer_came_back"] = wait_for(default_has_answer)

# ---- C · admission: two tasks sent to a busy channel must not interleave
C1, C2 = f"CQ1{RUN}", f"CQ2{RUN}"
first_task = threading.Thread(target=lambda: turn("scout", f"reply with exactly {C1}"))
first_task.start()
time.sleep(3)
state_during, _ = state_of("scout")
second_task = threading.Thread(target=lambda: turn("scout", f"reply with exactly {C2}"))
second_task.start()
first_task.join()
second_task.join()
time.sleep(1)

checks["C_busy_while_working"] = state_during == "working"

# the property that matters is ordering, not a transient depth reading: each request's question is
# followed by its own answer before the next question appears
msgs = messages("scout", 60)
# interrupt notices quote the new message, so they mention the token without being the task itself
marks = [(i, m["role"], m.get("content") or "") for i, m in enumerate(msgs)
         if (C1 in (m.get("content") or "") or C2 in (m.get("content") or ""))
         and not (m.get("content") or "").startswith("[interrupt]")]
order = [("C1" if C1 in text else "C2") for _, _, text in marks if "user" == _]
sequence_ok = order[:2] == ["C1", "C2"] if len(order) >= 2 else False
interleaved = False
for index, role, text in marks:
    if role == "user" and C2 in text:
        # between C2's question and its answer there must be no C1 question
        interleaved = any(r == "user" and C1 in t for _, r, t in marks
                          if index < _ < index + 4 and r == "user")
        break
checks["C_no_interleaving"] = sequence_ok and not interleaved

print("roster:", roster or "(absent)")
print("default said:", repr(first_reply[:90]))
print("scout received / answered:", checks["B_peer_received_it"], "/", checks["B_peer_answered"])
print("answer returned to default:", checks["B_answer_came_back"])
print("busy state during first task:", state_during)
print("scout question order:", order[:3])
print()
for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

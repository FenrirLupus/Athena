"""Attachment round trip: upload → manifest in the turn → the agent reads it with read_file."""
import json
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8765"


def post_json(path, payload, timeout=600):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=data,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def upload(channel, name, content: bytes):
    req = urllib.request.Request(
        f"{BASE}/agents/{channel}/attach", data=content, method="POST",
        headers={"X-Filename": urllib.parse.quote(name), "Content-Type": "text/plain"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


post_json("/agents/default/clear", {})
info = upload("default", "retry-notes.txt", b"the retry limit is 3, then give up\n")
print("uploaded:", info["path"])

manifest = (f"Read the attached file and tell me the retry limit.\n\n"
            f"[attached files — open one with read_file(path)]\n"
            f"- {info['name']} → {info['path']} ({info['bytes']} bytes)")
result = post_json("/agents/default/turn", {"text": manifest})
print("status:", result.get("status"), "| calls:", result.get("calls"),
      "| tool rounds:", result.get("tool_rounds"))
print("reply:", repr((result.get("reply") or "")[:160]))

with urllib.request.urlopen(f"{BASE}/agents/default/messages?limit=6", timeout=20) as resp:
    messages = json.loads(resp.read())["messages"]
print("\ntranscript:")
for message in messages[-4:]:
    print(f"  {message['role']:<9} {message.get('tool_calls') or ''} {repr((message['content'] or '')[:110])}")

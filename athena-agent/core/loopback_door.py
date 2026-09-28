"""Loopback door — the parent↔child HTTP bridge.

The Operator's architecture: the SERVER (parent) is the only outward-facing
gateway. Each profile runtime (child) listens on a loopback-only HTTP
port; the parent routes platform messages to the right child by
POSTing to its door. Nothing is exposed beyond 127.0.0.1.

Port scheme: 127.0.0.1:84<2-digit hash of the profile name> — stable
per profile, collision-resistant for the handful of profiles.
"""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request

BASE_PORT = 8400
PORT_SPAN = 100


def _log(level: int, msg: str, source: str = "loopback") -> None:
    """The door logs its failures — delivery errors are system events."""
    try:
        from metrics.logger import log
        log(level, msg, source=source)
    except Exception:
        pass


def door_port(profile: str) -> int:
    """A stable loopback port for a profile (8400 + hash mod 100)."""
    digest = hashlib.sha256(profile.encode("utf-8")).hexdigest()
    return BASE_PORT + (int(digest[:8], 16) % PORT_SPAN)


class _DoorHandler(BaseHTTPRequestHandler):
    runtime = None  # set by the child

    def do_POST(self):  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.runtime is not None:
                ack = self.runtime.handle_event(payload)
            else:
                ack = {"ok": False, "detail": "no runtime"}
            body = json.dumps(ack).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            _log(3, f"door event failed: {exc}", source="loopback")
            body = json.dumps({"ok": False, "detail": str(exc)}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802 — health probe
        body = json.dumps({"ok": True, "profile": getattr(
            self.runtime, "profile", None) and getattr(
            self.runtime.profile, "name", "")}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the child quiet
        pass


def start_door(runtime, profile: str) -> threading.Thread:
    """Start the child's loopback listener on its own thread."""
    _DoorHandler.runtime = runtime
    server = ThreadingHTTPServer(("127.0.0.1", door_port(profile)),
                                 _DoorHandler)
    t = threading.Thread(target=server.serve_forever,
                         daemon=True, name=f"door-{profile}")
    t.start()
    return t


def post_event(profile: str, event: dict, timeout: float = 10.0) -> dict:
    """PARENT side: deliver an event to a child's door. Returns the ack."""
    port = door_port(profile)
    body = json.dumps(event).encode("utf-8")
    req = request.Request(
        f"http://127.0.0.1:{port}/",
        data=body, headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def child_alive(profile: str, timeout: float = 2.0) -> bool:
    """PARENT side: is the child's door answering (health probe)?"""
    port = door_port(profile)
    try:
        with request.urlopen(
                f"http://127.0.0.1:{port}/", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False

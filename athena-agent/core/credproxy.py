"""credproxy — the process that holds the keys, so the server does not have to.

The server signs provider requests today: it reads `.secret`, puts the key in a header, and sends it. Anything running
inside that process — a tool, a plugin, a future bug — can therefore read the key. This moves the trust boundary:

    before   server ──(key in header)──▶ api.deepseek.com
    after    server ──(no key)──▶ credproxy ──(key in header)──▶ api.deepseek.com

The proxy runs as a **separate process**. It reads `.secret` itself, validates that the destination is on an
allowlist, injects the right key for that host, and forwards. It never returns a key to the caller — the only thing it
will do with one is spend it on an allowlisted host.

How the server reaches it: loopback port, plus a token generated at start and held only in the two processes' memory.
Without the token the proxy refuses, so another local process cannot use it as a key-spending relay.

Run as: `python3 -m core.credproxy --athena <root> --allow api.deepseek.com` → prints `{"port":…, "token":…}`.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from core.jsonio import read_json
from core import trace

MAX_BODY = 8 * 1024 * 1024


def _provider_map(athena_root: Path) -> dict:
    """host → key name, from provider.json when it exists, else from the provider's own name."""
    mapping: dict[str, str] = {}
    catalogue = athena_root / "provider.json"
    data = read_json(catalogue, None)
    if data is None:
        return mapping
    for name, entry in (data or {}).items():
        base = str((entry or {}).get("base_url") or "")
        # strip the port: the request side compares bare hosts, and "127.0.0.1:8080" != "127.0.0.1" was a real
        # miss that made the proxy report "no key ... (looked for any)"
        host = base.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
        keys = [k for k in (entry or {}).get("keys") or [] if k.get("name")]
        if host and keys:
            mapping[host] = str(keys[0]["name"])
    return mapping


def _secret_value(athena_root: Path, name: str) -> str:
    """Read a key from `.secret` — the whole point of this process existing."""
    for candidate in (athena_root / ".secret",):
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == name:
                    return value.strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


class Handler(BaseHTTPRequestHandler):
    server_version = "athena-credproxy"
    sys_version = ""

    def log_message(self, fmt, *args):                # never log the Authorization value
        if self.server.verbose:                       # noqa: SLF001
            sys.stderr.write("credproxy: " + (fmt % args) + "\n")

    def do_POST(self):                                # noqa: N802 — BaseHTTPRequestHandler's contract
        self._handle()

    def do_GET(self):                                 # noqa: N802
        self._handle()

    def _fail(self, status: int, detail: str) -> None:
        body = json.dumps({"error": {"message": detail, "type": "credproxy"}}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        cfg = self.server.settings                        # type: ignore[attr-defined]
        if self.headers.get("x-athena-proxy") != cfg["token"]:
            trace.event("cred.refused", level="error", rule="proxy-token", status=401, path=self.path)
            self._fail(401, "credproxy: missing or wrong proxy token")
            return

        upstream = (self.headers.get("x-athena-upstream") or "").strip()
        if not upstream:
            trace.event("cred.refused", level="error", rule="no-upstream", status=400, path=self.path)
            self._fail(400, "credproxy: x-athena-upstream header is required")
            return
        if not upstream.startswith("https://") and not upstream.startswith("http://127.0.0.1"):
            trace.event("cred.refused", level="error", rule="scheme", status=403,
                        upstream=upstream[:160])
            self._fail(403, "credproxy: only https upstreams (or loopback) are allowed")
            return

        host = upstream.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
        if host not in cfg["allow"]:
            trace.event("cred.refused", level="error", rule="allowlist", status=403, host=str(host))
            self._fail(403, f"credproxy: {host} is not on the allowlist")
            return

        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY:
            trace.event("cred.refused", level="error", rule="body-too-large", status=413,
                        host=str(host), bytes=length)
            self._fail(413, "credproxy: request body too large")
            return
        body = self.rfile.read(length) if length else b""

        key_name = cfg["providers"].get(host) or cfg["fallback_key"]
        key = cfg["secret"](key_name) if key_name else ""
        if not key:
            known = ", ".join(sorted(cfg["providers"])) or "none"
            # the names, never the values: which key was looked for is the diagnosis, the value is the secret
            trace.event("cred.refused", level="error", rule="no-key", status=500, provider=str(host),
                        key_name=str(key_name or "any"), known=known[:200])
            self._fail(500, f"credproxy: no key for {host} (looked for {key_name or 'any'}; "
                            f"known hosts: {known})")
            return
        # the one line that proves a key reached the resolver without the key appearing anywhere: its name and its
        # length, which is enough to see a key was found and not enough to reconstruct any part of it
        trace.event("cred.key", level="info", provider=str(host), key_name=str(key_name),
                    found=True, bytes=len(key))

        request = urllib.request.Request(upstream, data=body, method=self.command,
                                         headers={"content-type": self.headers.get("content-type",
                                                                                   "application/json"),
                                                  "authorization": f"Bearer {key}",
                                                  "accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=cfg["timeout"]) as response:
                payload = response.read()
                status = response.status
                content_type = response.headers.get("content-type", "application/json")
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            status = exc.code
            content_type = exc.headers.get("content-type", "application/json") if exc.headers else "application/json"
        except Exception as exc:                       # noqa: BLE001 — reported as an HTTP error, not a traceback
            trace.failure("cred.upstream", exc, host=str(host), method=self.command)
            self._fail(502, f"credproxy: upstream failed: {type(exc).__name__}: {exc}")
            return

        trace.event("cred.upstream", level="debug", host=str(host), status=status, bytes=len(payload))
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _die_with_parent(interval: float = 2.0) -> None:
    """Exit when the server that spawned us is gone.

    A proxy is only useful to the process that started it, and it holds the keys. Orphaned by a restart, it kept
    listening — five of them accumulated over one session, each still holding credentials in memory.
    """
    import os
    import threading

    parent = os.getppid()

    def watch() -> None:
        while True:
            if os.getppid() != parent:
                os._exit(0)                        # reparented to init: the server is gone
            time.sleep(interval)

    threading.Thread(target=watch, daemon=True, name="credproxy-parent-watch").start()


def serve(athena_root: Path, allow: list[str], fallback_key: str = "", timeout: float = 180.0,
          verbose: bool = False) -> None:
    import secrets

    athena_root = Path(athena_root)
    settings = {"token": secrets.token_urlsafe(32),
                "allow": set(allow),
                "providers": _provider_map(athena_root),
                "fallback_key": fallback_key,
                "timeout": float(timeout),
                "secret": lambda name: _secret_value(athena_root, name)}
    _die_with_parent()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.settings = settings                            # type: ignore[attr-defined]
    httpd.verbose = verbose                              # type: ignore[attr-defined]
    # the port, the hosts it will spend a key on, and how many it knows — never the token or a key
    trace.event("cred.listen", level="info", port=httpd.server_address[1], allow=sorted(settings["allow"]),
                providers=len(settings["providers"]), fallback=bool(fallback_key))
    print(json.dumps({"port": httpd.server_address[1], "token": settings["token"],
                      "allow": sorted(settings["allow"]), "providers": settings["providers"]}), flush=True)
    httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    athena = Path.home() / ".athena"
    allow: list[str] = []
    fallback = ""
    while argv:
        flag = argv.pop(0)
        if flag == "--athena" and argv:
            athena = Path(argv.pop(0))
        elif flag == "--allow" and argv:
            allow.append(argv.pop(0))
        elif flag == "--fallback-key" and argv:
            fallback = argv.pop(0)
        elif flag == "--verbose":
            serve(athena, allow, fallback, verbose=True)
            return 0
    serve(athena, allow, fallback)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Network defence for the multiplexer: who knocked, and where did we reach?

The two teams, applied to sockets:

    BLUE TEAM   inbound. Someone connecting *to* the server: probing paths that should stay local, failing auth,
                flooding, sending a body that is obviously not a request. Handled at the door, before the route runs.
    RED TEAM    outbound. Something *inside* — a tool, a plugin, a provider adapter — opening a connection that the
                architecture did not intend. Handled by auditing the process's own established sockets against an
                allowlist, because an agent has no business reaching an arbitrary host.

Neither is a firewall. They are observers with a veto at one point: the door. Everything else is evidence, and the
agents get told about it through the same report the disk watchdog writes.

Loopback is the default posture. The server binds `127.0.0.1`, and the middleware refuses sensitive paths from any
non-loopback client even if someone later runs it on `0.0.0.0` — a setting is not a promise.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from collections import defaultdict, deque
from pathlib import Path

SENSITIVE_PREFIXES = ("/security", "/config", "/approvals", "/agents", "/metrics", "/docs")
# Served without a token so the page can hand the browser its cookie. Content only — no state, no transcript.
PUBLIC_PREFIXES = ("/", "/index.html", "/html/", "/js/", "/css/", "/favicon", "/health")
COOKIE_NAME = "athena_session"
LOOPBACK_NAMES = {"127.0.0.1", "::1", "localhost", "testclient", ""}


def _is_loopback(host: str) -> bool:
    if not host or host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return host in LOOPBACK_NAMES


class RateLimiter:
    """A sliding window per client. Small, because the only client that should exist is the user."""

    def __init__(self, per_minute: int = 240):
        self.per_minute = max(10, int(per_minute))
        self.window: dict[str, deque] = defaultdict(deque)

    def hit(self, client: str) -> bool:
        """True when the client is over the limit (and has been recorded as such)."""
        now = time.time()
        bucket = self.window[client]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        bucket.append(now)
        return len(bucket) > self.per_minute

    def count(self, client: str) -> int:
        return len(self.window.get(client, ()))


class InboundGuard:
    """ASGI middleware: classify every request, veto the ones that should never arrive."""

    def __init__(self, app, ledger, *, loopback_only: bool = True, rate_per_minute: int = 240,
                 max_body: int = 25 * 1024 * 1024, token: str = "", require_token: bool = False):
        self.app = app
        self.ledger = ledger
        self.loopback_only = loopback_only
        self.limiter = RateLimiter(rate_per_minute)
        self.max_body = max_body
        self.token = token
        # With a token configured, every request must present it — except the static shell, which is how the browser
        # obtains the cookie in the first place. This is what stops another local process from driving /agents.
        self.require_token = bool(require_token and token)

    def _sensitive(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in SENSITIVE_PREFIXES)

    def _public(self, path: str) -> bool:
        return path == "/" or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES if prefix != "/")

    def _is_loopback_path(self, path: str) -> bool:
        """Only the page shell earns a cookie — a deep link does not."""
        return path == "/" or path.endswith(".html")

    def _presented_token(self, headers: dict) -> str:
        direct = headers.get("x-athena-token") or ""
        if direct:
            return direct
        for part in (headers.get("cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME:
                return value
        return ""

    def evaluate(self, client: str, path: str, headers: dict) -> tuple[int, str] | None:
        """Return (status, reason) to refuse, or None to let it through."""
        if self.limiter.hit(client):
            return 429, f"rate limit exceeded ({self.limiter.count(client)} in 60s)"
        if not _is_loopback(client):
            if self.loopback_only and self._sensitive(path):
                return 403, f"non-loopback client {client} requested a local-only path"
            if self.token and headers.get("authorization") != f"Bearer {self.token}":
                return 401, f"non-loopback client {client} did not present the API token"

        if self.require_token and not self._public(path):
            if self._presented_token(headers) != self.token:
                return 401, "missing or wrong API token (set it as a cookie or X-Athena-Token header)"
        return None

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        client = (scope.get("client") or ("", 0))[0]
        path = scope.get("path") or ""
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in (scope.get("headers") or [])}

        verdict = self.evaluate(str(client), path, headers)
        if verdict is not None:
            status, reason = verdict
            self.ledger.record("blue", f"inbound-{status}", reason, path, "")
            body = f'{{"detail":"{reason}"}}'.encode()
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        captured = {"status": 0}
        hand_out_cookie = self.require_token and self._public(path) and self._is_loopback_path(path)

        async def watching_send(message):
            if message["type"] == "http.response.start":
                captured["status"] = message.get("status", 0)
                if hand_out_cookie:
                    headers = list(message.get("headers") or [])
                    headers.append((b"set-cookie",
                                    f"{COOKIE_NAME}={self.token}; Path=/; HttpOnly; SameSite=Strict".encode()))
                    message = dict(message, headers=headers)
            await send(message)

        await self.app(scope, receive, watching_send)

        status = captured["status"]
        if status in (401, 403, 429):
            self.ledger.record("blue", f"inbound-{status}", f"{scope.get('method')} {path}", path, "")
        elif status >= 500:
            self.ledger.record("red", "inbound-500", f"{scope.get('method')} {path} failed", path, "")


def _hex_to_ip(text: str) -> str:
    """`/proc/net/tcp` writes addresses as little-endian hex."""
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        return "?"
    if len(raw) == 4:
        return ".".join(str(part) for part in raw[::-1])
    if len(raw) == 16:
        groups = [raw[i:i + 4][::-1].hex() for i in range(0, 16, 4)]
        return ":".join(groups)
    return "?"


def own_inodes(pid: int | None = None) -> set[str]:
    """Socket inodes held by *this* process.

    `/proc/net/tcp` is the whole network namespace — in a container that means the host's sockets too, so without
    this filter every unrelated connection (a browser, a chat client) is reported as "the architecture reached out".
    That is how a watchdog earns the ignore list.
    """
    import os

    target = int(pid or os.getpid())
    found: set[str] = set()
    folder = Path(f"/proc/{target}/fd")
    try:
        for entry in folder.iterdir():
            try:
                link = os.readlink(entry)
            except OSError:
                continue
            if link.startswith("socket:["):
                found.add(link[8:-1])
    except OSError:
        pass
    return found


def established(proc_net: Path, inodes: set[str] | None = None) -> list[dict]:
    """Established TCP endpoints from a `/proc/net/tcp`-shaped file (st == 01)."""
    rows = []
    try:
        lines = proc_net.read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return rows
    for line in lines:
        parts = line.split()
        if len(parts) < 10 or parts[3] != "01":
            continue
        if inodes is not None and parts[9] not in inodes:
            continue
        try:
            local_ip, local_port = parts[1].split(":")
            remote_ip, remote_port = parts[2].split(":")
        except ValueError:
            continue
        rows.append({"local": f"{_hex_to_ip(local_ip)}:{int(local_port, 16)}",
                     "remote_ip": _hex_to_ip(remote_ip), "remote_port": int(remote_port, 16),
                     "inode": parts[9]})
    return rows


def _allowed(ip: str, allow_hosts: set[str], allow_ips: set[str]) -> bool:
    if not ip or ip in allow_ips:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    return ip in allow_hosts


def outbound_findings(ledger, allow: list[str] | None = None,
                      proc_net_paths: tuple[str, ...] = ("/proc/net/tcp", "/proc/net/tcp6"),
                      inodes: set[str] | None = None) -> list[dict]:
    """Anything the process is connected to that is not local and not on the allowlist."""
    allow = allow or []
    allow_ips: set[str] = set()
    for host in allow:
        try:
            for info in socket.getaddrinfo(host, None):
                allow_ips.add(info[4][0])
        except (socket.gaierror, OSError):
            continue

    findings = []
    seen = set()
    own = own_inodes() if inodes is None else inodes
    for path_text in proc_net_paths:
        for row in established(Path(path_text), own):
            key = (row["remote_ip"], row["remote_port"])
            if key in seen:
                continue
            seen.add(key)
            if _allowed(row["remote_ip"], allow_ips, {"127.0.0.1", "::1"}):
                continue
            findings.append({
                "severity": "high", "team": "red", "kind": "outbound-unexpected",
                "path": f"{row['remote_ip']}:{row['remote_port']}",
                "detail": (f"the process holds an established connection to {row['remote_ip']}:"
                           f"{row['remote_port']}, which is not loopback and not on the allowlist"),
                "guidance": ("An outbound connection the architecture did not plan. Do not repeat the action that "
                             "opened it, do not send anything through it, and tell the user the address. If it came "
                             "from a tool you were asked to run, say which."),
            })
            ledger.record("red", "outbound-unexpected",
                          f"established to {row['remote_ip']}:{row['remote_port']}",
                          f"{row['remote_ip']}:{row['remote_port']}", "")
    return findings


def token_path(athena_root) -> Path:
    from pathlib import Path as _Path

    return _Path(athena_root) / "security" / "api_token"


def ensure_token(athena_root, rotate: bool = False) -> str:
    """Read the API token, generating one on first boot. Mode 0600: other users cannot read it.

    This is not protection against a process running as *you* — nothing in user space is. It is protection against
    another user on the machine (loopback is reachable across users), against an app that scans localhost, and against
    a page in a browser that is not the UI.
    """
    import os
    import secrets

    path = token_path(athena_root)
    if path.is_file() and not rotate:
        try:
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(32)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value

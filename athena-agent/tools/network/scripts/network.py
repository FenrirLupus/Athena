"""Built-in network tool — named network connections (one tool).

The Operator's 08-12 spec: a Network tool that manages connections to
networks — each network is a custom URL or IP address, with its own
optional credentials. Other tools (like terminal) can target a network
BY NAME — the registry resolves to the URL/IP (+ credentials) so the
agent doesn't re-type connection details.

A plain JSONL registry under the profile's runtime dir. Credentials are
stored in the registry (never echoed back in full).
"""

import json
import socket
from pathlib import Path


def _net_path(profile: str = "") -> Path:
    from core.config import ATHENA_ROOT
    p = ATHENA_ROOT / "profiles" / (profile or ".default") / "runtime" / "networks.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load(path: Path) -> list[dict]:
    nets = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    nets.append(json.loads(line))
                except Exception:
                    continue
    return nets


def _save(path: Path, nets: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for n in nets:
            f.write(json.dumps(n, ensure_ascii=False) + "\n")


def _redact(net: dict) -> dict:
    """Return the network WITHOUT the credential value (only a flag)."""
    out = dict(net)
    if out.get("credential"):
        out["has_credential"] = True
        out.pop("credential", None)
    return out


def _add(args: dict, timeout: float = 10.0) -> str:
    path = _net_path(args.get("profile", ""))
    name = str(args.get("name", "")).strip()
    url = str(args.get("url", "")).strip()
    if not name or not url:
        return json.dumps({"ok": False, "detail": "name and url/ip required"},
                          ensure_ascii=False)
    nets = _load(path)
    if any(n.get("name") == name for n in nets):
        return json.dumps({"ok": False, "detail": f"network exists: {name}"},
                          ensure_ascii=False)
    net = {
        "name": name,
        "url": url,
        "kind": str(args.get("kind", "http")).strip() or "http",
        "credential": str(args.get("credential", "")).strip() or "",
        "user": str(args.get("user", "")).strip() or "",
        "note": str(args.get("note", "")).strip() or "",
    }
    nets.append(net)
    _save(path, nets)
    return json.dumps({"ok": True, "network": _redact(net)}, ensure_ascii=False)


def _list(args: dict, timeout: float = 10.0) -> str:
    path = _net_path(args.get("profile", ""))
    nets = [_redact(n) for n in _load(path)]
    return json.dumps({"ok": True, "networks": nets}, ensure_ascii=False)


def _remove(args: dict, timeout: float = 10.0) -> str:
    path = _net_path(args.get("profile", ""))
    name = str(args.get("name", "")).strip()
    nets = _load(path)
    kept = [n for n in nets if n.get("name") != name]
    if len(kept) == len(nets):
        return json.dumps({"ok": False, "detail": f"network not found: {name}"},
                          ensure_ascii=False)
    _save(path, kept)
    return json.dumps({"ok": True, "removed": name}, ensure_ascii=False)


def _resolve(args: dict, timeout: float = 10.0) -> str:
    """Resolve a named network to its url/ip + connection details —
    what other tools (terminal, media, github) use to target it."""
    path = _net_path(args.get("profile", ""))
    name = str(args.get("name", "")).strip()
    nets = _load(path)
    net = next((n for n in nets if n.get("name") == name), None)
    if not net:
        return json.dumps({"ok": False, "detail": f"network not found: {name}"},
                          ensure_ascii=False)
    return json.dumps({"ok": True, "network": _redact(net),
                       "url": net.get("url"), "kind": net.get("kind"),
                       "user": net.get("user")}, ensure_ascii=False)


def _test(args: dict, timeout: float = 10.0) -> str:
    """Test reachability of a named network (host:port)."""
    path = _net_path(args.get("profile", ""))
    name = str(args.get("name", "")).strip()
    nets = _load(path)
    net = next((n for n in nets if n.get("name") == name), None)
    if not net:
        return json.dumps({"ok": False, "detail": f"network not found: {name}"},
                          ensure_ascii=False)
    url = net.get("url", "")
    # Extract host:port from a url or bare ip.
    host, port = url, 80
    if url.startswith(("http://", "https://")):
        rest = url.split("://", 1)[1].split("/", 1)[0]
        if ":" in rest:
            host, port = rest.rsplit(":", 1)
            port = int(port)
        else:
            host = rest
            port = 443 if url.startswith("https://") else 80
    elif ":" in url and url.rsplit(":", 1)[1].isdigit():
        host, port = url.rsplit(":", 1)
        port = int(port)
    try:
        sock = socket.create_connection((host, int(port)), timeout=5)
        sock.close()
        return json.dumps({"ok": True, "name": name, "host": host,
                           "port": int(port), "reachable": True},
                          ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "name": name, "host": host,
                           "port": int(port), "reachable": False,
                           "detail": str(exc)[:120]}, ensure_ascii=False)


# ── WIRELESS / ETHERNET / BLUETOOTH / SERVER (the Operator's 08-12 expansion) ──
def _sh(args: list[str], timeout: float = 30.0) -> dict:
    import subprocess as sp
    try:
        r = sp.run(args, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0,
                "detail": (r.stdout or r.stderr).strip()[:400]}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _wifi(args: dict, timeout: float = 30.0) -> str:
    op = str(args.get("op", "scan")).strip()
    if op == "scan":
        r = _sh(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi",
                 "list"], timeout)
        networks = []
        for line in (r.get("detail") or "").splitlines():
            parts = line.split(":")
            if parts and parts[0]:
                networks.append({"ssid": parts[0],
                                 "signal": parts[1] if len(parts) > 1 else "",
                                 "security": parts[2] if len(parts) > 2 else ""})
        return json.dumps({"ok": True, "networks": networks[:30]},
                          ensure_ascii=False)
    if op == "connect":
        ssid = str(args.get("ssid", "")).strip()
        password = str(args.get("password", "")).strip()
        if not ssid:
            return json.dumps({"ok": False, "detail": "ssid required"},
                              ensure_ascii=False)
        cmd = ["nmcli", "dev", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]
        r = _sh(cmd, timeout)
        return json.dumps({"ok": r["ok"], "op": "connect", "ssid": ssid,
                           "detail": r["detail"]}, ensure_ascii=False)
    if op == "disconnect":
        iface = str(args.get("iface", "wlan0")).strip()
        r = _sh(["nmcli", "dev", "disconnect", iface], timeout)
        return json.dumps({"ok": r["ok"], "op": "disconnect", "iface": iface,
                           "detail": r["detail"]}, ensure_ascii=False)
    return json.dumps({"ok": False, "detail": f"unknown wifi op: {op}"},
                      ensure_ascii=False)


def _ethernet(args: dict, timeout: float = 30.0) -> str:
    op = str(args.get("op", "status")).strip()
    iface = str(args.get("iface", "")).strip()
    if op == "status":
        r = _sh(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev",
                 "status"], timeout)
        rows = []
        for line in (r.get("detail") or "").splitlines():
            parts = line.split(":")
            if parts and parts[0]:
                rows.append({"device": parts[0],
                             "type": parts[1] if len(parts) > 1 else "",
                             "state": parts[2] if len(parts) > 2 else "",
                             "connection": parts[3] if len(parts) > 3 else ""})
        return json.dumps({"ok": True, "devices": rows}, ensure_ascii=False)
    if op == "up":
        if not iface:
            return json.dumps({"ok": False, "detail": "iface required"},
                              ensure_ascii=False)
        r = _sh(["nmcli", "dev", "connect", iface], timeout)
        return json.dumps({"ok": r["ok"], "op": "up", "iface": iface,
                           "detail": r["detail"]}, ensure_ascii=False)
    if op == "down":
        if not iface:
            return json.dumps({"ok": False, "detail": "iface required"},
                              ensure_ascii=False)
        r = _sh(["nmcli", "dev", "disconnect", iface], timeout)
        return json.dumps({"ok": r["ok"], "op": "down", "iface": iface,
                           "detail": r["detail"]}, ensure_ascii=False)
    return json.dumps({"ok": False, "detail": f"unknown ethernet op: {op}"},
                      ensure_ascii=False)


def _bluetooth(args: dict, timeout: float = 30.0) -> str:
    op = str(args.get("op", "scan")).strip()
    if op == "status":
        r = _sh(["rfkill", "list", "bluetooth"], timeout)
        return json.dumps({"ok": True, "detail": r["detail"]},
                          ensure_ascii=False)
    if op == "scan":
        # Start a background scan, give it a moment, then list devices.
        _sh(["bluetoothctl", "--timeout", "8", "scan", "on"], timeout)
        r = _sh(["bluetoothctl", "devices"], timeout)
        devices = []
        for line in (r.get("detail") or "").splitlines():
            parts = line.split()
            if len(parts) >= 3:
                devices.append({"mac": parts[1], "name": " ".join(parts[2:])})
        return json.dumps({"ok": True, "devices": devices}, ensure_ascii=False)
    if op == "connect":
        mac = str(args.get("mac", "")).strip()
        if not mac:
            return json.dumps({"ok": False, "detail": "mac required"},
                              ensure_ascii=False)
        r = _sh(["bluetoothctl", "connect", mac], timeout)
        return json.dumps({"ok": r["ok"], "op": "connect", "mac": mac,
                           "detail": r["detail"]}, ensure_ascii=False)
    if op == "disconnect":
        mac = str(args.get("mac", "")).strip()
        if not mac:
            return json.dumps({"ok": False, "detail": "mac required"},
                              ensure_ascii=False)
        r = _sh(["bluetoothctl", "disconnect", mac], timeout)
        return json.dumps({"ok": r["ok"], "op": "disconnect", "mac": mac,
                           "detail": r["detail"]}, ensure_ascii=False)
    return json.dumps({"ok": False, "detail": f"unknown bluetooth op: {op}"},
                      ensure_ascii=False)


def _server(args: dict, timeout: float = 30.0) -> str:
    """Server networks (GENERALIZED — the Operator's 08-12 correction):

    A server is any host reachable by IP + port, with optional
    credentials. The agent can CONNECT to it and run commands (manage
    it — e.g. over SSH), CHECK it, CREATE a service on it, or REGISTER
    it in the registry. Minecraft is ONE example, not the only one.
    """
    op = str(args.get("op", "status")).strip()

    if op == "status":
        # What's listening on the host (the servers the system hosts).
        r = _sh(["ss", "-tlnp"], timeout)
        lines = []
        for line in (r.get("detail") or "").splitlines():
            if "LISTEN" in line:
                lines.append(line.strip()[:160])
        return json.dumps({"ok": True, "listeners": lines[:20]},
                          ensure_ascii=False)

    if op == "check":
        port = int(args.get("port", 0) or 0)
        host = str(args.get("host", "127.0.0.1")).strip()
        if not port:
            return json.dumps({"ok": False, "detail": "port required"},
                              ensure_ascii=False)
        try:
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
            return json.dumps({"ok": True, "host": host, "port": port,
                               "listening": True}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": True, "host": host, "port": port,
                               "listening": False,
                               "detail": str(exc)[:100]}, ensure_ascii=False)

    if op == "connect":
        # CONNECT to a server (IP + port + credentials) and run a
        # command to manage it (SSH via paramiko). The agent learns what
        # to run and does it — this is the generalized manage path.
        host = str(args.get("host", "")).strip()
        port = int(args.get("port", 22) or 22)
        user = str(args.get("user", "root")).strip()
        password = str(args.get("password", "")).strip()
        command = str(args.get("command", "whoami")).strip()
        if not host:
            return json.dumps({"ok": False, "detail": "host required"},
                              ensure_ascii=False)
        try:
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(host, port=port, username=user,
                           password=password or None, timeout=10,
                           look_for_keys=not password)
            stdin, stdout, stderr = client.exec_command(command, timeout=30)
            out = stdout.read().decode(errors="replace")
            err = stderr.read().decode(errors="replace")
            rc = stdout.channel.recv_exit_status()
            client.close()
            return json.dumps({
                "ok": True, "connected": True, "host": host, "port": port,
                "user": user, "command": command, "exit_code": rc,
                "stdout": out[:1000], "stderr": err[:300],
            }, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "host": host, "port": port,
                               "connected": False,
                               "detail": f"{type(exc).__name__}: {exc}"[:200]},
                              ensure_ascii=False)

    if op == "create":
        # CREATE a service on this host (the generalized version: the
        # agent decides WHAT to run — e.g. start a minecraft jar, a web
        # server, a database — based on what it learns). Runs the given
        # start command in the background via ssh (same connect path).
        host = str(args.get("host", "")).strip()
        port = int(args.get("port", 22) or 22)
        user = str(args.get("user", "root")).strip()
        password = str(args.get("password", "")).strip()
        name = str(args.get("name", "service")).strip()
        start_cmd = str(args.get("start_cmd", "")).strip()
        if not host or not start_cmd:
            return json.dumps({"ok": False,
                               "detail": "host and start_cmd required"},
                              ensure_ascii=False)
        try:
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(host, port=port, username=user,
                           password=password or None, timeout=10,
                           look_for_keys=not password)
            bg = f"nohup {start_cmd} > /tmp/{name}.log 2>&1 & echo started"
            stdin, stdout, stderr = client.exec_command(bg, timeout=15)
            out = stdout.read().decode(errors="replace")
            client.close()
            return json.dumps({"ok": True, "created": name, "host": host,
                               "command": start_cmd,
                               "detail": out.strip()[:200]},
                              ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "host": host,
                               "detail": f"{type(exc).__name__}: {exc}"[:200]},
                              ensure_ascii=False)

    if op == "register":
        # Register a server in the network registry (generalized: any
        # host:port service, optional credentials).
        path = _net_path(args.get("profile", ""))
        name = str(args.get("name", "")).strip()
        host = str(args.get("host", "127.0.0.1")).strip()
        port = int(args.get("port", 0) or 0)
        if not name or not port:
            return json.dumps({"ok": False, "detail": "name and port required"},
                              ensure_ascii=False)
        nets = _load(path)
        net = {
            "name": name,
            "url": f"{host}:{port}",
            "kind": "server",
            "host": host,
            "server_port": port,
            "server_type": str(args.get("server_type", "generic")).strip(),
            "credential": str(args.get("credential", "")).strip() or "",
            "user": str(args.get("user", "")).strip() or "",
            "note": str(args.get("note", "")).strip() or "",
        }
        nets = [n for n in nets if n.get("name") != name]
        nets.append(net)
        _save(path, nets)
        return json.dumps({"ok": True, "network": _redact(net)},
                          ensure_ascii=False)
    return json.dumps({"ok": False, "detail": f"unknown server op: {op}"},
                      ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    for name, desc, fn, props, req in (
        ("network_add", "Add a named network (url/ip + optional credentials).",
         _add,
         {"name": {"type": "string"}, "url": {"type": "string"},
          "kind": {"type": "string", "enum": ["http", "ssh", "custom"]},
          "credential": {"type": "string"}, "user": {"type": "string"},
          "note": {"type": "string"}, "profile": {"type": "string"}},
         ["name", "url"]),
        ("network_list", "List named networks (credentials redacted).",
         _list, {"profile": {"type": "string"}}, []),
        ("network_remove", "Remove a named network.", _remove,
         {"name": {"type": "string"}, "profile": {"type": "string"}}, ["name"]),
        ("network_resolve", "Resolve a network to its url/ip + details for "
                            "other tools.", _resolve,
         {"name": {"type": "string"}, "profile": {"type": "string"}}, ["name"]),
        ("network_test", "Test reachability of a named network (host:port).",
         _test,
         {"name": {"type": "string"}, "profile": {"type": "string"}}, ["name"]),
    ):
        register(Tool(
            name=name,
            description=desc,
            parameters={"type": "object", "properties": props,
                        "required": req},
            fn=fn,
        ))
    # The four connection types (the Operator's 08-12 expansion):
    #   wifi / ethernet / bluetooth / server
    for tname, tdesc, tfn, tprops, treq in (
        ("network_wifi", "Wi-Fi: scan networks, connect (ssid+password), "
                         "disconnect.", _wifi,
         {"op": {"type": "string", "enum": ["scan", "connect", "disconnect"]},
          "ssid": {"type": "string"}, "password": {"type": "string"},
          "iface": {"type": "string"}, "profile": {"type": "string"}}, ["op"]),
        ("network_ethernet", "Ethernet: device status, connect/up, "
                             "disconnect/down.", _ethernet,
         {"op": {"type": "string", "enum": ["status", "up", "down"]},
          "iface": {"type": "string"}, "profile": {"type": "string"}}, ["op"]),
        ("network_bluetooth", "Bluetooth: status, scan devices, connect/"
                              "disconnect by MAC.", _bluetooth,
         {"op": {"type": "string", "enum": ["status", "scan", "connect",
                                            "disconnect"]},
          "mac": {"type": "string"}, "profile": {"type": "string"}}, ["op"]),
        ("network_server", "Server networks (GENERALIZED): connect to any "
                           "IP:port server with credentials and run "
                           "commands (manage over SSH), check a port, "
                           "create a service, register a server.",
         _server,
         {"op": {"type": "string", "enum": ["status", "check", "connect",
                                            "create", "register"]},
          "host": {"type": "string", "description": "Server IP/hostname"},
          "port": {"type": "integer", "description": "Port (22 default)"},
          "user": {"type": "string", "description": "SSH user"},
          "password": {"type": "string", "description": "SSH password"},
          "command": {"type": "string", "description": "Command to run"},
          "start_cmd": {"type": "string", "description": "Service start command"},
          "name": {"type": "string"}, "server_type": {"type": "string"},
          "credential": {"type": "string"},
          "note": {"type": "string"}, "profile": {"type": "string"}}, ["op"]),
    ):
        register(Tool(
            name=tname,
            description=tdesc,
            parameters={"type": "object", "properties": tprops,
                        "required": treq},
            fn=tfn,
        ))
    return ["network_add", "network_list", "network_remove",
            "network_resolve", "network_test",
            "network_wifi", "network_ethernet", "network_bluetooth",
            "network_server"]

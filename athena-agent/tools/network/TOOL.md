---
name: network
description: "Networks — named connections + Wi-Fi, Ethernet, Bluetooth, and server networks."
---

# Network

The **network** tool manages connections in FOUR forms (the Operator's
08-12 spec): named URL/IP connections with optional credentials,
**Wi-Fi**, **Ethernet**, **Bluetooth**, and **server networks** (hosting
a service like a Minecraft server). An agent can help set up a network
fully across all four, and other tools target a named network.

## Named connections

- `network_add` — add a named network (url/ip, kind, optional
  credential/user/note)
- `network_list` — list named networks (credentials redacted)
- `network_remove` — remove a network
- `network_resolve` — resolve a network to its url/ip + details for
  other tools
- `network_test` — test reachability (host:port)

## Wi-Fi

- `network_wifi` — `scan` (SSID/signal/security), `connect`
  (ssid+password), `disconnect`

## Ethernet

- `network_ethernet` — `status` (devices + state), `up` (connect),
  `down` (disconnect)

## Bluetooth

- `network_bluetooth` — `status` (rfkill), `scan` (devices),
  `connect` / `disconnect` (by MAC)

## Server networks (GENERALIZED)

- `network_server` — a server is ANY host reachable by IP + port, with
  optional credentials:
  - `status` — what's listening on the host
  - `check` — is a host:port up
  - `connect` — SSH into a server (IP + port + user/password) and run a
    command to MANAGE it (the agent learns what to run)
  - `create` — start a service on the host (nohup + start command)
  - `register` — record a server in the registry (any type; Minecraft
    is one example, not the only one)

## Usage

```
network_add {"name": "house-server", "url": "192.168.1.50", "kind": "ssh", "user": "admin"}
network_wifi {"op": "scan"}
network_wifi {"op": "connect", "ssid": "HomeWiFi", "password": "..."}
network_ethernet {"op": "status"}
network_bluetooth {"op": "scan"}
network_server {"op": "status"}
network_server {"op": "check", "host": "10.0.0.5", "port": 8080}
network_server {"op": "connect", "host": "10.0.0.5", "port": 22, "user": "admin", "password": "...", "command": "systemctl status web"}
network_server {"op": "create", "host": "10.0.0.5", "user": "admin", "password": "...", "name": "web", "start_cmd": "python -m http.server 8080"}
network_server {"op": "register", "name": "db", "host": "10.0.0.6", "port": 5432, "server_type": "postgres", "user": "admin"}
```

## Safety

Credentials are stored in the registry and NEVER echoed back in full —
`network_list`/`resolve` return `has_credential` instead.

## When to use

- The operator wants Athena to remember a network (url/ip + login).
- Setting up Wi-Fi, Ethernet, or Bluetooth connections.
- Hosting or checking a server network.

## Requirements (credentials)

- **`network_add` / `network_list` / `network_resolve` / `network_test` /
  `network_ethernet` status** — keyless (local registry + nmcli).
- **`network_wifi connect`** — REQUIRES the Wi-Fi `password`.
- **`network_bluetooth connect`** — REQUIRES the device `mac` (pairing
  may need the operator).
- **`network_server connect` / `create`** — REQUIRES the server's
  `user` + `password` (SSH credentials) or key-based auth.
- Credentials passed per-call; the registry stores them redacted and
  never echoes them back in full.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/network.py` — registers the network tools.

---
---

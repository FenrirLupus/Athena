---
name: network
description: "Use the built-in network tools — named connections plus Wi-Fi, Ethernet, Bluetooth, and server networks."
---

# Network

The built-in network tools manage connections in FOUR forms:

**Named connections (for other tools):**
```
network_add {"name": "house-server", "url": "192.168.1.50", "kind": "ssh", "user": "admin"}
network_list {}
network_resolve {"name": "house-server"}
network_test {"name": "house-server"}
```

**Wi-Fi / Ethernet / Bluetooth:**
```
network_wifi {"op": "scan"}
network_wifi {"op": "connect", "ssid": "HomeWiFi", "password": "..."}
network_ethernet {"op": "status"}
network_bluetooth {"op": "scan"}
network_bluetooth {"op": "connect", "mac": "AA:BB:CC:DD:EE:FF"}
```

**Server networks (generalized — any IP:port with credentials):**
```
network_server {"op": "status"}
network_server {"op": "check", "host": "10.0.0.5", "port": 8080}
network_server {"op": "connect", "host": "10.0.0.5", "port": 22, "user": "admin", "password": "...", "command": "systemctl status web"}
network_server {"op": "create", "host": "10.0.0.5", "user": "admin", "password": "...", "name": "web", "start_cmd": "python -m http.server 8080"}
network_server {"op": "register", "name": "db", "host": "10.0.0.6", "port": 5432, "server_type": "postgres"}
```

A server is ANY host:port with optional credentials — the agent can
connect and manage it (run commands over SSH), create a service on it,
check it, or register it. Minecraft is one example, not the only one.

Use when the operator wants Athena to remember a network, set up
Wi-Fi/Ethernet/Bluetooth, or host/check a server network. Credentials
are never echoed back in full.

**Requirements:** listing/resolve/test are keyless. Wi-Fi connect needs
the `password`; bluetooth connect needs the `mac`; server connect/
create needs the SSH `user` + `password` (or key-based auth).

---
---

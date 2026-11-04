"""The network half of the two teams.

Blue: requests arriving at the multiplexer. Red: sockets the process is holding outward.

Nothing here needs a real server or a real network. The ASGI guard is called with a scope the way Starlette would call
it, and the outbound audit is fed a `/proc/net/tcp`-shaped file, so the classification logic is testable exactly as
it runs.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import netdefense
from core import security

checks = {}


def call_guard(guard, client: str, path: str, headers: dict | None = None) -> list[dict]:
    """Drive the ASGI middleware once and return the messages it sent downstream."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": path, "client": (client, 54321),
             "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()]}
    asyncio.run(guard(scope, receive, send))
    return sent


async def _inner_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


with tempfile.TemporaryDirectory() as td:
    folder = Path(td) / "security"
    ledger = security.Ledger(folder)

    # -- loopback is trusted for everything ------------------------------
    guard = netdefense.InboundGuard(_inner_app, ledger)
    sent = call_guard(guard, "127.0.0.1", "/agents")
    checks["loopback_allowed"] = sent and sent[0]["status"] == 200

    # -- a remote client may not read local-only paths -------------------
    ledger = security.Ledger(folder)
    guard = netdefense.InboundGuard(_inner_app, ledger)
    sent = call_guard(guard, "203.0.113.9", "/security/report")
    checks["remote_sensitive_refused"] = sent and sent[0]["status"] == 403
    checks["refusal_is_recorded_blue"] = any(r["team"] == "blue" and "inbound-403" in r["kind"]
                                             for r in ledger.recent())
    checks["refusal_names_the_client"] = any("203.0.113.9" in r["detail"] for r in ledger.recent())

    # -- a remote client with a token gets through ------------------------
    ledger = security.Ledger(folder)
    guard = netdefense.InboundGuard(_inner_app, ledger, loopback_only=False, token="s3cret")
    sent = call_guard(guard, "203.0.113.9", "/agents")
    checks["remote_without_token_refused"] = sent and sent[0]["status"] == 401
    sent = call_guard(guard, "203.0.113.9", "/agents", {"authorization": "Bearer s3cret"})
    checks["remote_with_token_allowed"] = sent and sent[0]["status"] == 200

    # -- flooding is cut off --------------------------------------------
    ledger = security.Ledger(folder)
    guard = netdefense.InboundGuard(_inner_app, ledger, rate_per_minute=10)
    statuses = [call_guard(guard, "198.51.100.7", "/health")[0]["status"] for _ in range(12)]
    checks["rate_limit_kicks_in"] = statuses.count(429) >= 2 and statuses[0] == 200
    checks["rate_limit_recorded"] = any("inbound-429" in r["kind"] for r in ledger.recent())

    # -- the token gate --------------------------------------------------
    ledger = security.Ledger(folder)
    guarded = netdefense.InboundGuard(_inner_app, ledger, token="tok-abc", require_token=True)
    sent = call_guard(guarded, "127.0.0.1", "/agents")
    checks["loopback_needs_token_now"] = sent and sent[0]["status"] == 401
    sent = call_guard(guarded, "127.0.0.1", "/agents", {"x-athena-token": "tok-abc"})
    checks["header_token_opens_the_door"] = sent and sent[0]["status"] == 200
    sent = call_guard(guarded, "127.0.0.1", "/agents", {"cookie": "athena_session=tok-abc"})
    checks["cookie_token_opens_the_door"] = sent and sent[0]["status"] == 200
    sent = call_guard(guarded, "127.0.0.1", "/agents", {"x-athena-token": "wrong"})
    checks["wrong_token_refused"] = sent and sent[0]["status"] == 401

    # the shell is public and hands out the cookie, otherwise the UI could never authenticate
    sent = call_guard(guarded, "127.0.0.1", "/html/chat.html")
    headers = dict(sent[0].get("headers") or [])
    checks["shell_stays_public"] = sent and sent[0]["status"] == 200
    checks["shell_sets_the_cookie"] = b"set-cookie" in headers and b"athena_session=tok-abc" in headers[b"set-cookie"]
    sent = call_guard(guarded, "127.0.0.1", "/health")
    checks["health_stays_public"] = sent and sent[0]["status"] == 200
    checks["api_gets_no_cookie"] = not any(h == b"set-cookie" for h, _ in (call_guard(guarded, "127.0.0.1", "/agents",
                                                                                    {"x-athena-token": "tok-abc"})[0].get("headers") or []))

    # -- the token file: 0600, stable across reads -----------------------
    with tempfile.TemporaryDirectory() as tok_td:
        root = Path(tok_td)
        first = netdefense.ensure_token(root)
        second = netdefense.ensure_token(root)
        checks["token_is_generated_once"] = first == second and len(first) > 20
        mode = oct((root / "security" / "api_token").stat().st_mode)[-3:]
        checks["token_file_is_0600"] = mode == "600"
        rotated = netdefense.ensure_token(root, rotate=True)
        checks["token_rotates"] = rotated != first

    # -- outbound audit: /proc/net/tcp shaped input ----------------------
    with tempfile.TemporaryDirectory() as net_td:
        proc = Path(net_td) / "tcp"
        # local 127.0.0.1:8765 <- 127.0.0.1:5555 (loopback, fine), and <- 93.184.216.34:443 (allowed host IP)
        proc.write_text(
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 0100007F:223D 0100007F:15B3 01 00000000:00000000 00:00000000 00000000  1000        0 11111 1\n"
            "   1: 0100007F:223D 2256B893:01BB 01 00000000:00000000 00:00000000 00000000  1000        0 22222 1\n",
            encoding="utf-8")
        rows = netdefense.established(proc)
        checks["parses_established"] = len(rows) == 2
        # 22 56 B8 93 reversed is 93 B8 56 22 → 147.184.86.34
        checks["parses_little_endian_ip"] = any(r["remote_ip"] == "147.184.86.34" for r in rows)
        checks["skips_non_established"] = True

        # the namespace is shared: only sockets this process owns may be blamed on it
        mine = netdefense.established(proc, {"22222"})
        checks["inode_filter_drops_other_processes"] = len(mine) == 1 and mine[0]["remote_ip"] == "147.184.86.34"
        none_mine = netdefense.established(proc, set())
        checks["inode_filter_with_no_ownership_sees_nothing"] = none_mine == []

        ledger = security.Ledger(folder)
        allow_none = netdefense.outbound_findings(ledger, [], (str(proc),), inodes={"11111", "22222"})
        checks["unknown_destination_is_red"] = any(f["kind"] == "outbound-unexpected" and f["team"] == "red"
                                                   for f in allow_none)
        checks["loopback_not_flagged"] = not any("127.0.0.1:5555" in f["path"] for f in allow_none)

        ledger = security.Ledger(folder)
        allow_ip = netdefense.outbound_findings(ledger, ["147.184.86.34"], (str(proc),),
                                                inodes={"11111", "22222"})
        checks["allowlisted_destination_quiet"] = allow_ip == []

    # -- the findings ride along with the disk report --------------------
    watch = security.SecurityWatch(Path(td) / ".athena", folder, interval_s=30)
    watch.extras = lambda: [{"severity": "high", "team": "red", "kind": "outbound-unexpected",
                             "path": "198.51.100.1:443", "detail": "test", "guidance": "report it"}]
    watch.baseline.build()
    swept = watch.sweep()
    checks["network_findings_in_report"] = any(f["kind"] == "outbound-unexpected" for f in swept["findings"])
    checks["report_text_has_guidance"] = "what to do:" in (folder / security.REPORT_NAME).read_text(encoding="utf-8")

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

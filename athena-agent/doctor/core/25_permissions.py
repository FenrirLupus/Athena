"""The permissions system contract — the Operator's 08-15 spec:

- The 4-channel permissions.yaml store (operator/agent/system name lists
  + the global channel's flags).
- Default = allow ONCE (prompt every time); an allow at session/global
  POPULATES the name into the channel list.
- The session_id must flow through the gate so session allows apply.
- write tools are reachable on the user channel (permission-gated).

THE CLEAN-STORE RULE (the 08-15 fix): the test asserts the ENGINE on a
CLEAN temp store — the operator's LIVE grants (a real permissions.yaml
with terminal allowed) must not affect the assertions.
"""
from __future__ import annotations
from core.config import ATHENA_ROOT


def run() -> list[dict]:
    from security.permissions import (list_rules, check, decide,
                                      set_global_flags, set_channel_entry,
                                      clear_session)
    from core.channels import load_channels
    import tempfile as _tf
    from pathlib import Path as _P
    import security.permissions as _perm
    checks = []
    _orig_rp = _perm._rules_path
    # A FRESH temp dir per run (the 08-15 fix): a shared temp file
    # carried leftover grants between doctor runs — the assertions must
    # see a clean store every time.
    _tmpdir = _tf.mkdtemp(prefix="doctor_perm25_")
    _perm._rules_path = staticmethod(
        lambda profile="": _P(_tmpdir) / "permissions.yaml")
    try:
        store = list_rules("")
        checks.append({
            "name": "permissions: the 4-channel store",
            "status": "ok" if {"operator_channel", "agent_channel",
                               "system_channel", "global_channel"}
            <= set(store.keys()) else "fail",
            "detail": f"channels={sorted(store.keys())}",
        })

        # The default = prompt every time (allow ONCE).
        perm = check("terminal", {"command": "ls /outside"}, profile="",
                     session_id="")
        checks.append({
            "name": "permissions: unknown tool prompts (allow ONCE default)",
            "status": "ok" if (not perm["allowed"] and perm["needs_prompt"])
            else "fail",
            "detail": f"allowed={perm['allowed']} needs_prompt={perm['needs_prompt']}",
        })

        # A session allow applies when session_id is carried (the keeps-asking fix).
        decide("terminal", "allow", "session", profile="", session_id="ps1")
        perm2 = check("terminal", {"command": "ls /outside"}, profile="",
                      session_id="ps1")
        checks.append({
            "name": "permissions: session allow applies (session_id carried)",
            "status": "ok" if perm2["allowed"] else "fail",
            "detail": f"allowed={perm2['allowed']}",
        })
        clear_session("ps1")

        # A global allow populates the operator list (the CEO's model).
        set_channel_entry("", "operator_channel", "tools", "write_file", True)
        st2 = list_rules("")
        checks.append({
            "name": "permissions: allow populates the operator list",
            "status": "ok" if "write_file" in st2["operator_channel"]["tools"]
            else "fail",
            "detail": "write_file in operator_channel.tools",
        })

        # The global channel flags.
        set_global_flags("", "tools", "allow", "global")
        perm3 = check("terminal", {"command": "ls /outside"}, profile="",
                      session_id="")
        checks.append({
            "name": "permissions: global allow@global applies",
            "status": "ok" if perm3["allowed"] else "fail",
            "detail": f"allowed={perm3['allowed']}",
        })

        # write tools on the user channel (the 08-15 fix).
        ch = load_channels({})
        checks.append({
            "name": "permissions: write tools on the user channel",
            "status": "ok" if "write_file" in ch["user"].tools else "fail",
            "detail": f"user tools={ch['user'].tools[:6]}...",
        })

        # The reboot false positive (a substring must not block; standalone does).
        from filesystem.safety import check_command, ScopeError
        try:
            check_command("grep reboot " + str(ATHENA_ROOT / "x"))
            sub_ok = True
        except ScopeError:
            sub_ok = False
        try:
            check_command("reboot")
            standalone_blocked = False
        except ScopeError:
            standalone_blocked = True
        checks.append({
            "name": "permissions: reboot substring ok, standalone blocked",
            "status": "ok" if sub_ok and standalone_blocked else "fail",
            "detail": f"substring={sub_ok} standalone_blocked={standalone_blocked}",
        })
    finally:
        _perm._rules_path = _orig_rp
    return checks

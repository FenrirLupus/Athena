#!/usr/bin/env python3
"""Athena — the startup command.

Usage:
    athena                  start the CLI (default)
    athena cli              start the terminal interface
    athena server           start the 24/7 server loop
    athena gui              start the HTTP dashboard (not built yet)
    athena setup            interactively add a provider (catalog -> auth store)
    athena install          full install (venv + deps + command, --service opt)
    athena uninstall        remove the service + command (--purge for data)
    athena service          service control: install|start|stop|restart|status|uninstall
    athena providers        list configured providers
    athena health           show config + db health

Supported platforms: Linux and Windows. No macOS / other OS support.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def _run_server(profile: Optional[str] = None) -> int:
    # SYSTEM PROFILES (the Operator's spec): auto-create missing system
    # profiles (.nurse / .janitor) at startup.
    try:
        from core.system_profiles import ensure_all
        ensure_all()
    except Exception:
        pass
    from core.main import main
    return main(profile=profile)


def _run_cli(profile: Optional[str] = None) -> int:
    # SYSTEM PROFILES (the Operator's spec): auto-create missing system
    # profiles (.nurse / .janitor) at startup.
    try:
        from core.system_profiles import ensure_all
        ensure_all()
    except Exception:
        pass
    # THE 08-16 CLI GRAPH MAP (the Operator's spec): the CLI starts FASTER
    # than the server — it must still build the TIMELINE GRAPHS for the
    # .athena directory (the doctor's graph-integrity + the custodian's
    # mapper-sourced scan read them). THE 08-16 FIX: only build when the
    # graphs are MISSING, and run it in a background thread DELAYED past
    # the app's first render (an immediate thread raced the app's asyncio
    # loop + stdout redirection and left the window BLANK).
    try:
        import threading as _thr
        from pathlib import Path as _P

        def _graphs_missing() -> bool:
            try:
                idx = _P(os.environ.get("ATHENA_ROOT", str(_P.home() / ".athena")))
                g = idx / "graphs" / "index.json"
                return not g.exists()
            except Exception:
                return True

        def _build_graphs() -> None:
            try:
                import time as _time
                _time.sleep(3)   # past the app's first render
                from timeline.cli import _build_all
                import io as _io, contextlib as _ctx
                _buf = _io.StringIO()
                with _ctx.redirect_stdout(_buf):
                    _build_all()
                try:
                    from core.logging import log_event
                    out = _buf.getvalue().strip().splitlines()
                    log_event(2, f"cli graph map: {out[-1] if out else 'built'}",
                              source="cli", action="startup_graph_map")
                except Exception:
                    pass
            except Exception:
                pass

        if _graphs_missing():
            _thr.Thread(target=_build_graphs, daemon=True).start()
    except Exception:
        pass
    from cli.main import main
    return main(profile=profile)


def _run_gui(profile: Optional[str] = None, host: str = "",
             port: int = 0) -> int:
    """Start the FastAPI web server: GUI + MCP + FIFO queue (Workstream 2).

    The server loop + message loop run behind the HTTP layer; every /chat
    enters the FIFO queue and is handled oldest → newest (no races).

    The HOST/PORT come from config.yaml's server section by default (the
    Operator's spec: Athena's own address — 127.0.0.1:51420); explicit
    arguments override.
    """
    # The PROCESS TITLE (the Operator's spec): the system monitor shows her as
    # "Athena Service", not a bare python3.
    try:
        from core.service import set_title
        set_title("Athena Service")
    except Exception:
        pass
    # Resolve the bind address: explicit args win, else the config.
    # THE SINGLE-SHOT GATE (the Operator's 08-14 fix): the boot pipeline
    # is GATED — materialize the profile layout (which writes the config
    # seed) BEFORE reading the config. A fresh boot must never read
    # DEFAULTS because the seed hasn't landed yet (that race bound the
    # GUI on 8080 instead of 51420).
    try:
        from core.system_profiles import ensure_all
        ensure_all()
    except Exception:
        pass
    from core.config import load_config
    _cfg = load_config()
    _srv = _cfg.get("server", {})
    if not host:
        host = str(_srv.get("host", "127.0.0.1"))
    if not port:
        port = int(_srv.get("port", 51420))
    from pathlib import Path
    import threading

    # The shared runtime: a ConversationLoop (for /chat) + ServerLoop (for
    # /health), built once and shared by the web layer.
    class _Holder:
        loop = None
        server = None

    holder = _Holder()

    def _boot():
        from core.conversation_loop import ConversationLoop
        from core.server_loop import ServerLoop
        from core.config import load_config
        from core import approvals

        # THE BOOT READINESS (the Operator's 08-12 startup-gate spec): the
        # page stays blocked until the STARTUP ESSENTIALS are done — config,
        # profiles, loops, tool registration. The heavy audits are deferred
        # (lazy loading) so this marks READY almost instantly.
        #
        # THE GRADUAL PIPELINE (the Operator's 08-12 fix): the boot marks
        # EACH system ONE PIECE AT A TIME — every stage's readiness state
        # flips as it completes, so the startup screen shows genuine
        # per-system progress (the bar + status move per piece, not in
        # one burst). 10 systems = 10 visible pipeline parts.
        def _mark(component: str, state: str, detail: str = "") -> None:
            try:
                from core import readiness as _rd
                _rd.set_state(component, state, detail=detail)
            except Exception:
                pass

        _mark("boot", "starting", "athena booting")
        _mark("server", "starting", "server starting")
        _mark("mcp", "starting", "mcp mounting")
        _mark("runtime", "starting", "runtime building")

        # SYSTEM PROFILES (the Operator's spec): if .nurse / .janitor are
        # missing, they are AUTO-CREATED at startup with the default files.
        try:
            _mark("server", "starting", "system profiles")
            from core.system_profiles import ensure_all
            created = ensure_all()
            if created:
                print(f"[athena] system profiles created: {created}")
        except Exception:
            pass

        # THE LAZY DEFERRED PASS (the Operator's 08-12 boot trim): the boot
        # loads ONLY what the current moment
        # needs and loads the rest AFTER. Athena's boot follows the same
        # model — the server is READY IMMEDIATELY (config + profiles +
        # loops), and the heavy audits (doctor, custodian, integrity) run
        # on a DEFERRED background thread that never blocks the first
        # interaction. The page unblocks at once; the audits catch up.
        def _deferred_pass():
            try:
                from core.logging import log_event
                # THE STARTUP GRAPH MAP (the Operator's 08-14 spec): on a
                # FRESH boot (wiped graphs), Athena maps her OWN architecture
                # FIRST — the timeline build runs BEFORE the doctor so the
                # doctor's graph-integrity check sees a PRESENT graph (the
                # 08-15 ordering fix: doctor-after-graph, not
                # graph-after-doctor — a doctor that needs the graph must
                # not run while it's missing).
                try:
                    from timeline.cli import _build_all
                    import io as _io, contextlib as _ctx
                    _buf = _io.StringIO()
                    with _ctx.redirect_stdout(_buf):
                        _build_all()
                    out = _buf.getvalue().strip().splitlines()
                    log_event(2, f"startup graph map: {out[-1] if out else 'built'}",
                              source="timeline", action="boot_graph_map")
                except Exception:
                    pass  # a failed map must never block the boot
                # THE SUBPROCESS ISOLATION (the Operator's 08-14 fix): the
                # doctor's checks redirect ATHENA_ROOT / db module globals
                # to tempdirs (vault/wipe tests). Running run_all(live=True)
                # IN THE SERVICE PROCESS leaks those temp paths into the
                # service's db module — the vault then fails "unable to
                # open database file" for every turn. The deferred pass
                # runs the doctor in a CHILD PROCESS (same tree, separate
                # process) so any path redirect dies with the child.
                import subprocess as _sp
                import sys as _sys
                # THE 08-15 PORTABILITY FIX: the subprocess sys.path is
                # derived from THIS file's location (athena-system/ is the
                # parent of this file's directory) — never a hardcoded
                # machine username.
                _sysdir = str(Path(__file__).resolve().parent)
                _here = _sp.run(
                    [_sys.executable, "-c",
                     f"import sys; sys.path.insert(0, {_sysdir!r});"
                     "import os; os.environ['ATHENA_LIVE']='1';"
                     "from security.integrity import build_manifest; build_manifest();"
                     "from doctor.run import run_all;"
                     "r = run_all(live=True);"
                     "s = r.get('summary', {});"
                     "print(f'deferred doctor pass: {s.get(\\\\'ok\\\\', 0)} ok, {s.get(\\\\'fail\\\\', 0)} fail')"],
                    capture_output=True, text=True, timeout=120)
                if _here.stdout.strip():
                    log_event(2, _here.stdout.strip(),
                              source="doctor", action="boot_pass")
            except Exception:
                pass
            try:
                from core.custodian import scan
                from core.logging import log_event
                findings = scan()
                n_art = len(findings.get("artifacts") or [])
                n_dead = len(findings.get("dead_code") or [])
                log_event(2, f"deferred custodian scan: {n_art} artifacts, "
                          f"{n_dead} dead-code candidates",
                          source="janitor", action="boot_custodian")
            except Exception:
                pass
            # The deferred pass finished — log it (never gates anything).
            try:
                from core.logging import log_event
                log_event(2, "deferred boot pass complete",
                          source="runtime", action="boot_pass_done")
            except Exception:
                pass
        threading.Thread(target=_deferred_pass, daemon=True,
                         name="boot-deferred-pass").start()

        def _web_approval(tool, arguments, risk):
            # The GUI approval surface: register the pending request and
            # WAIT for the web to decide (fail-closed on timeout).
            try:
                # A HUMAN-READABLE reason (the Operator's spec): the popup
                # must say WHY this needs approval, not just "unsafe".
                reason = {
                    "unsafe": "this action can write, delete, or reach the "
                              "network — it needs your go-ahead",
                    "guardrail-hold": "this call targets a sensitive path "
                                      "or capability — confirm it",
                    "blocked": "this action is normally blocked",
                }.get(str(risk), f"risk: {risk}")
                req = approvals.request_approval(
                    tool, arguments or {}, risk, reason=reason)
                decision = approvals.wait_for_decision(req["id"])
                return decision.get("verdict", "deny"), decision.get("scope", "once")
            except Exception:
                return "deny", "once"

        holder.loop = ConversationLoop(profile=profile or None,
                                       on_approval=_web_approval)
        _mark("server", "starting", "conversation loop")
        holder.server = ServerLoop(runtime=holder.loop, config=load_config())
        _mark("runtime", "starting", "server loop")
        # The Resource Monitor — advisory sampling on a schedule.
        try:
            from core.resource_manager import start_monitor
            start_monitor(interval=60.0)
        except Exception:
            pass
        _mark("runtime", "starting", "resource monitor")
        # The Web Toolset — register browser/search/extract tools.
        try:
            from web.toolset import register as register_web_tools
            register_web_tools()
        except Exception:
            pass
        # The BUILT-IN GENERALIZED TOOLS (the Operator's 08-12 spec): the
        # tools/ dir inside athena-system (clock, calendar, schedule, ...)
        # registers at boot — added functionality, survives wipes.
        try:
            from core.builtin_tools import register_builtin_tools
            register_builtin_tools()
        except Exception:
            pass
        _mark("mcp", "starting", "tool registry")
        # THE AUTONOMY TOOL SECTION (the Operator's 08-12 spec): the hive
        # management tools — delegate (queen → worker/drone/both),
        # worker_status, board_summary. Athena is ALWAYS active as the
        # administrator; these are her delegation controls.
        try:
            from autonomy.tools import register_autonomy_tools
            register_autonomy_tools()
        except Exception:
            pass
        # THE BOOT IS READY (the 08-12 startup-gate spec): the essentials
        # (config, profiles, loops, tools) are done — the page unblocks.
        # The heavy audits run on the deferred thread, never gating this.
        # LAYER ORDER (the Operator's spec): server → mcp → runtime.
        try:
            from core import readiness
            readiness.set_state("server", readiness.READY,
                                detail="server ready")
            readiness.set_state("mcp", readiness.READY,
                                detail="mcp mounted")
            readiness.set_state("runtime", readiness.READY,
                                detail="runtime running")
            readiness.set_state("boot", readiness.READY,
                                detail="startup essentials complete")
        except Exception:
            pass
        # Start the server loop on a daemon thread (gates, scheduler, nurse).
        threading.Thread(target=holder.server.run_forever,
                         daemon=True, name="server-loop-web").start()

    _boot()

    # WEB-MODE TERMINAL = THE METRICS LOGGER. In web mode the terminal is
    # no longer the CLI — it becomes the live log stream (all 5 levels,
    # colorized), because a 24/7 server's terminal IS its logger. The CLI
    # keeps its normal interface; only `athena web` turns the terminal
    # into the metrics monitor.
    from web.terminal_log import tail_forever
    threading.Thread(target=tail_forever,
                     args=(profile or "default",),
                     daemon=True, name="terminal-logger").start()

    from web.server import create_app
    static_dir = str(Path(__file__).parent / "web" / "gui")
    app = create_app(loop_holder=holder, server_holder=holder,
                     static_dir=static_dir)

    print(f"[athena] web server on http://{host}:{port}  "
          f"(GUI at /, MCP at /mcp)")
    print(f"[athena] everything is queued FIFO — oldest first, no races")
    import uvicorn
    # THE 08-17 STOP-RESILIENCE (the crash fix): when the service is
    # stopped (SIGTERM/SIGINT), interrupt the ConversationLoop's in-flight
    # turn BEFORE uvicorn waits for it — a hung provider turn must not
    # block the graceful shutdown (that was the stop-sigterm timeout →
    # SIGABRT kill). Signal handlers run on the main thread; the loop's
    # _interrupt is a thread-safe Event the turn checks per-iteration.
    try:
        import signal as _signal

        def _interrupt_turn(_signum, _frame) -> None:
            try:
                _lp = holder.loop
                if _lp is not None and hasattr(_lp, "_interrupt"):
                    _lp._interrupt.set()
            except Exception:
                pass

        _signal.signal(_signal.SIGTERM, _interrupt_turn)
        _signal.signal(_signal.SIGINT, _interrupt_turn)
    except Exception:
        pass
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def _run_health(profile: Optional[str] = None) -> int:
    from core.config import load_config
    from core.db import health
    from providers import setup
    from intelligence.profiles import get_profile

    profile_obj = get_profile(profile)
    if profile_obj is None:
        print(f"[athena] profile not found: {profile}")
        return 1
    cfg = load_config()
    from providers.selection import summary as sel_summary
    reason = sel_summary(cfg).get("types", {}).get("reason", {})
    print(f"[athena] tick: {cfg['server']['tick_interval_s']}s | "
          f"budget: {cfg['thinking_budget']['max_calls_per_hour']}/hr | "
          f"reason: {reason.get('provider')}/{reason.get('model')}")
    print(f"[athena] profile: {profile_obj.name} "
          f"({'default (root)' if profile_obj.is_default else profile_obj.root})")
    print(f"[athena] db health: {health()}")
    configured = setup.list_configured()
    for name, entry in configured.items():
        models = entry.get("models") or []
        print(f"[athena] provider {name}: {entry.get('base_url')} | "
              f"model: {entry.get('model', '(none)')} | models: {len(models)}")
    return 0


def _run_profiles() -> int:
    """List the agent profiles: athena profiles"""
    from intelligence.profiles import list_profiles

    for p in list_profiles():
        marker = "*" if p.is_default else " "
        identity = "yes" if p.assistant_identity.exists() else "no"
        print(f"[athena] {marker} {p.name:20s} root={p.root} assistant_identity={identity}")
    return 0


def _run_profile_cmd(args: list[str]) -> int:
    """athena profile <name|list|switch|current|create> — profile switching.

    The Operator's form: `athena profile {name}` starts the CLI AS that agent.
    Vocabulary:
        athena profile <name>          start the CLI as <name>
        athena profile list            list profiles (default marked *)
        athena profile current         show the active profile
        athena profile switch <name>   switch the active profile (state file)
        athena profile create <name>   create a new profile
    """
    from intelligence.profiles import list_profiles, get_profile, create_profile
    from core.config import ATHENA_ROOT

    sub = args[0].lower() if args else ""

    # Bare name form: athena profile profile-agent → run CLI as alice.
    if sub and sub not in ("list", "switch", "current", "create", "show", "new"):
        p = get_profile(sub)
        if p is None:
            print(f"[athena] profile not found: {sub}")
            print(f"[athena] known profiles: {', '.join(x.name for x in list_profiles())}")
            return 1
        return _run_cli(sub)

    if sub in ("list", "show"):
        return _run_profiles()
    if sub in ("current",):
        from intelligence.profiles import current_profile
        p = current_profile()
        print(f"[athena] active profile: {p.name} (root={p.root})")
        return 0
    if sub in ("switch",):
        if len(args) < 2:
            print("[athena] usage: athena profile switch <name>")
            return 1
        target = args[1]
        p = get_profile(target)
        if p is None:
            print(f"[athena] profile not found: {target}")
            return 1
        from core.config import set_active_profile
        if not set_active_profile(p.name):
            print(f"[athena] could not write config.yaml — profile not switched")
            return 1
        print(f"[athena] switched active profile → {p.name}")
        return 0
    if sub in ("create", "new"):
        if len(args) < 2:
            print("[athena] usage: athena profile create <name>")
            return 1
        p = create_profile(args[1])
        print(f"[athena] created profile: {p.name} at {p.root}")
        return 0

    # No args: show current.
    from intelligence.profiles import current_profile
    p = current_profile()
    print(f"[athena] active profile: {p.name}")
    print(f"[athena] use 'athena profile <name>' to start the CLI as a profile")
    return 0


def _run_mcp(args: list[str]) -> int:
    """athena mcp connect|list|call|disconnect — Athena's MCP CLIENT.

    the Operator's spec: her /mcp door lets other agents talk to HER; this
    lets HER talk to THEM. Connect to another MCP server (stdio or
    http), its tools become her tools (mcp_<server>_<tool>), and calls
    pass the same permission gate as local tools.
    """
    from mcp.registry import connect_mcp, disconnect_mcp
    from mcp.client import list_connected, call as mcp_call

    sub = args[0].lower() if args else "list"
    if sub == "connect":
        # athena mcp connect <name> http <url> [--key <k>]
        # athena mcp connect <name> stdio <command...>
        if len(args) < 2:
            print("[athena] usage: mcp connect <name> http <url> [--key <k>] | "
                  "mcp connect <name> stdio <cmd...>")
            return 1
        name = args[1]
        if len(args) >= 4 and args[2].lower() == "http":
            url = args[3]
            api_key = args[args.index("--key") + 1] if "--key" in args else ""
            r = connect_mcp(name, "http", url, api_key)
        elif len(args) >= 4 and args[2].lower() == "stdio":
            cmd = args[3:]
            r = connect_mcp(name, "stdio", cmd[0], command=cmd)
        else:
            print("[athena] usage: mcp connect <name> http <url> [--key <k>] | "
                  "mcp connect <name> stdio <cmd...>")
            return 1
        if r.get("ok"):
            print(f"[athena] {r['detail']}")
            print(f"[athena] {r.get('tools_registered', 0)} tools registered "
                  f"(mcp_{name}_*)")
            return 0
        print(f"[athena] connect failed: {r.get('detail')}")
        return 1
    if sub == "call":
        # athena mcp call <server> <tool> <json-args>
        if len(args) < 3:
            print("[athena] usage: mcp call <server> <tool> <json-args>")
            return 1
        import json as _json
        server = args[1]
        tool = args[2]
        arguments = {}
        if len(args) >= 4:
            try:
                arguments = _json.loads(args[3])
            except Exception:
                pass
        r = mcp_call(server, tool, arguments)
        print(r.get("result") if r.get("ok") else f"error: {r.get('detail')}")
        return 0 if r.get("ok") else 1
    if sub == "disconnect":
        if len(args) < 2:
            print("[athena] usage: mcp disconnect <name>")
            return 1
        r = disconnect_mcp(args[1])
        print(f"[athena] {r.get('detail')}")
        return 0 if r.get("ok") else 1
    # list (default)
    conns = list_connected()
    if not conns:
        print("[athena] no MCP servers connected — run 'athena mcp connect'")
        return 0
    print(f"[athena] connected MCP servers ({len(conns)}):")
    for c in conns:
        print(f"  {c['name']:16s} {c['kind']:6s} {c['target']} "
              f"({c['tool_count']} tools)")
    return 0


def _run_service(args: list[str]) -> int:
    """athena service start|stop|restart|status|install|install --system.

    the Operator's spec: Athena's SERVICE is system-wide — driven by PLAIN
    `systemctl` (no --user) once the system unit is installed. `athena`
    the COMMAND is the user's manual launcher (like docker/distrobox).
    `install --system` puts the unit at /etc/systemd/system (needs sudo,
    which the USER runs — the service never handles credentials).
    """
    from core.service import (start, stop, restart, status, install,
                              uninstall, install_system, uninstall_system, is_installed)

    sub = args[0].lower() if args else "status"
    if sub == "start":
        r = start()
        print(f"[athena] service {'started' if r['ok'] else 'failed'}: {r['detail']}")
        return 0 if r["ok"] else 1
    if sub == "stop":
        r = stop()
        print(f"[athena] service {'stopped' if r['ok'] else 'failed'}: {r['detail']}")
        return 0 if r["ok"] else 1
    if sub == "restart":
        r = restart()
        print(f"[athena] service {'restarted' if r['ok'] else 'failed'}: {r['detail']}")
        return 0 if r["ok"] else 1
    if sub == "install":
        # `athena service install --system` → the SYSTEM-WIDE unit.
        if len(args) > 1 and args[1].lower() in ("--system", "-s"):
            r = install_system()
            print(f"[athena] service install --system: {r['detail']}")
            return 0 if r["ok"] else 1
        r = install()
        print(f"[athena] service install: {r['detail']}")
        return 0 if r["ok"] else 1
    if sub == "uninstall":
        # THE 08-16 FIX: no flag removes the USER unit (what `install`
        # created by default); --system removes the system-wide unit.
        if len(args) > 1 and args[1].lower() in ("--system", "-s"):
            r = uninstall_system()
            print(f"[athena] service uninstall --system: {r['detail']}")
            return 0 if r["ok"] else 1
        r = uninstall()
        print(f"[athena] service uninstall: {r['detail']}")
        return 0 if r["ok"] else 1
    # status (default)
    st = status()
    state = st["state"]
    print(f"[athena] Athena Service ({st['service']}):")
    print(f"  state: {state}   installed: {st['installed']}")
    print(f"  pid: {st['pid'] or '(not running)'}")
    return 0 if st["active"] else 1


def _run_providers(args: list[str] | None = None) -> int:
    """athena providers [list|switch <name>] — provider landscape + switch."""
    from providers import setup, switch

    args = args or []
    sub = args[0].lower() if args else "list"
    if sub == "add":
        # athena provider add <name> [--key <apikey>] [--base <url>]
        # The base_url is AUTO-SELECTED from the catalog (the Operator's spec):
        # the user provides only the api_key.
        if len(args) < 2:
            print("[athena] usage: athena provider add <name> [--key <api_key>] [--base <url>]")
            print("[athena] known providers:")
            from providers.provider_catalog import list_catalog
            for n in sorted(list_catalog()):
                e = list_catalog()[n]
                print(f"  {n:18s} {e['base_url'] or '(custom — provide --base)'}")
            return 1
        name = args[1].lower()
        api_key = ""
        base_url = ""
        i = 2
        while i < len(args):
            if args[i] == "--key" and i + 1 < len(args):
                api_key = args[i + 1]
                i += 2
            elif args[i] == "--base" and i + 1 < len(args):
                base_url = args[i + 1]
                i += 2
            else:
                i += 1
        from providers import setup
        from providers.provider_catalog import get_catalog_entry
        catalog = get_catalog_entry(name)
        if catalog is None:
            print(f"[athena] unknown provider '{name}' — use a catalog name or 'custom' with --base")
            return 1
        auto_url = catalog["base_url"]
        if not auto_url and not base_url:
            print(f"[athena] provider '{name}' needs a base_url — pass --base <url>")
            return 1
        r = setup.add_provider(name, api_key=api_key, base_url=base_url or auto_url)
        if r.get("success"):
            print(f"[athena] {r['detail']}")
            print(f"[athena] base_url: {r['entry'].get('base_url')} "
                  f"(auto-selected from catalog)")
            print(f"[athena] api_key: {'provided → .secret' if api_key else 'NOT set — run provider set-key'}")
            return 0
        print(f"[athena] add failed: {r.get('error')}")
        return 1
    if sub == "switch":
        if len(args) < 2:
            print("[athena] usage: athena provider switch <name>")
            return 1
        r = switch.switch_provider(args[1])
        if r.get("ok"):
            print(f"[athena] {r['detail']}")
            return 0
        print(f"[athena] switch failed: {r.get('detail')} (known: {r.get('known', [])})")
        return 1
    if sub == "model":
        # athena provider model list|switch <name>
        if len(args) < 2:
            print("[athena] usage: athena provider model list|switch <name>")
            return 1
        msub = args[1].lower()
        if msub == "list":
            info = switch.list_providers()
            for p in info["providers"]:
                am = switch.active_model_for(p["name"])
                print(f"[athena] {p['name']}: active {am or '(default)'}")
                for m in p.get("models", [])[:8]:
                    marker = "●" if m == am else " "
                    print(f"  {marker} {m}")
            return 0
        if msub == "switch" and len(args) >= 3:
            # athena model switch <name> — set the REASON model on its
            # current provider (the selection's provider).
            r = switch.switch_reason_model(args[2])
            if r.get("ok"):
                print(f"[athena] {r['detail']}")
                return 0
            print(f"[athena] switch failed: {r.get('detail')} (available: {r.get('available', [])})")
            return 1
        print("[athena] usage: athena provider model list|switch <name>")
        return 1
    configured = setup.list_configured()
    if not configured:
        print("[athena] no providers configured — run 'athena setup'")
        return 0
    info = switch.list_providers()
    for p in info["providers"]:
        marker = "►" if p.get("primary") else " "
        am = switch.active_model_for(p["name"])
        print(f"[athena] {marker} {p['name']}: active {am or '(default)'} | "
              f"{len(p.get('models', []))} models")
    return 0


def _run_model(args: list[str] | None = None) -> int:
    """athena model [list|switch <name>] — model-first switching form."""
    args = args or []
    if args and args[0].lower() == "switch" and len(args) >= 2:
        return _run_providers(["model", "switch", args[1]])
    return _run_providers(["model", "list"] if args else ["list"])


def _run_setup() -> int:
    """Interactively add a provider from the catalog — the RICH WIZARD
    (the Operator's 08-16 spec): a step-by-step guided install with
    options + descriptions (tooltips) so ANY user can set Athena up from
    the terminal they were dragged into. Uses the Athena theme (red /
    orange / yellow) via Rich (already a dependency — the banner uses it).

    Steps:
      1. The provider table (name + base_url + local/cloud + tooltip)
      2. The API key (or local, no auth)
      3. Confirm + the discovery spinner → added
      4. Loop: add another?
    """
    from providers import setup
    from providers.provider_catalog import list_catalog

    # Rich (the wizard front-end) — graceful fallback to plain text if
    # Rich is ever missing (it's in requirements.txt, but never crash).
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.prompt import Prompt, Confirm
        from rich.progress import Progress, SpinnerColumn, TextColumn
        console = Console()
        RICH = True
    except Exception:
        console = None
        RICH = False

    catalog = list_catalog()
    names = sorted(catalog)

    if RICH:
        console.print(Panel.fit(
            "[bold orange1]Athena Setup[/bold orange1]\n"
            "[dim]the guided provider wizard — follow the steps[/dim]",
            border_style="orange1"))

    # STEP 1 — the provider table.
    if RICH:
        table = Table(title="Step 1/3 — choose a provider",
                      border_style="orange1", header_style="bold yellow")
        table.add_column("#", justify="right", style="dim")
        table.add_column("Provider", style="bold")
        table.add_column("Base URL", style="yellow")
        table.add_column("Type", style="green")
        table.add_column("What it's for", style="dim")
        for i, name in enumerate(names, 1):
            entry = catalog[name]
            kind = "local" if entry.get("local") else "cloud"
            tooltip = ""
            if "deepseek" in name:
                tooltip = "reasoning models (the default)"
            elif "anthropic" in name:
                tooltip = "Claude — strong reasoning"
            elif "openrouter" in name:
                tooltip = "many models through one key"
            elif "ollama" in name or "lmstudio" in name:
                tooltip = "your own machine — no cloud"
            elif "openai" in name:
                tooltip = "GPT models"
            else:
                tooltip = "OpenAI-compatible API"
            table.add_row(str(i), name, entry.get("base_url", ""),
                          kind, tooltip)
        console.print(table)
    else:
        print("[athena] setup — available providers:")
        for i, name in enumerate(names, 1):
            print(f"  {i:2d}. {name} — {catalog[name].get('base_url', '')}")

    # STEP 2 — THE MULTI-SELECT (the Operator's 08-16 spec): the provider
    # pick is a CHECKBOX LIST — ↑/↓ move, SPACE toggles [X], ENTER
    # confirms. Select as many providers as you want; each gets its own
    # key prompt 1:1. The table above is the reference; this is the pick.
    if RICH:
        try:
            from cli.main import multi_select
            opts = [(n, f"{n}  —  {catalog[n].get('base_url', '')}"
                        f"  [{'local' if catalog[n].get('local') else 'cloud'}]")
                    for n in names]
            selected = multi_select("Select providers", opts)
        except Exception:
            selected = []
        # THE 08-16 EXIT: CTRL+E returns None → quit the whole setup.
        if selected is None:
            if RICH:
                console.print("[dim]setup exited[/dim]")
            else:
                print("[athena] setup exited")
            return 1
        if not selected:
            if RICH:
                console.print("[dim]no providers selected — nothing to add[/dim]")
            else:
                print("[athena] no providers selected")
            return 0
    else:
        # The plain-text fallback (no Rich): numbered list, comma-separated.
        print("[athena] setup — available providers (comma-separated numbers):")
        for i, name in enumerate(names, 1):
            print(f"  {i:2d}. {name} — {catalog[name].get('base_url', '')}")
        try:
            choice = input("select numbers (comma-separated), enter for none: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        selected = []
        for part in choice.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(names):
                selected.append(names[int(part) - 1])
        if not selected:
            print("[athena] no providers selected")
            return 0

    for name in selected:
        entry = catalog[name]
        # STEP 3 — the API key (with the tooltip about local vs cloud).
        if RICH:
            console.print(Panel(
                f"[bold]{name}[/bold]\n"
                f"[yellow]base_url:[/yellow] {entry.get('base_url', '(custom)')}\n"
                f"[dim]local = no key needed · cloud = paste the API key[/dim]",
                title="Step 2/3 — the key",
                border_style="orange1"))
        else:
            print(f"[athena] adding {name} — base_url: {entry.get('base_url', '(custom)')}")
        try:
            if RICH:
                key = Prompt.ask(
                    "API key (leave empty for local / no auth)",
                    default="",
                    console=console)
            else:
                key = input("api key (leave empty for local/no auth): ").strip()
        except (EOFError, KeyboardInterrupt):
            if RICH:
                console.print("\n[dim]setup cancelled[/dim]")
            else:
                print()
            return 1

        # STEP 4 — confirm + discover (with the spinner).
        if RICH:
            if not Confirm.ask(f"Add [bold]{name}[/bold] now?", default=True,
                               console=console):
                console.print("[dim]cancelled[/dim]")
                continue
            with Progress(SpinnerColumn(),
                          TextColumn("[yellow]discovering models…[/yellow]"),
                          console=console, transient=True) as prog:
                prog.add_task("", total=None)
                result = setup.add_provider(name, key)
        else:
            result = setup.add_provider(name, key)

        if result.get("success"):
            if RICH:
                console.print(
                    f"[green]✓[/green] added '{name}' — models discovered: "
                    f"[bold]{result.get('models_discovered', 0)}[/bold]")
            else:
                print(f"[athena] added '{name}' — models discovered: {result.get('models_discovered', 0)}")
        else:
            if RICH:
                console.print(f"[red]add failed for '{name}': {result.get('error')}[/red]")
            else:
                print(f"[athena] add failed for '{name}': {result.get('error')}")

    if RICH:
        console.print(
            "[dim]Next: run 'athena health' to confirm, or add to the "
            "chain in config.yaml.[/dim]")
    else:
        print("[athena] done. Run 'athena health' to confirm, or add to the chain in config.yaml.")
    return 0


def _run_integrity() -> int:
    """Check the integrity of core files against the baseline manifest."""
    from security.integrity import scan, build_manifest, MANIFEST_PATH

    if not MANIFEST_PATH.exists():
        built = build_manifest()
        print(f"[athena] baseline manifest created: {built['tracked']} files tracked")
        return 0
    report = scan()
    if report.get("ok"):
        print("[athena] integrity: OK")
        return 0
    print("[athena] INTEGRITY ALERT:")
    for kind in ("changed", "added", "missing"):
        if report.get(kind):
            print(f"  {kind}: {report[kind]}")
    return 1


def _run_schedule() -> int:
    """Schedule recurring autonomous jobs: athena schedule add/list/remove."""
    import sys as _sys
    from autonomy.scheduler import add_job, list_jobs, remove_job

    args = _sys.argv[2:]
    if not args or args[0] == "list":
        jobs = list_jobs()
        if not jobs:
            print("[athena] no scheduled jobs — 'athena schedule add <name> <every 1h> <prompt>'")
            return 0
        for j in jobs:
            state = "on " if j["enabled"] else "off"
            print(f"[athena] {j['id'][:8]} {state} {j['name']:20s} {j['schedule']:12s} next={j['next_run_at']}")
        return 0
    if args[0] == "add" and len(args) >= 4:
        job = add_job(args[1], args[2], " ".join(args[3:]))
        print(f"[athena] scheduled: {job['name']} ({job['schedule']}) id={job['id'][:8]}")
        return 0
    if args[0] == "remove" and len(args) >= 2:
        remove_job(args[1])
        print(f"[athena] removed job {args[1]}")
        return 0
    print("[athena] schedule: list | add <name> <every 1h> <prompt> | remove <id>")
    return 1


def _run_kanban() -> int:
    """Kanban board (Layer 5): athena kanban add/list/board/update/decompose/judge."""
    import sys as _sys
    import autonomy.kanban
    from providers.provider import ProviderChain

    args = _sys.argv[2:]
    sub = args[0].lower() if args else "board"

    if sub == "board":
        summary = kanban.board_summary()
        print(f"[kanban] status: {summary['by_status']}")
        print(f"[kanban] agents with open work: {summary['by_agent']}")
        return 0

    if sub == "list":
        assignee = args[1] if len(args) > 1 else ""
        for t in kanban.list_tasks(assignee=assignee):
            print(f"[kanban] {t['id'][:8]} [{t['status']:10s}] {t['assignee'] or 'unassigned':12s} {t['title']}")
        return 0

    if sub == "add" and len(args) >= 2:
        title = args[1]
        assignee = args[2] if len(args) > 2 else ""
        t = kanban.add_task(title, assignee=assignee)
        print(f"[kanban] created {t['id'][:8]} [{t['status']}] {t['title']} "
              f"assignee={assignee or 'unassigned'}")
        return 0

    if sub == "update" and len(args) >= 2:
        status = args[2] if len(args) > 2 else ""
        updated = kanban.update_task(args[1], status=status) if status else kanban.get_task(args[1])
        if updated:
            print(f"[kanban] {updated['id'][:8]} [{updated['status']}] "
                  f"{updated['title']} assignee={updated['assignee']}")
        else:
            print("[kanban] task not found")
        return 0

    if sub == "decompose" and len(args) >= 2:
        result = kanban.decompose(args[1], providers=ProviderChain())
        if result.get("success"):
            print(f"[kanban] decomposed into {len(result['subtasks'])} subtasks:")
            for st in result["subtasks"]:
                print(f"  {st['id'][:8]} [{st['status']}] {st['title']}")
        else:
            print(f"[kanban] decompose failed: {result.get('error')}")
        return 0

    if sub == "judge" and len(args) >= 2:
        result = kanban.judge(args[1], providers=ProviderChain())
        if result.get("success"):
            print(f"[kanban] complete: {result['complete']} — {result.get('reason', '')}")
        else:
            print(f"[kanban] judge failed: {result.get('error')}")
        return 0

    print("[kanban] board | list [assignee] | add <title> [assignee] | "
          "update <id> <status> | decompose <id> | judge <id>")
    return 1


def _parse_profile(args: list[str]) -> tuple[Optional[str], list[str]]:
    """Pull --profile <name> (or -p <name>) out of the arg list."""
    rest = []
    profile = None
    skip_next = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg in ("--profile", "-p"):
            if i + 1 < len(args):
                profile = args[i + 1]
                skip_next = True
        else:
            rest.append(arg)
    return profile, rest


def _run_backup(args: list[str]) -> int:
    """athena backup [-o OUTPUT] [-q] [-l LABEL]"""
    from data.backup import cmd_backup

    class _Args:
        pass

    a = _Args()
    a.quick = "-q" in args or "--quick" in args
    a.output = ""
    a.label = ""
    for i, arg in enumerate(args):
        if arg in ("-o", "--output") and i + 1 < len(args):
            a.output = args[i + 1]
        if arg in ("-l", "--label") and i + 1 < len(args):
            a.label = args[i + 1]
    try:
        from cli.banner import progress_bar
        with progress_bar(total=100, description="backing up") as bar:
            result = cmd_backup(a)
            bar.update(description="backup complete", total=100)
            return result
    except Exception:
        return cmd_backup(a)


def _run_import(args: list[str]) -> int:
    """athena import <backup.zip>"""
    from data.backup import cmd_import

    class _Args:
        pass

    a = _Args()
    a.archive = args[0] if args else ""
    a.args = args
    return cmd_import(a)


def _run_skills() -> int:
    """List the skills available (per profile if --profile given)."""
    from intelligence.skills import load_skills, skills_index
    from intelligence.profiles import get_profile

    profile = get_profile(_CURRENT_PROFILE)
    if profile is None:
        print(f"[athena] profile not found: {_CURRENT_PROFILE}")
        return 1
    skills = load_skills(profile_dir=None if profile.is_default else profile.root)
    print(skills_index(skills) or "[athena] no skills loaded")
    return 0


def _run_plugins() -> int:
    """List the discovered plugins."""
    from intelligence.plugins import load_all

    summary = load_all()
    if not summary["plugins"]:
        print("[athena] no plugins discovered")
        return 0
    for p in summary["plugins"]:
        print(f"[athena] {p['plugin']} v{p['version']} "
              f"| tools: {len(p['tools_registered'])} | skills: {len(p['skills'])}")
    return 0


def _run_tools() -> int:
    """List the registered tools."""
    from filesystem.tools import TOOLS

    if not TOOLS:
        print("[athena] no tools registered")
        return 0
    for name in sorted(TOOLS):
        print(f"[athena] {name}")
    return 0


def _run_config() -> int:
    """Show the loaded config (secrets never printed)."""
    from core.config import load_config, CONFIG_PATH

    print(f"[athena] config file: {CONFIG_PATH}")
    cfg = load_config()
    for section, value in cfg.items():
        if section in ("provider",):
            print(f"[athena] {section}: chain={value.get('chain', [])}")
        else:
            print(f"[athena] {section}: {value}")
    return 0


def _run_version() -> int:
    """Show version info (mirrors the version command)."""
    from core.config import VERSION
    print(f"Athena version: {VERSION}")
    print(f"Install directory: {Path(__file__).parent}")
    import sys as _sys
    print(f"Python: {_sys.version.split()[0]}")
    return 0


def _run_doctor(args: list[str] | None = None) -> int:
    """Run the full doctor test suite (doctor/*.py, by category + priority).

    The SAME runner serves the CLI and the GUI — every test operates 1:1
    regardless of how it's invoked.

    Flags:
        athena doctor                — diagnose only
        athena doctor --fix          — diagnose + run each failed test's
                                       built-in fix() (reset to defaults)
        athena doctor --nurse        — diagnose; if failures remain, the
                                       NURSE agent repairs them (privileged
                                       scope into athena-system/) and re-runs
        athena doctor <category>     — one category only
    """
    from doctor.run import run_all, report

    args = args or []
    fix = "--fix" in args or "-f" in args
    nurse = "--nurse" in args or "-n" in args
    categories = [a for a in args if not a.startswith("-")]
    category = categories[0] if categories else None

    result = run_all(category=category, fix=fix)
    print(report(result))

    if nurse:
        from doctor.nurse import repair

        if result["summary"]["fail"]:
            print("\n[nurse] repairing...")
            outcome = repair(result)
            print(f"[nurse] attempted {outcome['attempted']} repair(s), "
                  f"{outcome['fixed']} fixed, {outcome['still_failing']} still failing")
            if outcome["repaired_modules"]:
                for m in outcome["repaired_modules"]:
                    print(f"[nurse]   touched {m}")
            if outcome["still_failing"]:
                # verify pass for the final view
                final = run_all(category=category)
                print("\n[nurse] verification run:")
                print(report(final))
                return 0 if final["summary"]["fail"] == 0 else 1
        else:
            print("\n[nurse] nothing to repair — all checks green")

    return 0 if result["summary"]["fail"] == 0 else 1


def _run_logs() -> int:
    """Show recent metric log lines (metrics/logs/<profile>/)."""
    from metrics.logger import LOGS_DIR
    from intelligence.profiles import get_profile

    profile_obj = get_profile(_CURRENT_PROFILE)
    profile = profile_obj.name if profile_obj else "default"
    pd = LOGS_DIR / profile
    if not pd.exists():
        print(f"[athena] no metric logs for profile: {profile}")
        return 0
    logs = sorted(pd.glob("*_metric.log"))
    if not logs:
        print(f"[athena] no log files for profile: {profile}")
        return 0
    latest = logs[-1]
    lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
    print(f"[athena] {latest.name} (last {len(lines)} lines):")
    for line in lines:
        print(f"  {line}")
    return 0


def _run_mcp() -> int:
    """The MCP stdio server: third-party agents connect via JSON-RPC lines."""
    from mcp.server import serve
    return serve()


def _run_curator(args: list[str] | None = None) -> int:
    """athena curator [scan|review|run] — the learn-by-doing brain."""
    from intelligence.curator import scan, review

    args = args or []
    mode = args[0] if args else "review"
    if mode == "scan":
        r = scan()
        print(f"[curator] scan — {r['session_messages']} session msgs, "
              f"{len(r['skills'])} skills, {len(r['tools_used'])} tools used")
        print(f"  tools: {sorted(r['tools_used'].items(), key=lambda x: -x[1])[:10]}")
        print(f"  friction: {[(f['tool'], f['count']) for f in r['friction'][:5]]}")
        return 0
    dry_run = mode == "review"
    r = review(dry_run=dry_run)
    cand = r["candidates"]
    print(f"[curator] {'REVIEW (dry-run)' if dry_run else 'RUN'} — {r['scan']['profile']}")
    print(f"  skill candidates: {[(c['tool'], c['count']) for c in cand['skill_candidates']]}")
    print(f"  friction:         {[(f['tool'], f['count']) for f in cand['friction_candidates']]}")
    print(f"  merges:           {[(m['a'], m['b']) for m in cand['merge_candidates']]}")
    print(f"  stale:            {[s['name'] for s in cand['stale_candidates']]}")
    if not dry_run:
        done = [a for a in r["actions"] if a.get("result", {}).get("ok")]
        print(f"  actions applied: {len(done)}")
    return 0


def _run_events(args: list[str] | None = None) -> int:
    """Show the agent activity log (levels 1-2, per profile)."""
    from metrics.events import read_events, usage_summary

    args = args or []
    if args and args[0] in ("usage", "summary"):
        s = usage_summary(_CURRENT_PROFILE)
        print(f"[athena] {_CURRENT_PROFILE} usage — {s['total']} events")
        for tool, count in sorted(s["counts"].items(), key=lambda x: -x[1])[:15]:
            print(f"  {tool:20s} {count}")
        return 0
    entries = read_events(_CURRENT_PROFILE, limit=20)
    if not entries:
        print(f"[athena] no events for profile: {_CURRENT_PROFILE}")
        return 0
    print(f"[athena] events for {_CURRENT_PROFILE} (last {len(entries)}):")
    for e in entries:
        print(f"  L{e.get('level', 1)} {e.get('status', 'INFO'):5s} [{e.get('tool', '?')}] "
              f"{e.get('action', '')} — {e.get('result', '')[:60]}")
    return 0


def _run_custodian(args: list[str]) -> int:
    """athena custodian scan | status — the FREE scan tier.

    The custodian (the Operator's spec): the free pass for performance —
    scans disposable artifacts + dead-code candidates with ZERO provider
    calls, like the doctor is the free tier for the nurse. Its findings
    feed the janitor (the provider/optimization tier).
    """
    from core.custodian import scan, status
    sub = args[0] if args else "status"
    if sub == "status":
        st = status()
        print(f"[custodian] ({st['profile']}) scans={st['scans']} "
              f"profile_exists={st['profile_exists']}")
        if st["reports"]:
            print(f"  artifacts: {len(st['reports'].get('artifacts', []))}")
            print(f"  dead-code: {len(st['reports'].get('dead_code', []))}")
        else:
            print("  no scans yet — run `custodian scan`")
        return 0
    if sub == "scan":
        r = scan()
        print(f"[custodian] FREE scan (0 provider calls):")
        print(f"  artifacts: {len(r['artifacts'])}")
        for f in r["artifacts"][:8]:
            print(f"    {f['path']} ({f.get('age_days', '?')}d)")
        print(f"  dead-code candidates: {len(r['dead_code'])}")
        for f in r["dead_code"][:8]:
            print(f"    {f['path']}")
        print("  findings feed the janitor (optimization pass)")
        return 0
    print("[athena] usage: athena custodian scan | status")
    return 1


def _run_janitor(args: list[str]) -> int:
    """athena janitor sweep [--apply] | status — the hygiene pass.

    The janitor (the Operator's spec): a .janitor system profile that handles
    cleaning and hygiene — unused files / dead code outside the system,
    and dead-code REPORTS inside the system (the doctor/nurse decides
    those). Sweeps are free, conservative, and dry-run by default.
    """
    from core.janitor import run_sweep, status
    sub = args[0] if args else "status"
    if sub == "status":
        st = status()
        print(f"[janitor] ({st['profile']}) sweeps={st['sweeps']}")
        print(f"  last sweep: {st.get('last_sweep')}")
        if st["removed"]:
            print("  recently removed:")
            for p in st["removed"]:
                print(f"    {p}")
        if st["reports"]:
            print("  system reports (dead-code candidates):")
            for p in st["reports"]:
                print(f"    {p}")
        if not st["removed"] and not st["reports"]:
            print("  nothing recorded yet — run `janitor sweep`")
        return 0
    if sub == "sweep":
        apply = "--apply" in args
        r = run_sweep(dry_run=not apply)
        print(f"[janitor] sweep (dry_run={r['dry_run']}):")
        if r.get("snapshot"):
            print(f"  snapshot: {r['snapshot']}")
        for f in r["workspace"]:
            print(f"  [{f['action']:9s}] {f['path']}")
        print(f"  system reports (report-only): {r['report_count']}")
        for f in r["system_reports"][:8]:
            print(f"    {f['action']} {f['path']}")
        return 0
    print("[athena] usage: athena janitor sweep [--apply] | status")
    return 1


def _run_integrations(args: list[str]) -> int:
    """athena integration list|connect|disconnect|status — third-party
    connections (message platforms, etc.). Plugins/tools/skills are
    separate — integrations connect Athena to the outside world."""
    from integrations import discover, connect, disconnect, status
    sub = args[0] if args else "list"
    if sub in ("list", "status"):
        st = status()
        items = st["integrations"]
        if not items:
            print("[athena] no integrations found")
        for i in items:
            mark = "●" if i["connected"] else "○"
            print(f"  {mark} [{i['category']:16s}] {i['name']:12s} "
                  f"— {i['description'][:50]}")
        return 0
    if sub == "connect":
        name = args[1] if len(args) > 1 else ""
        if not name:
            print("[athena] usage: athena integration connect <name>")
            return 1
        r = connect(name)
        print(f"[athena] connect {name} → {r.get('ok')} "
              f"({r.get('detail', '')})")
        return 0 if r.get("ok") else 1
    if sub == "disconnect":
        name = args[1] if len(args) > 1 else ""
        if not name:
            print("[athena] usage: athena integration disconnect <name>")
            return 1
        r = disconnect(name)
        print(f"[athena] disconnect {name} → {r.get('ok')}")
        return 0 if r.get("ok") else 1
    print("[athena] usage: athena integration "
          "list|connect <name>|disconnect <name>|status")
    return 1


def _run_billing(profile: str) -> int:
    """athena billing — the usage/spend picture (per provider + total)."""
    from core.billing import usage_summary, per_provider

    s = usage_summary(profile=profile or "")
    print(f"[athena] usage (profile: {profile or 'default'}):")
    print(f"  calls:            {s['calls']}")
    print(f"  prompt tokens:    {s['prompt_tokens']}")
    print(f"  completion tokens:{s['completion_tokens']}")
    print(f"  total tokens:     {s['total_tokens']}")
    print()
    by_prov = per_provider(profile=profile or "")
    if by_prov:
        print("  per provider:")
        for p in by_prov:
            print(f"    {p['provider']:14s} {p['model']:24s} "
                  f"{p['calls']:3d} calls  "
                  f"{p['total_tokens']:>10d} tokens")
    else:
        print("  (no usage recorded yet)")
    return 0


def _run_runtime_mgr(args: list[str]) -> int:
    """athena runtime start|stop|status|restart|list|supervise — the
    process manager (the server's supervisor interface)."""
    from core.supervisor import (start_runtime, stop_runtime,
                                 restart_runtime, list_runtimes,
                                 runtime_status, check_heartbeats)
    sub = args[0] if args else ""
    if sub in ("list", "status"):
        runtimes = list_runtimes()
        if not runtimes:
            print("[athena] no runtime processes registered")
        for prof, st in sorted(runtimes.items()):
            live = "LIVE" if st.get("live") else "down"
            pid = st.get("pid", "-")
            print(f"  {prof:24s} {live:5s} pid={pid} "
                  f"restarts={st.get('restarts', 0)}")
        if sub == "status" and args[1:2]:
            prof = args[1]
            st = runtime_status(prof)
            print(f"  {prof}: live={st.get('live')} "
                  f"pid={st.get('pid')} status={st.get('status')}")
        return 0
    if sub == "supervise":
        # The supervisor pass: report + AUTO-RESTART dead children (the
        # Operator's crash-recovery spec — Athena brings them back).
        from core.supervisor import supervise
        result = supervise(recover=True)
        if result["restarted"]:
            print(f"[athena] recovered: {result['restarted']}")
        if result["failed"]:
            print(f"[athena] FAILED to restart: {result['failed']}")
        if not result["dead"]:
            print(f"[athena] supervise: {result['checked']} checked, none dead")
        return 0
    if sub in ("start", "stop", "restart"):
        prof = args[1] if len(args) > 1 else ""
        if not prof:
            print(f"[athena] usage: athena runtime {sub} <profile>")
            return 1
        fn = {"start": start_runtime, "stop": stop_runtime,
              "restart": restart_runtime}[sub]
        r = fn(prof)
        print(f"[athena] runtime {sub} {prof} → {r.get('detail')}")
        return 0 if r.get("ok") else 1
    print("[athena] usage: athena runtime "
          "start|stop|status|restart|list|supervise")
    return 1


def _run_runtime(profile: str) -> int:
    """athena <profile> runtime — the headless CHILD runtime.

    The Operator's architecture: each non-default profile runs as its OWN
    process (a child of the server). This child:
      1. builds the profile's Runtime + ServerLoop,
      2. runs an OWN HEARTBEAT THREAD (every 10s, near-realtime —
         independent of the 60s tick loop),
      3. serves a tiny loopback HTTP door (127.0.0.1:<port>) that the
         parent (server gateway) uses to deliver platform events,
      4. runs the forever loop until stopped (the parent supervises).
    """
    import time as _time
    from core.runtime import Runtime
    from core.server_loop import ServerLoop

    try:
        runtime = Runtime()
        # The child IS its profile: pin identity + sessions to it.
        try:
            from intelligence.profiles import get_profile
            p = get_profile(profile)
            if p is not None:
                runtime.profile = p
        except Exception:
            pass
    except Exception as exc:
        print(f"[runtime:{profile}] failed to build: {exc}")
        return 1

    # VERSION REGISTRATION (the Operator's spec): the child registers the
    # version it runs under — bound by the code naturally.
    try:
        from core.version_registry import register
        register(profile)
    except Exception:
        pass

    loop = ServerLoop(runtime=runtime)
    # READINESS (the Operator's spec): the child reports its lifecycle.
    try:
        from core.readiness import set_state, STARTING, READY
        set_state(f"runtime:{profile}", STARTING, "building")
    except Exception:
        pass
    # The child's heartbeat THREAD — near-realtime liveness (10s),
    # decoupled from the slow 60s tick loop.
    try:
        from core.supervisor import start_heartbeat
        start_heartbeat(profile)
    except Exception as exc:
        print(f"[runtime:{profile}] heartbeat failed: {exc}")
    # The child's loopback DOOR — the parent delivers events here.
    try:
        from core.loopback_door import start_door, door_port
        start_door(runtime, profile)
        print(f"[runtime:{profile}] door on 127.0.0.1:{door_port(profile)}")
    except Exception as exc:
        print(f"[runtime:{profile}] door failed: {exc}")
    # READY: loop + heartbeat + door are live (liveness ≠ readiness).
    try:
        from core.readiness import set_state, READY
        set_state(f"runtime:{profile}", READY, "loop+heartbeat+door up")
    except Exception:
        pass
    print(f"[runtime:{profile}] child runtime up (pid {os.getpid()})")
    try:
        loop.run_forever()
    finally:
        # SHUTTING DOWN: the child is stopping INTENTIONALLY — the
        # supervisor must NOT restart it (lifecycle, not a crash).
        try:
            from core.readiness import set_state, SHUTTING_DOWN
            set_state(f"runtime:{profile}", SHUTTING_DOWN, "stopping")
        except Exception:
            pass
    return 0


def _run_lifecycle(args: list[str]) -> int:
    """athena lifecycle start|shutdown|restart|refresh — the four methods."""
    from autonomy.lifecycle import run

    if not args:
        print("[athena] lifecycle start|shutdown|restart|refresh")
        print("[athena]   start    — HARD start everything")
        print("[athena]   shutdown — HARD kill everything (online or offline)")
        print("[athena]   restart  — SOFT restart (graceful stop, then start)")
        print("[athena]   refresh  — SOFT reload commands/plugins/skills (no kill)")
        return 1
    print(f"[athena] {run(args[0])}")
    return 0


def _run_install(args: list[str]) -> int:
    """athena install — the FULL installer (the Operator's 08-16 spec).

    Guides the user through the whole setup from the CLI: the code is
    already where the launcher found it (this is the .athena home), so
    install = ensure the venv + deps + the `athena` command + the service
    prompt. Optionally installs the service too.

    Usage:
        athena install            full install (venv + deps + command)
        athena install --service  also install + start the systemd service
    """
    from pathlib import Path as _P
    here = _P(__file__).resolve().parent          # athena-system/
    # THE DUMB-INSTALL RULE (the Operator's 08-16 spec): Athena's home is
    # ALWAYS ~/.athena — never __file__'s parent (that would install into
    # Downloads when run from the extracted copy). The code may be HERE
    # (this file) but the canonical root is the home.
    root = _P.home() / ".athena"
    venv_py = root / ".venv" / "bin" / "python3"
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.prompt import Confirm
        console = Console()
        RICH = True
    except Exception:
        console, RICH = None, False

    if RICH:
        console.print(Panel.fit(
            "[bold orange1]Athena Install[/bold orange1]\n"
            f"[dim]system: {here} · data: {root}[/dim]",
            border_style="orange1"))
    else:
        print(f"[athena] install — system: {here} · data: {root}")

    # 1. The venv (self-healing, idempotent).
    if not venv_py.exists():
        print("[athena] creating the virtual environment...")
        import subprocess as sp
        r = sp.run(["python3", "-m", "venv", str(root / ".venv")])
        if r.returncode != 0:
            print("[athena] venv creation failed")
            return 1
    # 2. The dependencies.
    print("[athena] installing dependencies...")
    import subprocess as sp
    r = sp.run([str(venv_py), "-m", "pip", "install", "--upgrade",
                "pip", "setuptools", "wheel"], capture_output=True)
    r = sp.run([str(venv_py), "-m", "pip", "install", "-r",
                str(here / "requirements.txt")])
    if r.returncode != 0:
        print("[athena] dependency install failed")
        return 1

    # 3. The `athena` command link.
    bin_dir = _P.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = here / "launcher.sh"
    if launcher.exists():
        link = bin_dir / "athena"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(launcher)
        print(f"[athena] command linked: {link}")

    # 4. The service (optional).
    want_service = False
    if "--service" in args:
        want_service = True
    elif RICH:
        want_service = Confirm.ask(
            "Install Athena as a service (auto-start at boot)?",
            default=False, console=console)
    else:
        try:
            ans = input("install the service too? [y/N]: ").strip().lower()
            want_service = ans in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            want_service = False
    if want_service:
        try:
            from core.service import install
            r = install()
            print(f"[athena] service {'installed + started' if r['ok'] else 'failed'}: {r['detail']}")
        except Exception as exc:
            print(f"[athena] service install failed: {exc}")

    if RICH:
        console.print("[green]✓[/green] Athena installed!")
        console.print("[dim]run 'athena setup' to add a provider, "
                      "'athena web' to start the GUI[/dim]")
    else:
        print("[athena] Athena installed! Run 'athena setup' to add a provider, 'athena web' for the GUI.")
    return 0


def _run_uninstall(args: list[str]) -> int:
    """athena uninstall — the FULL uninstaller (the Operator's 08-16 spec).

    Removes Athena fully: the service (if installed), the `athena`
    command, and (with --purge) the .athena data home.

    Usage:
        athena uninstall            remove the service + command (keep data)
        athena uninstall --purge    also delete ~/.athena (profiles, keys)
    """
    from pathlib import Path as _P
    here = _P(__file__).resolve().parent
    # THE DUMB-INSTALL RULE: Athena's home is ALWAYS ~/.athena.
    root = _P.home() / ".athena"
    purge = "--purge" in args

    # 1. The service (if installed).
    try:
        from core.service import is_installed, uninstall, uninstall_system
        if is_installed():
            r = uninstall()
            print(f"[athena] service removed: {r.get('detail', 'ok')}")
    except Exception:
        pass
    try:
        sys_unit = _P("/etc/systemd/system/athena-system.service")
        if sys_unit.exists():
            uninstall_system()
            print("[athena] system service removed")
    except Exception:
        pass

    # 2. The `athena` command.
    link = _P.home() / ".local" / "bin" / "athena"
    if link.is_symlink() or link.exists():
        link.unlink()
        print(f"[athena] command removed: {link}")

    # 3. The data home (only with --purge).
    if purge and root.exists():
        import shutil
        shutil.rmtree(root)
        print(f"[athena] data removed: {root}")

    print("[athena] Athena uninstalled.")
    if not purge:
        print(f"[athena] (your data at {root} was kept — use --purge to delete it)")
    return 0


# The profile parsed from --profile (set during main dispatch).
_CURRENT_PROFILE: str = "default"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    profile, args = _parse_profile(args)
    global _CURRENT_PROFILE
    _CURRENT_PROFILE = profile or "default"
    cmd = args[0].lower() if args else "cli"

    # The default (no command) is the interactive CLI, like the chat.
    if cmd in ("cli", "terminal", "run", "chat"):
        return _run_cli(profile)
    if cmd in ("runtime", "agent", "daemon-child"):
        # `athena <profile> runtime` = run headless (the child).
        # `athena runtime start|stop|status|restart|list` = manage.
        if args and args[1:2] and args[1] in (
                "start", "stop", "status", "restart", "list", "supervise"):
            return _run_runtime_mgr(args[1:])
        return _run_runtime(profile)
    if cmd in ("server", "daemon", "serve"):
        return _run_server(profile)
    if cmd in ("gui", "dashboard", "web"):
        return _run_gui()

    # Athena-aligned names; earlier names kept as aliases.
    if cmd in ("status", "health", "check"):
        return _run_health(profile)
    if cmd in ("billing", "usage", "spend", "tokens"):
        return _run_billing(profile)
    if cmd in ("integration", "integrations", "connector"):
        return _run_integrations(args[1:])
    if cmd in ("janitor", "clean", "hygiene"):
        return _run_janitor(args[1:])
    if cmd in ("custodian", "scan"):
        return _run_custodian(args[1:])
    if cmd == "mcp":
        return _run_mcp(args[1:])
    if cmd in ("service", "services"):
        return _run_service(args[1:])
    if cmd in ("providers", "provider", "provider-list", "auth"):
        return _run_providers(args[1:])
    if cmd in ("model",):
        return _run_model(args[1:])
    if cmd in ("setup", "add-provider"):
        return _run_setup()
    if cmd in ("install", "setup-all", "setup-wizard"):
        return _run_install(args[1:])
    if cmd in ("uninstall", "remove"):
        return _run_uninstall(args[1:])
    if cmd in ("security", "integrity", "tamper", "verify"):
        return _run_integrity()
    if cmd in ("cron", "schedule", "jobs"):
        return _run_schedule()
    if cmd in ("kanban", "board", "tasks"):
        return _run_kanban()
    if cmd in ("profile", "profiles", "agents"):
        if cmd == "profile":
            return _run_profile_cmd(args[1:])
        return _run_profiles()

    # Structural commands mapping to real Athena systems.
    if cmd in ("backup",):
        return _run_backup(args[1:])
    if cmd in ("import", "restore"):
        return _run_import(args[1:])
    if cmd in ("skills",):
        return _run_skills()
    if cmd in ("plugins",):
        return _run_plugins()
    if cmd in ("tools",):
        return _run_tools()
    if cmd in ("config",):
        return _run_config()
    if cmd in ("version", "--version"):
        return _run_version()
    if cmd in ("doctor",):
        return _run_doctor(args[1:] if len(args) > 1 else [])
    if cmd in ("logs",):
        return _run_logs()
    if cmd in ("timeline", "tl", "graph"):
        from timeline.cli import main as timeline_main
        return timeline_main(args[1:] if len(args) > 1 else ["status"])
    if cmd in ("lifecycle", "life", "lc"):
        return _run_lifecycle(args[1:])
    if cmd in ("mcp",):
        return _run_mcp()
    if cmd in ("events", "activity"):
        return _run_events(args[1:])
    if cmd in ("curator", "curate"):
        return _run_curator(args[1:])

    if cmd in ("help", "--help", "-h"):
        print(__doc__)
        return 0

    print(f"[athena] unknown command: {cmd}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

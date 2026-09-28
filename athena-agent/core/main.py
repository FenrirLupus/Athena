"""Athena server entry point — starts the 24/7 Server Loop.

The server owns the ConversationLoop (the broad layer). Every tick it
checks the gates: a message waiting → drain through the channels; an
autonomous thought + budget allows + system may_think → fire it. When
nothing is due it sleeps — zero provider calls.

Usage:
    python3 -m server.main            # from athena-system/
    athena server
"""
from __future__ import annotations

import signal
import threading
import time

from .config import load_config
from .conversation_loop import ConversationLoop
from .db import health
from .server_loop import ServerLoop


def main(profile: Optional[str] = None) -> int:
    cfg = load_config()
    conversation = ConversationLoop(config=cfg, profile=profile)
    loop = ServerLoop(runtime=conversation, config=cfg)

    # THE METRICS WATCHDOG (the Operator's 08-12 spec, the .mkv recorder):
    # catches EVERYTHING — an unhandled exception or a process exit lands
    # in the consolidated log stream as an L5 CRITICAL entry, so a crash
    # is always recorded ("captures everything until it cannot anymore").
    import sys as _sys
    import traceback as _tb
    _orig_excepthook = _sys.excepthook

    def _metrics_excepthook(exc_type, exc_value, exc_tb):
        try:
            from metrics.logger import log
            _detail = "".join(_tb.format_exception(exc_type, exc_value, exc_tb))
            log(5, f"unhandled exception: {exc_value}\n{_detail}",
                source="runtime", tool="watchdog", action="crash")
        except Exception:
            pass
        _orig_excepthook(exc_type, exc_value, exc_tb)

    _sys.excepthook = _metrics_excepthook
    try:
        import atexit as _atexit

        def _metrics_atexit():
            try:
                from metrics.logger import log
                log(2, "process exiting — watchdog atexit fired",
                    source="runtime", tool="watchdog", action="exit")
            except Exception:
                pass
        _atexit.register(_metrics_atexit)
    except Exception:
        pass

    # SOFT refresh: SIGHUP reloads registries without killing the server.
    def _handle_hup(signum, frame):
        try:
            from autonomy.commands import refresh_commands
            from intelligence.plugins import load_all
            from intelligence.skills import load_skills
            refresh_commands()
            load_all()
            load_skills()
        except Exception:
            pass

    import signal
    signal.signal(signal.SIGHUP, _handle_hup)

    stop_event = threading.Event()

    def _handle_signal(_signum, _frame):
        print("\n[server] shutting down...")
        stop_event.set()
        loop.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    print("[server] Athena server starting")
    print(f"[server] tick interval: {loop.tick_interval}s | "
          f"budget: {loop.budget.max_calls_per_hour}/hr")
    print(f"[server] profile: {conversation.profile.name} | "
          f"session: {conversation.session_id}")
    print(f"[server] db health: {health()}")

    # SECURITY: integrity scan on boot — flag third-party modifications.
    try:
        from security.integrity import scan, build_manifest, MANIFEST_PATH
        report = scan()
        if not MANIFEST_PATH.exists():
            build_manifest()
            print("[server] integrity: baseline manifest created")
        elif report.get("ok"):
            print("[server] integrity: OK")
        else:
            from core.logging import log_event
            log_event(4, f"integrity alert at boot: {report.get('reason', 'tamper detected')}",
                      source="security", action="boot_integrity")
            print(f"[server] INTEGRITY ALERT: {report}")
    except Exception as exc:  # noqa: BLE001
        from core.logging import log_event
        log_event(3, f"integrity scan skipped at boot: {exc}", source="security",
                  action="boot_integrity")
        print(f"[server] integrity scan skipped: {exc}")

    # INDEX: rebuild EVERY profile's vault table-of-contents at boot — an
    # empty/stale index silently breaks semantic retrieval (the observed
    # degradation). THE 08-17 ALL-PROFILE FIX (the Operator's doctrine):
    # profiles are individual — every profile that HAS a vault must get its
    # index created/refreshed on startup, not just the active one.
    try:
        from core.db import build_index, connect_vault
        from intelligence.profiles import list_profiles
        # The active profile's vault (always built).
        built = []
        try:
            built.append(build_index(profile or "").get("entries", 0))
        except Exception:
            pass
        # Every other profile that has a vault file.
        for _p in list_profiles():
            try:
                _n = _p.name
                if _n and (_n != (profile or "") and _n != ((profile or "").lstrip("."))):
                    _vp = connect_vault(_n)
                    _has = False
                    try:
                        _c = _vp.execute(
                            "SELECT 1 FROM entries LIMIT 1").fetchone()
                        _has = _c is not None
                    finally:
                        _vp.close()
                    if _has:
                        built.append(build_index(_n).get("entries", 0))
            except Exception:
                pass
        from core.logging import log_event
        log_event(2, f"index rebuilt at boot: {len(built)} profile vaults "
                     f"({sum(built)} entries total)",
                  source="db", action="boot_index")
    except Exception as exc:  # noqa: BLE001
        from core.logging import log_event
        log_event(4, f"index rebuild failed at boot: {exc}", source="db",
                  action="boot_index")

    # Run the loop on a thread so a Ctrl+C can land cleanly.
    thread = threading.Thread(target=loop.run_forever, args=(stop_event,), daemon=True)
    thread.start()

    try:
        while not stop_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop_event.set()
        loop.stop()

    thread.join(timeout=5)
    print(f"[server] stopped after {loop.ticks} ticks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

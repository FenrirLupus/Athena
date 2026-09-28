"""Athena Service — the systemd user unit manager (the Operator's spec).

Like the gateway service commands, adapted to Athena's server:
the `athena.service` USER unit runs `athena web` (the FastAPI door +
in-process loop). This module wraps systemctl --user so the CLI can:

    athena service start | stop | restart | status | install | uninstall

The unit is crash-safe (Restart=on-failure) and boot-safe (enabled).
"""
from __future__ import annotations

import subprocess

def _whoami() -> str:
    """The current user (for the system unit's User= template)."""
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return "root"


SERVICE_NAME = "athena.service"
# The detected systemd level: "system" (plain systemctl) or "user"
# (systemctl --user). None until first use; cached for the process.
_SERVICE_LEVEL = None


def _systemctl(*args: str) -> dict:
    """Run a systemctl command at the ACTIVE level.

    the Operator's spec: Athena is controllable by plain `systemctl` when the
    system-level unit is installed (/etc/systemd/system/athena.service);
    otherwise it falls back to the user unit (systemctl --user). The
    level is detected once and cached for the process.
    """
    global _SERVICE_LEVEL
    if _SERVICE_LEVEL is None:
        try:
            chk = subprocess.run(
                ["systemctl", "list-unit-files", "athena.service"],
                capture_output=True, text=True, timeout=15,
            )
            _SERVICE_LEVEL = ("system" if "athena.service" in (chk.stdout or "")
                              else "user")
        except Exception:
            _SERVICE_LEVEL = "user"
    cmd = ["systemctl"] if _SERVICE_LEVEL == "system" else ["systemctl", "--user"]
    try:
        out = subprocess.run(
            [*cmd, *args],
            capture_output=True, text=True, timeout=30,
        )
        detail = (out.stdout or out.stderr or "").strip()
        if out.returncode != 0:
            _log(4, f"systemctl {_SERVICE_LEVEL} {' '.join(args)} failed: {detail}")
        return {"ok": out.returncode == 0, "detail": detail or "done"}
    except Exception as exc:
        _log(4, f"systemctl {_SERVICE_LEVEL} {' '.join(args)} error: {exc}")
        return {"ok": False, "detail": str(exc)}


def _log(level: int, msg: str) -> None:
    """The service is operational — failures are logged."""
    try:
        from metrics.logger import log
        log(level, msg, source="service")
    except Exception:
        pass


def is_installed() -> bool:
    """The unit file exists in the user's systemd config."""
    from pathlib import Path
    return (Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME
            ).exists()


def install() -> dict:
    """Copy the unit + enable. Returns {ok, detail}."""
    from pathlib import Path
    import shutil
    src = (Path(__file__).parent.parent / SERVICE_NAME)
    dest_dir = Path.home() / ".config" / "systemd" / "user"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        # THE 08-16 PORTABILITY FIX: substitute the @ATHENA_BIN@ template
        # with this machine's ~/.local/bin (the unit is a pure template —
        # no hardcoded paths).
        unit_text = src.read_text(encoding="utf-8")
        unit_text = unit_text.replace(
            "@ATHENA_BIN@", str(Path.home() / ".local" / "bin"))
        (dest_dir / SERVICE_NAME).write_text(unit_text, encoding="utf-8")
        r1 = _systemctl("daemon-reload")
        r2 = _systemctl("enable", "--now", SERVICE_NAME)
        if r1["ok"] and r2["ok"]:
            return {"ok": True, "detail": f"{SERVICE_NAME} installed + enabled"}
        return {"ok": False, "detail": f"{r1['detail']} {r2['detail']}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def uninstall() -> dict:
    """Remove the USER unit (the default install target).

    THE 08-16 FIX: `athena service uninstall` removes the user unit that
    `athena service install` created — it must NOT require --system.
    """
    from pathlib import Path
    unit = Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME
    try:
        # Stop + disable first (ignore errors if not running).
        _systemctl("stop", SERVICE_NAME)
        _systemctl("disable", SERVICE_NAME)
        if unit.exists():
            unit.unlink()
        _systemctl("daemon-reload")
        return {"ok": True,
                "detail": f"{SERVICE_NAME} removed (the user unit)"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def install_system() -> dict:
    """Install Athena SYSTEM-WIDE (the Operator's spec): the service unit at
    /etc/systemd/system/athena.service, driven by PLAIN `systemctl`
    (no --user). `athena` the COMMAND stays the user's manual launcher.

    Needs root — this uses `sudo` so the user's password prompt appears
    when THEY run it (the service never handles credentials).
    """
    from pathlib import Path
    import shutil
    src = (Path(__file__).parent.parent / "athena-system.service")
    try:
        def _sudo(args: list, timeout: int = 60) -> dict:
            out = subprocess.run(["sudo", *args], capture_output=True,
                                 text=True, timeout=timeout)
            return {"ok": out.returncode == 0,
                    "detail": (out.stdout or out.stderr or "").strip()}

        # THE 08-16 PORTABILITY FIX: substitute the templates BEFORE the
        # sudo cp (the units are pure templates — no hardcoded paths).
        unit_text = src.read_text(encoding="utf-8")
        unit_text = unit_text.replace(
            "@ATHENA_BIN@", str(Path.home() / ".local" / "bin"))
        unit_text = unit_text.replace("@ATHENA_USER@", _whoami())
        import tempfile as _tf
        _tmp = _tf.NamedTemporaryFile("w", suffix=".service", delete=False)
        _tmp.write(unit_text)
        _tmp.close()
        r0 = _sudo(["cp", _tmp.name, "/etc/systemd/system/athena.service"])
        try:
            import os as _os
            _os.unlink(_tmp.name)
        except Exception:
            pass
        if not r0["ok"]:
            return {"ok": False, "detail": r0["detail"] or "sudo cp failed"}
        r1 = _sudo(["systemctl", "daemon-reload"])
        r2 = _sudo(["systemctl", "enable", "--now", "athena.service"])
        if r1["ok"] and r2["ok"]:
            return {"ok": True,
                    "detail": "athena.service installed SYSTEM-WIDE (plain systemctl)"}
        return {"ok": False, "detail": f"{r1['detail']} {r2['detail']}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def uninstall_system() -> dict:
    """Remove the SYSTEM-WIDE unit (plain systemctl path). Needs sudo.

    THE 08-16 FIX: when the system unit was NEVER installed (only the
    user unit), `systemctl disable` reports "Unit does not exist" — that
    is NOT a failure. We still remove the file + reload, and report ok.
    """
    try:
        # The unit file: does it exist?
        exists = subprocess.run(
            ["bash", "-c", "test -f /etc/systemd/system/athena.service && echo yes"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() == "yes"
        out = subprocess.run(
            ["sudo", "systemctl", "disable", "--now", "athena.service"],
            capture_output=True, text=True, timeout=60,
        )
        rm = subprocess.run(
            ["sudo", "rm", "-f", "/etc/systemd/system/athena.service"],
            capture_output=True, text=True, timeout=30,
        )
        subprocess.run(["sudo", "systemctl", "daemon-reload"],
                       capture_output=True, text=True, timeout=30)
        # The disable "Unit does not exist" error is EXPECTED when the
        # system unit was never installed — the removal is still done.
        disable_ok = out.returncode == 0 or "does not exist" in out.stderr
        if disable_ok and (rm.returncode == 0 or not exists):
            return {"ok": True, "detail": "system-wide athena.service removed"}
        return {"ok": False,
                "detail": f"{out.stderr or ''} {rm.stderr or ''}".strip()}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def _clear_cache() -> None:
    """Clear the GUI in-memory cache on the RUNNING server (best effort).

    the Operator's spec: start/stop/restart frees the cache so the website
    ALWAYS shows the files on disk. A stop/restart kills the process
    (its memory is freed anyway); the clear matters when the server is
    already up — e.g. `athena service restart` after editing css/js/html
    must not serve stale files.
    """
    try:
        from core.config import load_config
        port = int(load_config().get("server", {}).get("port", 51420))
        subprocess.run(
            ["curl", "-s", "-m", "3", f"http://127.0.0.1:{port}/gui/__cache_clear__"],
            capture_output=True, timeout=8,
        )
    except Exception:
        pass  # best effort — the cache is mtime-validated anyway


def start() -> dict:
    _clear_cache()  # free any stale cached files before boot
    r = _systemctl("start", SERVICE_NAME)
    return {"ok": r["ok"], "detail": r["detail"] or "started"}


def stop() -> dict:
    _clear_cache()  # free the cache (the process is about to die anyway)
    r = _systemctl("stop", SERVICE_NAME)
    return {"ok": r["ok"], "detail": r["detail"] or "stopped"}


def restart() -> dict:
    _clear_cache()  # the running server must not serve stale files
    r = _systemctl("restart", SERVICE_NAME)
    return {"ok": r["ok"], "detail": r["detail"] or "restarted"}


def status() -> dict:
    active = _systemctl("is-active", SERVICE_NAME)
    pid = ""
    try:
        pid = subprocess.run(
            ["systemctl", "--user", "show", SERVICE_NAME, "-p", "MainPID",
             "--value"], capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    except Exception:
        pass
    return {
        "service": SERVICE_NAME,
        "active": active["ok"],
        "state": active["detail"] or "unknown",
        "pid": pid,
        "installed": is_installed(),
    }


def set_title(name: str = "Athena Service") -> None:
    """Set the process title (what the system monitor shows).

    Linux prctl(PR_SET_NAME) — the comm name the system monitor displays.
    Best-effort: never fails the boot.
    """
    import ctypes
    import sys
    if not sys.platform.startswith("linux"):
        return
    try:
        # prctl(PR_SET_NAME=15, name, 0, 0, 0) — max 15 chars + NUL.
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(15, name.encode("utf-8")[:15], 0, 0, 0)
    except Exception:
        pass
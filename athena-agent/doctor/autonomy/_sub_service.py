"""Service test — the Athena Service daemon (the Operator's spec).

Like the gateway service commands adapted to Athena: the
systemd USER unit runs `athena web`, and the process shows as
"Athena Service" in the system monitor.
"""
from __future__ import annotations


def run() -> list[dict]:
    from core import service

    checks = []

    # 1. The service module exposes the gateway-style lifecycle.
    for name in ("start", "stop", "restart", "status", "install"):
        checks.append({
            "name": f"service: {name} exposed",
            "status": "ok" if callable(getattr(service, name, None)) else "fail",
            "detail": f"core.service.{name}",
        })

    # 1b. The SYSTEM-WIDE path (the Operator's spec): `athena service
    #     install --system` puts the unit at /etc/systemd/system so PLAIN
    #     `systemctl` (no --user) drives her; the launchers/ repo carries
    #     the system unit file.
    checks.append({
        "name": "service: system-wide install/uninstall exposed",
        "status": "ok" if callable(getattr(service, "install_system", None))
        and callable(getattr(service, "uninstall_system", None)) else "fail",
        "detail": "core.service.install_system / uninstall_system",
    })
    from pathlib import Path
    sys_unit = (Path(__file__).parent.parent.parent / "athena-system.service")
    checks.append({
        "name": "service: system unit staged at the root",
        "status": "ok" if sys_unit.exists()
        and "WantedBy=multi-user.target" in sys_unit.read_text(encoding="utf-8")
        else "fail",
        "detail": "athena-system.service (systemctl, no --user)",
    })

    # 2. The unit file points at `athena web` (the FastAPI door).
    from pathlib import Path
    unit = (Path(__file__).parent.parent.parent / "athena.service")
    if unit.exists():
        text = unit.read_text(encoding="utf-8")
        ok_web = "athena web" in text
        ok_restart = "Restart=on-failure" in text
        checks.append({
            "name": "service unit: athena web + auto-restart",
            "status": "ok" if ok_web and ok_restart else "fail",
            "detail": f"web={ok_web} restart={ok_restart}",
        })
    else:
        checks.append({
            "name": "service unit file exists",
            "status": "fail",
            "detail": str(unit),
        })

    # 3. The process title sets "Athena Service" (system monitor name).
    import inspect
    src = inspect.getsource(service.set_title)
    checks.append({
        "name": "service: process title = Athena Service",
        "status": "ok" if "Athena Service" in src
        and "prctl" in src else "fail",
        "detail": "prctl(PR_SET_NAME) — the system-monitor comm",
    })

    # 4. The web boot sets the title.
    import athena
    athena_src = inspect.getsource(athena._run_gui)
    checks.append({
        "name": "web boot sets the service title",
        "status": "ok" if "set_title" in athena_src
        and "Athena Service" in athena_src else "fail",
        "detail": "_run_gui calls set_title('Athena Service')",
    })
    return checks

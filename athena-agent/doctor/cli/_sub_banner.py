"""CLI banner test — the ASCII logo + flow diagram render."""
from __future__ import annotations


def run() -> list[dict]:
    from cli.banner import ATHENA_LOGO, FLOW, print_banner, banner_text

    checks = []
    # The logo is the ASCII block letters (spell ATHENA).
    logo_letters = ATHENA_LOGO.replace("█", "").replace("╗", "").replace("╔", "")
    checks.append({
        "name": "ASCII logo present",
        "status": "ok" if len(ATHENA_LOGO.splitlines()) >= 5 else "fail",
        "detail": f"logo lines={len(ATHENA_LOGO.splitlines())}",
    })
    # The flow diagram: User ›› Thinking ›› Assistant.
    checks.append({
        "name": "flow diagram present",
        "status": "ok" if "User" in FLOW and "Thinking" in FLOW and "Assistant" in FLOW else "fail",
        "detail": FLOW[:60],
    })
    # The banner prints without raising (rich + plain both).
    import io
    from rich.console import Console
    buf = io.StringIO()
    c = Console(file=buf, force_terminal=False, width=80)
    try:
        c.print(ATHENA_LOGO)
        c.print(FLOW)
        ok_render = len(buf.getvalue()) > 100
    except Exception:
        ok_render = False
    checks.append({
        "name": "banner renders",
        "status": "ok" if ok_render else "fail",
        "detail": f"{len(buf.getvalue())} chars rendered",
    })
    checks.append({
        "name": "plain banner fallback",
        "status": "ok" if "ATHENA" in banner_text() else "fail",
        "detail": banner_text()[:40],
    })
    # The status section: provider, model, platform, server, runtime,
    # tools, skills, plugins — the big welcome info block.
    from cli.banner import build_status_section, hotbar_text
    import io
    from rich.console import Console
    buf = io.StringIO()
    c = Console(file=buf, force_terminal=False, width=80)
    info = build_status_section(c)
    joined = "\n".join(info)
    fields = ["Provider", "Model", "Platform", "Version", "Server", "Runtime",
              "Tools", "Skills", "Plugins"]
    present = [f for f in fields if f in joined]
    checks.append({
        "name": "status section fields",
        "status": "ok" if len(present) >= 7 else "fail",
        "detail": f"{len(present)}/{len(fields)}: {present}",
    })
    # The theme: red/orange/yellow only in the status markup.
    import re as _re
    hexes = set(_re.findall(r"#([0-9A-Fa-f]{6})", joined))
    theme_ok = hexes <= {"FF3B30", "FFA500", "FFD700"}
    checks.append({
        "name": "theme = red/orange/yellow only",
        "status": "ok" if theme_ok else "fail",
        "detail": f"colors={sorted(hexes)}",
    })
    # The hotbar renders the command vocabulary as PLAIN text (no markup).
    from cli.banner import hotbar_plain
    hb = hotbar_plain()
    checks.append({
        "name": "hotbar present",
        "status": "ok" if "kanban" in hb and "doctor" in hb and "quit" in hb
        and "[" not in hb else "fail",
        "detail": hb[:60],
    })
    # The thinking spinner + progress bar exist and run.
    from cli.banner import thinking_spinner, progress_bar
    import time
    with thinking_spinner(c) as spin:
        spin("thinking test")
        time.sleep(0.05)
    with progress_bar(c, total=3, description="progress test") as bar:
        bar.advance(1)
        bar.advance(2)
    checks.append({
        "name": "thinking + progress render",
        "status": "ok",
        "detail": "spinner and progress bar ran",
    })
    return checks

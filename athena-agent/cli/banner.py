"""Banner — the ASCII art + rich visuals for the Athena CLI.

Mirrors the banner approach: rich-styled ASCII block letters with
a gold/amber gradient, plus the session header. prompt_toolkit and rich
are imported lazily so the module never blocks on their availability.
"""
from __future__ import annotations

# The Athena logo — ASCII block letters in HER theme: red → orange → yellow.
ATHENA_LOGO = """[bold #FF3B30]  █████╗ ████████╗██╗  ██╗███████╗███╗   ██╗ █████╗ [/]
[bold #FF3B30] ██╔══██╗╚══██╔══╝██║  ██║██╔════╝████╗  ██║██╔══██╗[/]
[bold #FF8C00] ███████║   ██║   ███████║█████╗  ██╔██╗ ██║███████║[/]
[bold #FF8C00] ██╔══██║   ██║   ██╔══██║██╔══╝  ██║╚██╗██║██╔══██║[/]
[bold #FFCC00] ██║  ██║   ██║   ██║  ██║███████╗██║ ╚████║██║  ██║[/]
[bold #FFCC00] ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝[/]"""

# The flow diagram the Operator specified: User → Thinking → Assistant.
# Red = operator (User side), Orange = Thinking, Yellow = the agent
# (Assistant side). The agent's name appears BOTH as the thinking
# subject and the Assistant side.
def flow_markup(agent: str = "Assistant", operator: str = "User") -> str:
    """The flow diagram markup: {operator} ›› {agent} is Thinking ››
    {agent} — the theme roles (the Operator's 08-16 spec):
      USER (operator) = YELLOW, THINKING = ORANGE, AGENT = RED."""
    return (f"[bold #FFCC00]{operator}[/]  [dim]››[/]  "
            f"[bold #FF8C00]{agent} is Thinking[/]  [dim]››[/]  "
            f"[bold #FF3B30]{agent}[/]")

# The static fallback flow (no config loaded): User ›› Assistant is
# Thinking ›› Assistant — the Operator's fallback names.
FLOW = flow_markup("Assistant", "User")

def banner_art() -> str:
    """The Athena logo as PLAIN text (no rich markup) — for the CLI
    persistent-window body seed (the no-flicker welcome)."""
    import re as _re
    return _re.sub(r"\[[^\]]*\]", "", ATHENA_LOGO).rstrip()

def banner_rows() -> list[tuple[str, str]]:
    """The logo as PER-ROW styled lines — each row carries its gradient
    style (the 08-16 5-tone banner: red → red-orange → orange →
    orange-yellow → yellow). The persistent window renders each row with
    its own color instead of a flat single-color strip."""
    import re as _re
    rows: list[tuple[str, str]] = []
    for line in ATHENA_LOGO.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _re.search(r"#([0-9A-Fa-f]{6})", line)
        if m:
            plain = _re.sub(r"\[/?[a-zA-Z#0-9 _-]*\]", "", line)
            rows.append((f"class:body-banner-{m.group(1).lower()}", plain))
        else:
            rows.append(("class:body-banner",
                         _re.sub(r"\[/?[a-zA-Z#0-9 _-]*\]", "", line)))
    return rows

def print_banner(profile: str = "default", version: str = "") -> None:
    """Print the Athena banner: logo, flow, profile + version header.

    Uses the configured identity names: {operator} ›› {agent} is
    Thinking ›› {agent} (fallbacks: User / Assistant).
    """
    try:
        from rich.console import Console
    except Exception:
        return
    console = Console()
    console.print(ATHENA_LOGO)
    console.print()
    try:
        from core.config import flow_names
        agent, operator = flow_names()
        console.print(flow_markup(agent, operator))
    except Exception:
        console.print(FLOW)
    console.print()
    tag = f"[bold]{profile}[/]" if profile and profile != "default" else "[bold]default[/]"
    line = f"[dim]Athena[/] [bold #FFCC00]·[/] profile {tag}"
    if version:
        line += f" [bold #FFCC00]·[/] [dim]v{version}[/]"
    console.print(line)
    console.print()

def banner_text() -> str:
    """The plain-text banner (no rich) for non-TTY output."""
    return "ATHENA — User ›› Thinking ›› Assistant"

def clear_screen() -> None:
    """Clear the terminal (cls) before the welcome message.

    Uses ANSI clear (works on both Linux and Windows terminals); falls
    back to os.system('cls'/'clear') on older shells.
    """
    try:
        import os
        if os.name == "nt":
            os.system("cls")
        else:
            os.system("clear")
    except Exception:
        # Last resort: the ANSI escape sequence.
        print("\x1b[2J\x1b[H", end="")

# -- The status section (welcome panel) ---------------------

def build_status_section(console=None) -> list[str]:
    """Gather the status data: providers, model, platform, server, runtime,
    tools, skills, plugins, update check — the big welcome info block."""
    info: list[str] = []
    try:
        from providers.selection import summary as sel_summary
        from core.config import load_config, VERSION
        from core.db import health
        import platform as _p

        cfg = load_config()
        sel = sel_summary(cfg)
        reason = sel.get("types", {}).get("reason", {})
        # Provider / model
        provider = reason.get("provider") or "none"
        model = reason.get("model") or "not set"
        info.append(f"[bold #FF3B30]Provider:[/] {provider}")
        info.append(f"[bold #FF8C00]Model:[/]    {model}")
        info.append(f"[bold #FFCC00]Platform:[/] {_p.system()} {_p.release()}")
        info.append(f"[bold #FF3B30]Version:[/]  v{VERSION}")
        # Server / runtime status
        try:
            h = health()
            info.append(f"[bold #FF8C00]Server:[/]   {'online' if h.get('vault') else 'degraded'}")
            info.append(f"[bold #FFCC00]Runtime:[/]  {'ready' if h.get('sessions_dir') else 'no sessions'}")
        except Exception:
            info.append("[bold #FF3B30]Server:[/]   unknown")
    except Exception as exc:
        info.append(f"[dim]status gathering failed: {exc}[/]")

    # Tools
    try:
        from filesystem.tools import TOOLS
        names = sorted(TOOLS.keys())
        info.append(f"[bold #FF8C00]Tools:[/]    {len(names)} — " + ", ".join(names[:10]) +
                    ("…" if len(names) > 10 else ""))
    except Exception:
        info.append("[bold #FF8C00]Tools:[/]    0")

    # Skills
    try:
        from intelligence.skills import load_skills
        skills = load_skills()
        info.append(f"[bold #FFCC00]Skills:[/]   {len(skills)} — " +
                    ", ".join(getattr(s, "name", str(s)) for s in skills[:8]) +
                    ("…" if len(skills) > 8 else ""))
    except Exception:
        info.append("[bold #FFCC00]Skills:[/]   0")

    # Plugins
    try:
        from intelligence.plugins import discover_plugins
        plugins = discover_plugins()
        names = [getattr(p, "name", str(p)) for p in plugins]
        info.append(f"[bold #FF3B30]Plugins:[/]  {len(names)} — " + ", ".join(names[:5]) +
                    ("…" if len(names) > 5 else ""))
    except Exception:
        info.append("[bold #FF3B30]Plugins:[/]  0")

    # Update check (best-effort)
    try:
        from data.snapshots import list_snapshots
        snaps = list_snapshots()
        if snaps:
            info.append(f"[bold #FF8C00]Snapshots:[/] {len(snaps)} in the 3-version window")
        else:
            info.append("[bold #FF8C00]Snapshots:[/] none yet — take one before changes")
    except Exception:
        pass

    return info

def strip_markup(text: str) -> str:
    """Strip Rich markup tags from a string (the 08-16 window fix): the
    persistent-window body renders its OWN colors — raw [bold #...] tags
    must never show as literal text."""
    import re as _re
    return _re.sub(r"\[/?[a-zA-Z#0-9 _-]*\]", "", text)

def build_status_plain() -> list[str]:
    """The status section as PLAIN text (no Rich markup) — for the CLI
    persistent-window body (the window themes it itself)."""
    try:
        return [strip_markup(l) for l in build_status_section()]
    except Exception:
        return []

def thinking_spinner(console=None):
    """A context manager that shows the ASCII dot spinner while running.

        with thinking_spinner() as spin:
            ... long work ...
            spin("almost done")

    Uses rich's Status with the braille dot frames (native).
    """
    from contextlib import contextmanager
    from rich.console import Console
    from rich.status import Status

    @contextmanager
    def _cm():
        c = console or Console()
        status = Status("[bold #FF8C00]thinking[/]", console=c, spinner="dots")
        status.start()
        try:
            yield lambda text: status.update(text)
        finally:
            status.stop()

    return _cm()

# -- Progress bars --------------------------------------------------------

def progress_bar(console=None, total: int = 100, description: str = ""):
    """A rich progress bar context manager (the Operator's spec).

        with progress_bar(total=10, description="repairing") as pbar:
            for i in range(10):
                ...
                pbar.advance(1)
    """
    from rich.console import Console
    from rich.progress import (Progress, BarColumn, TextColumn,
                               SpinnerColumn)
    console = console or Console()
    progress = Progress(
        SpinnerColumn(style="#FF3B30"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30, style="#FF3B30", complete_style="#FF8C00"),
        TextColumn("[bold #FFCC00]{task.percentage:>3.0f}%[/]"),
        console=console,
    )
    progress.start()
    task_id = progress.add_task(description or "working", total=total)
    bar = _ProgressBarHandle(progress, task_id)
    return bar

class _ProgressBarHandle:
    def __init__(self, progress, task_id):
        self._progress = progress
        self._task_id = task_id

    def advance(self, n: int = 1) -> None:
        self._progress.advance(self._task_id, n)

    def update(self, description: str = "", total: int = 0) -> None:
        kwargs = {}
        if description:
            kwargs["description"] = description
        if total:
            kwargs["total"] = total
        if kwargs:
            self._progress.update(self._task_id, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._progress.stop()

# -- The hotbar (bottom command bar) -------------------------

def hotbar_text(commands: list[str] | None = None) -> str:
    """The bottom command bar — the quick-access hotkeys/commands.

    Rendered as the prompt_toolkit bottom toolbar (native).
    """
    items = commands or ["kanban", "session", "cron", "profile", "doctor",
                         "nurse", "curator", "logs", "events", "help", "quit"]
    return "  ".join(f"[bold #FF3B30]{c}[/]" for c in items)

def hotbar_plain() -> str:
    """The hotbar as PLAIN text (no markup) for prompt_toolkit.

    Returns the command names joined with two spaces — the toolbar shows
    the commands, not the raw rich tags.
    """
    return "  ".join(["session", "kanban", "cron", "profile", "vault",
                      "doctor", "nurse", "logs", "theme", "help", "quit"])

def runtime_footer(profile: str = "default", session_id: str = "") -> str:
    """The CLI RUNTIME FOOTER (the hotbar status, 1:1 for CLI).

    A live status line under the hotbar: the active profile, the turn
    flow state (idle/thinking/responding), and the usage (tokens used vs
    the budget). The website has these features; the CLI gets the footer.
    """
    parts = [f"profile: {profile}"]
    # Turn flow state (the session state machine).
    try:
        from core.session_state import flow_of
        parts.append(f"flow: {flow_of(session_id or 'default')}")
    except Exception:
        pass
    # Token usage (the meter: since last compression, with the percent).
    try:
        from context.compression import usage_since_baseline
        from core.config import load_config
        _cfg = load_config()
        _budget = _cfg.get("iteration_budget", {}) or {}
        _avail = int(_budget.get("main_max_tokens", 5120) or 5120) * \
                 int(_budget.get("main_iterations", 100) or 100)
        _used = usage_since_baseline(profile if profile != "default" else "")
        _pct = (_used / _avail * 100.0) if _avail else 0.0
        parts.append(f"tokens: {_used:,} ({_pct:.1f}%)")
    except Exception:
        pass
    # Resource load (the resource monitor's latest).
    try:
        from core.resource_manager import latest
        snap = latest()
        parts.append(f"cpu: {snap['cpu'].get('load1', 0):.2f}")
        parts.append(f"mem: {snap['memory'].get('percent', 0):.0f}%")
    except Exception:
        pass
    return "  ·  ".join(parts)

# -- The live flow (tool/system/skill lines) ---------------

class LiveFlow:
    """The bottom-anchored status pattern (the Operator's spec):

    The THINKING spinner is ALWAYS at the bottom of the terminal. When a
    System/Tool/Skill event fires, its line is committed ABOVE the
    spinner (the spinner swaps down and stays at the bottom). On finish
    the spinner is replaced by the assistant's output.

        flow = LiveFlow()
        flow.start("Athena is thinking…")
        flow.event("tool", "read_file config.yaml")   # commits above
        flow.event("system", "gate fired: think")     # commits above
        flow.finish()                                  # spinner gone
    """
    def __init__(self, console=None):
        from rich.console import Console
        self.console = console or Console()
        self._active = False
        self._spinner_text = ""
        self._frame = 0

    def start(self, text: str = "Athena is thinking…") -> None:
        self._active = True
        self._spinner_text = text
        self._render_spinner()

    def _render_spinner(self) -> None:
        if not self._active:
            return
        try:
            frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            self._frame = (self._frame + 1) % len(frames)
            spinner = frames[self._frame]
            self.console.print(f"[#FF8C00]{spinner} {self._spinner_text}[/]", end="\r")
        except Exception:
            pass

    def event(self, kind: str, detail: str) -> None:
        """Commit a flow line ABOVE the spinner (which stays at bottom)."""
        if not self._active:
            return
        try:
            # Move up one line (to the spinner line), clear it, print the
            # committed event line, then re-render the spinner at bottom.
            self.console.print("\033[1A\033[K", end="")
            self.console.print(render_event_line(kind, detail))
            self._render_spinner()
        except Exception:
            pass

    def finish(self) -> None:
        """Clear the spinner line (the assistant's output follows)."""
        self._active = False
        try:
            self.console.print("\033[1A\033[K", end="")
        except Exception:
            pass

# The event emojis (the Operator's spec):
#   System: ⚙️   Tool: 🪛   Skill: 🖊️
#   Compression: 🗜️   Doctor: 💊   Nurse: 🩹   Saving: 💾   Loading: 💽
_EVENT_EMOJI = {
    "system": "⚙️",
    "tool": "🪛",
    "skill": "🖊️",
    "compression": "🗜️",
    "doctor": "💊",
    "nurse": "🩹",
    "saving": "💾",
    "loading": "💽",
}

# Tool-name → label (falls back to the 🪛 default per the Operator's spec).
_TOOL_LABELS = {
    "read_file": "read", "read": "read", "write": "write",
    "append": "append", "replace": "replace", "patch": "patch",
    "delete": "delete", "copy": "copy", "move": "move",
    "rename": "rename", "list": "list", "tree": "tree",
    "find": "find", "search": "search", "mkdir": "mkdir",
    "exists": "exists", "stat": "stat", "hash": "hash",
    "execute": "execute", "terminal": "shell", "process": "process",
    "kill": "kill", "download": "download", "upload": "upload",
    "compress": "compress", "extract": "extract",
    "memory_add": "remember", "memory_list": "memory",
    "vault_query": "vault", "vault_semantic": "vault",
    "vault_store": "vault-store", "web_search": "search",
    "web_extract": "extract",
}

def _event_emoji(kind: str, detail: str) -> str:
    """Pick the emoji: the Operator's defaults, with the specials recognized
    from the detail text (compression, doctor, nurse, saving, loading).

    The specials only apply to SYSTEM events — tool/skill events always
    use their own defaults (🪛 / 🖊️), never the keyword match.
    """
    if kind in ("tool", "skill"):
        return _EVENT_EMOJI.get(kind, "⚙️")
    # The specials: keyword match on the detail (case-insensitive).
    low = detail.lower()
    for key, emoji in (("compress", "🗜️"), ("doctor", "💊"), ("nurse", "🩹"),
                       ("sav", "💾"), ("load", "💽")):
        if key in low:
            return emoji
    return _EVENT_EMOJI.get(kind, "⚙️")

def render_event_line(kind: str, detail: str) -> str:
    """Render one live-flow line between the user's input and the output.

    Native: ``┊ <emoji> <label> <detail>`` — dim, quiet, stacked.
    Emojis are the Operator's set: System ⚙️, Tool 🪛, Skill 🖊️, plus the
    specials (Compression 🗜️, Doctor 💊, Nurse 🩹, Saving 💾, Loading 💽).
    Theme: the prefix is RED, the label ORANGE, the detail dim.
    """
    try:
        from rich.console import Console
        from rich.text import Text
        console = Console()
        if kind == "tool":
            # detail is "name args..." — split the tool name off
            parts = detail.split(" ", 1)
            name = parts[0]
            arg_preview = parts[1] if len(parts) > 1 else ""
            label = _TOOL_LABELS.get(name, name or "tool")
            if arg_preview and len(arg_preview) > 60:
                arg_preview = arg_preview[:57] + "..."
            text = Text()
            text.append("┊ ", style="#FF3B30")
            text.append(f"🪛 {label}", style="#FF8C00")
            if arg_preview:
                text.append("  " + arg_preview, style="dim")
            return text
        # Skill / System / specials: use the mapped emoji.
        emoji = _event_emoji(kind, detail)
        text = Text()
        text.append("┊ ", style="#FF3B30")
        text.append(f"{emoji} {kind}", style="#FF8C00")
        text.append("  " + detail, style="dim")
        return text
    except Exception:
        return f"┊ {kind}: {detail}"

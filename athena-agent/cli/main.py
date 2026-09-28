"""Athena CLI — the terminal door into the runtime.

Skeleton: a REPL that feeds events into the Runtime directly (in-process).
When the server's networking lands, this attaches to the live server instead.

Athena's colors: RED + ORANGE (the Operator's spec) — the [athena] tag is red,
the prompt and highlights are orange.

Usage:
    python3 -m cli.main            # from athena-system/
    python3 athena-system/cli/main.py

Commands:
    send <text>      feed a message into the runtime
    session          show the current session
    session new      start a fresh session
    health           check db + server state
    provider         list | catalog | add <name> [key]
    index            rebuild | list | query <category>
    quit             exit
"""
from __future__ import annotations

import readline
import sys
import uuid
from pathlib import Path

from core.conversation_loop import ConversationLoop
from core.db import health
from core import db as db_layer
from providers import setup
from providers.provider_catalog import list_catalog
from cli.colors import red, orange, yellow, bold, dim, green, blue

# -- Auto-complete (readline tab completion) --------------
# The registry is DYNAMIC: commands register via systems/commands.py and
# auto-complete reads it at startup and on /refresh. No static lists here.
from autonomy.commands import register_core_commands, list_commands, get_subcommands, refresh_commands

register_core_commands()

STATUSES = ["todo", "in_progress", "done", "blocked"]


def _completer(text: str, state: int) -> str | None:
    """readline completer: complete modules, subcommands, statuses."""
    line = readline.get_line_buffer()
    raw = line  # keep the raw line to detect trailing space
    tokens = line.strip().split()
    MODULES = list_commands()
    try:
        if not tokens:
            candidates = MODULES
        elif raw.endswith(" "):
            # The current token is complete — complete the NEXT arg.
            if len(tokens) == 1:
                # After "<module> " → subcommands (or next arg class)
                module = tokens[0].lstrip("/\\").lower()
                candidates = get_subcommands(module)
            elif len(tokens) == 2:
                module = tokens[0].lstrip("/\\").lower()
                sub = tokens[1]
                candidates = get_subcommands(module)
                if module == "kanban" and sub == "update":
                    candidates = STATUSES
            else:
                candidates = []
        elif len(tokens) == 1:
            candidates = [m for m in MODULES if m.startswith(tokens[0].lstrip("/\\"))]
        else:
            module = tokens[0].lstrip("/\\").lower()
            sub = tokens[-1]
            sub_candidates = get_subcommands(module)
            # Third token on kanban update → status values
            if module == "kanban" and len(tokens) >= 3 and tokens[1] == "update":
                sub_candidates = STATUSES
            if module == "kanban" and len(tokens) == 3 and tokens[1] == "add":
                sub_candidates = []  # title is free text
            candidates = [c for c in sub_candidates if c.startswith(sub)]
        return candidates[state] if state < len(candidates) else None
    except IndexError:
        return None




def _looks_like_schedule_part(tok: str) -> bool:
    """Is this token part of a schedule (cron field, interval, or ISO)?"""
    import re
    if re.match(r"^(\d{1,2}|\*|/\d+|,|-|\d+-\d+|\*/)\S*$", tok) or tok in ("*", "*/"):
        return True
    if re.match(r"^\d{4}-\d{2}-\d{2}", tok):  # ISO one-shot start
        return True
    if re.match(r"^(every|\d+[smhdw])$", tok, re.IGNORECASE):
        return True
    return False


def tag() -> str:
    """The red [athena] tag used at the start of lines."""
    return red("[athena]")


def multi_select(title: str, options: list[tuple[str, str]],
                 default: list[str] | None = None,
                 height: int = 12) -> list[str]:
    """THE ARROW-KEY MULTI-SELECT (the Operator's 08-16 spec).

    RICH-STYLED visuals + PROMPT_TOOLKIT functionality:
      · The list renders with the Athena theme — orange border, yellow
        title, the › cursor and [X] checkboxes.
      · prompt_toolkit's full-screen engine renders it — its DIFF-based
        redraw updates only the changed cells, so the menu NEVER
        duplicates (a single live copy, always).
      ↑ / ↓       — move between options
      space       — toggle the selection (the [X] boxes)
      enter       — confirm (return the selected values)
      backspace   — cancel (return nothing)
      CTRL+E      — exit the terminal (return None)
      CTRL+C      — cancel (return nothing)
    """
    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout, Window, FormattedTextControl
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.styles import Style
    except Exception:
        # Plain-text fallback (numbered list, comma-separated).
        print(f"\n{title}")
        for i, (val, label) in enumerate(options, 1):
            mark = "X" if val in (default or []) else " "
            print(f"  [{mark}] {i:2d}. {label}")
        try:
            raw = input("select numbers (comma-separated), enter for none: ").strip()
        except (EOFError, KeyboardInterrupt):
            return default or []
        if not raw:
            return default or []
        sel = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(options):
                sel.append(options[int(part) - 1][0])
        return sel

    # The state: checked values + the highlighted row.
    checked = set(default or [])
    index = [0]

    def _render() -> FormattedText:
        """The Athena-styled list — orange border, yellow title, the ›
        cursor at the number, [X] for checked. Prompt_toolkit renders
        this with DIFF-based redraw → one live copy, never duplicated.
        NOTE: line breaks go INSIDE the fragment text ("\\n" at the end) —
        prompt_toolkit ignores standalone ("\\n", "") fragments."""
        frags: list = []
        # Line 1: the title + hints.
        frags.append(("class:title", f"  {title}"))
        frags.append(("class:dim",
                      "  (↑/↓ move · space toggle · enter confirm · "
                      "backspace cancel · CTRL+E exit · CTRL+C cancel)\n"))
        # Line 2: the border.
        frags.append(("class:border", f"  {'─' * 60}\n"))
        # The numbered options — the cursor sits at the number.
        for i, (val, label) in enumerate(options):
            num = f"{i + 1:2d}"
            if i == index[0]:
                frags.append(("class:cursor", f"  › {num} "))
            else:
                frags.append(("", f"    {num} "))
            mark = ("class:checked", "[X]") if val in checked else ("class:unchecked", "[ ]")
            frags.append(mark)
            frags.append(("", f" {label}\n"))
        # The bottom border.
        frags.append(("class:border", f"  {'─' * 60}\n"))
        return FormattedText(frags)

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        index[0] = (index[0] - 1) % len(options)
        event.app.invalidate()

    @kb.add("down")
    def _(event):
        index[0] = (index[0] + 1) % len(options)
        event.app.invalidate()

    @kb.add("space")
    def _(event):
        val = options[index[0]][0]
        if val in checked:
            checked.discard(val)
        else:
            checked.add(val)
        event.app.invalidate()

    @kb.add("enter")
    def _(event):
        # THE CEO'S SPEC: enter CONFIRMS the selections directly.
        event.app.exit(result=sorted(checked))

    @kb.add("backspace")
    def _(event):
        # THE CEO'S SPEC: backspace CANCELS the whole selection.
        event.app.exit(result=[])

    @kb.add("c-e")
    def _(event):
        # THE CEO'S SPEC: CTRL+E EXITS the terminal (quits the setup).
        event.app.exit(result=None)

    @kb.add("c-c")
    def _(event):
        # THE CEO'S SPEC: CTRL+C CANCELS (same as backspace — nothing).
        event.app.exit(result=[])

    # THE ATHENA THEME (the 3-color doctrine): orange border/cursor,
    # yellow title, red X. The background stays the terminal's own.
    style = Style.from_dict({
        "title":     "bold #FFCC00",      # yellow — the highlight
        "border":    "#FF8C00",            # orange — the action color
        "cursor":    "bold #FF8C00",       # orange — the cursor
        "checked":   "bold #FF3B30",       # red — the X
        "unchecked": "#666666",
        "dim":       "#888888",
    })

    # THE FULL-SCREEN ENGINE: prompt_toolkit renders + diffs the layout,
    # so a keypress updates ONLY the changed cells — the menu keeps ONE
    # live copy (the CEO's no-duplication requirement, guaranteed by the
    # engine rather than manual clear tricks). The window is sized to
    # EXACTLY the content height (dont_extend_height) so every row
    # renders — never clipped, never blank.
    app = Application(
        layout=Layout(Window(FormattedTextControl(_render),
                             dont_extend_height=True,
                             wrap_lines=True)),
        key_bindings=kb,
        style=style,
        full_screen=True,
        mouse_support=False,
    )
    try:
        result = app.run()
        # THE EXIT SIGNAL: CTRL+E returns None (the wizard must QUIT).
        return result if (result is None or isinstance(result, list)) else (default or [])
    except Exception:
        # Final fallback: numbered input.
        print(f"\n{title}")
        for i, (val, label) in enumerate(options, 1):
            mark = "X" if val in checked else " "
            print(f"  [{mark}] {i:2d}. {label}")
        try:
            raw = input("select numbers (comma-separated), enter for none: ").strip()
        except (EOFError, KeyboardInterrupt):
            return default or []
        if not raw:
            return default or []
        sel = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(options):
                sel.append(options[int(part) - 1][0])
        return sel


class CLI:
    def __init__(self, profile: str = ""):
        # The live flow (Discord-style "Athena is thinking…" at the bottom,
        # with System/Tool/Skill lines committing above it). Created lazily
        # on first send so piped sessions stay clean.
        self._flow = None
        self._persist_window = None   # the persistent-window reply hook
        # The observer renders System/Tool/Skill events LIVE between the
        # user's input and the assistant's output (flow).
        self.loop = ConversationLoop(profile=profile or None,
                                     on_event=self._on_event,
                                     on_approval=self._on_approval)
        self.session_id = self.loop.session_id
        self.resumed = db_layer.find_last_session(profile=self.loop.profile.name) is not None
        self.profile = self.loop.profile
        # The server loop runs ALONGSIDE the REPL: gates, scheduler, nurse
        # watch, and metric logging keep ticking while the CLI is open.
        # Ctrl+C tears down BOTH (server + runtime) and closes the terminal.
        self._stop_event = None
        self._server_thread = None
        self._start_server()

    def _on_event(self, kind: str, detail: str) -> None:
        """The live-flow observer: a Discord-style typing line.

        'Athena is thinking…' stays anchored at the bottom; every
        System/Tool/Skill event commits a line ABOVE it and the spinner
        re-renders at the bottom (the Operator's spec).

        THE 08-16 PERSISTENT-WINDOW HOOK: when the full-screen CLI is
        active (no LiveFlow), the reply DELTAS stream into the body —
        the same live reply the website shows, in the terminal window.
        """
        try:
            if self._flow is not None:
                self._flow.event(kind, detail)
                return
            # The persistent window: live-render the thinking + reply.
            try:
                if self._persist_window:
                    if kind == "delta" and detail:
                        self._persist_window(detail)
                    elif kind in ("think", "skill", "tool", "system") and detail:
                        em = {"think": "💭", "skill": "🧠",
                              "tool": "🛠️", "system": "⚙️"}.get(kind, "•")
                        d = " ".join(str(detail).split())   # one line
                        self._persist_window(f"  {em} {d[:90]}", flow=True)
            except Exception:
                pass
        except Exception:
            pass

    def _on_approval(self, tool: str, arguments: dict, risk: str):
        """The interactive permission prompt (the Operator's spec).

        An unsafe tool needs a decision. Ask the user:
            verdict: allow | deny | block
            scope:   once | session | global
        Returns (verdict, scope). The gate persists per scope; ONCE
        grants this single call only.
        """
        try:
            from cli.colors import yellow, orange, red, dim
            print()
            print(red("⚠  Approval needed") + dim(f"  [{risk}]"))
            print(yellow(f"    Tool: {tool}"))
            if arguments:
                print(dim(f"    Args: {str(arguments)[:120]}"))
            print(dim("    Verdict: allow | deny | block"))
            print(dim("    Scope:   once | session | global"))
            print()
            verdict = input(orange("  verdict> ")).strip().lower() or "deny"
            scope = input(orange("  scope>   ")).strip().lower() or "once"
            if verdict not in ("allow", "deny", "block"):
                verdict = "deny"
            if scope not in ("once", "session", "global"):
                scope = "once"
            return verdict, scope
        except Exception:
            return "deny", "once"  # fail-closed on a broken prompt

    # -- Slash-command parsing (native) ---------------------------
    # A command starts with "/" or "\"; every part after the starter is a
    # STRING token: {/ or \} {module} {argument} {status}. e.g.
    #   \kanban list            → module=kanban, args=["list"]
    #   /status                 → module=status
    #   \cron add nightly 0 3 * * * "check the vault"
    # Each token is parsed with shlex so quoted strings stay one token.

    def _start_server(self) -> None:
        """Start the ServerLoop on a daemon thread (runs beside the REPL).

        The server owns the gates, scheduler, nurse watch, and metric
        logging — so a CLI session produces the SAME logs the server does.
        It is a daemon thread: it stops with the process, and we stop it
        explicitly on REPL exit (Ctrl+C / quit).
        """
        import threading
        from core.server_loop import ServerLoop
        from core.config import load_config

        cfg = load_config()
        self._server = ServerLoop(runtime=self.loop, config=cfg)
        # The CLI owns the session — the daemon server thread must not
        # close it, or the session splits into multiple log files.
        self._server.owns_session = False
        # INDEX: rebuild the vault table-of-contents so semantic retrieval
        # works from the first CLI turn (an empty index breaks it silently).
        try:
            from core.db import build_index
            build_index(self.profile.name)
        except Exception:
            pass
        self._stop_event = threading.Event()
        self._server_thread = threading.Thread(
            target=self._server.run_forever,
            args=(self._stop_event,),
            daemon=True,
        )
        self._server_thread.start()

    def _stop_server(self) -> None:
        """Stop the server loop + close its metric session (Ctrl+C path).

        The server-end entry is written EXPLICITLY here (source='server')
        so the log always shows 'server session ended' — even when the
        CLI tears the server down (the daemon thread's own finally may
        race with the session close).
        """
        try:
            from metrics.logger import log as metric_log
            metric_log(1, "server session ended", profile=self.profile.name, source="server")
        except Exception:
            pass
        try:
            if self._stop_event is not None:
                self._stop_event.set()
            if getattr(self, "_server", None) is not None:
                self._server.stop()
        except Exception:
            pass
        # The runtime (ConversationLoop) closes its session too.
        try:
            from metrics.logger import close_session
            close_session(self.profile.name)
        except Exception:
            pass

    def parse_command(self, line: str) -> tuple[str, list[str]] | None:
        """Split a line into (module, args). None if not a command.

        A line is a command when it:
          - starts with / or \\ (slash form), OR
          - begins with a REGISTERED command name (bare form — the Operator's
            model: 'model switch X' works without the slash).

        Everything else is chat.
        """
        import shlex

        stripped = line.strip()
        if not stripped:
            return None
        if stripped[0] in ("/", "\\"):
            try:
                tokens = shlex.split(stripped[1:])
            except ValueError:
                tokens = stripped[1:].split()
            if not tokens:
                return None
            return tokens[0].lower(), tokens[1:]
        # Bare form: the first word is a CORE command (not a tool name —
        # tools are for chat use; 'list the files' must stay chat).
        try:
            tokens = shlex.split(stripped)
        except ValueError:
            tokens = stripped.split()
        if not tokens:
            return None
        first = tokens[0].lower()
        if first in _core_command_names() or first in ("help", "quit", "exit"):
            return first, tokens[1:]
        return None

    def run_command(self, module: str, args: list[str]) -> bool:
        """Route a parsed slash command. Returns True if handled."""
        # THE ACTIVITY TRAIL (the Operator's 08-16 spec): every CLI command
        # is recorded as L2 (the audit/activity stream — the curator reads
        # it; the nurse ignores L1-2). Failures are logged separately as L4
        # by the REPL paths.
        try:
            from core.logging import log_event
            log_event(2, f"cli command '{module}'",
                      source="cli", action=f"cmd_{module}",
                      target=" ".join(args),
                      profile=self.profile.name)
        except Exception:
            pass
        if module in ("quit", "exit"):
            raise SystemExit(0)
        elif module in ("send", "say", "msg"):
            text = " ".join(args)
            if text:
                self.cmd_send(text)
        elif module in ("session", "sessions"):
            self.cmd_session(args)
        elif module in ("health", "status"):
            self.cmd_health()
        elif module in ("provider", "providers", "model", "auth"):
            if module == "model":
                # /model list | /model switch <name> — model-first form.
                self.cmd_provider(["model"] + (args if args else ["list"]))
            else:
                self.cmd_provider(args)
        elif module in ("emotion", "feelings", "mood"):
            self.cmd_emotion(args)
        elif module in ("mdformat", "fmt", "md-fmt"):
            self.cmd_mdformat(args)
        elif module in ("index", "vault"):
            self.cmd_index(args)
        elif module in ("kanban", "board"):
            self.cmd_kanban(args)
        elif module in ("cron", "schedule", "jobs"):
            self.cmd_cron(args)
        elif module in ("profile", "profiles", "agent"):
            self.cmd_profile(args)
        elif module in ("security", "integrity"):
            self.cmd_security(args)
        elif module in ("backup",):
            self.cmd_backup(args)
        elif module in ("snapshot", "snap"):
            self.cmd_snapshot(args)
        elif module in ("rollback", "rb"):
            self.cmd_rollback(args)
        elif module in ("patch",):
            self.cmd_patch(args)
        elif module in ("skills",):
            self.cmd_skills()
        elif module in ("plugins",):
            self.cmd_plugins()
        elif module in ("tools",):
            self.cmd_tools()
        elif module in ("config",):
            self.cmd_config()
        elif module in ("streaming", "stream"):
            self.cmd_streaming(args)
        elif module in ("version",):
            self.cmd_version()
        elif module in ("doctor",):
            self.cmd_doctor()
        elif module in ("wipe-test", "wipetest", "wipe"):
            self.cmd_wipe_test(args)
        elif module in ("release-propose", "release", "relpropose"):
            self.cmd_release_propose(args)
        elif module in ("wiki",):
            self.cmd_wiki(args)
        elif module in ("logs",):
            if args and args[0] in ("--color", "-c"):
                self.cmd_logs_color(args[1:])
            else:
                self.cmd_logs(args)
        elif module in ("lifecycle", "life", "lc"):
            self.cmd_lifecycle(args)
        elif module in ("nurse", "heal", "repair"):
            self.cmd_nurse(args)
        elif module in ("events", "activity"):
            self.cmd_events(args)
        elif module in ("help",):
            self.cmd_help()
        elif module in ("theme", "darkmode", "lightmode"):
            self.cmd_theme(args)
        else:
            print(f"{tag()} {red('unknown command')}: {module}")
        return True

    def cmd_send(self, text: str) -> None:
        # THE 08-16 CLI METRICS (the Operator's diagnosability spec): every
        # chat turn logs into the metric stream — the operator's input
        # length + the reply summary (tokens, flow calls). A diagnosable
        # CLI produces the SAME behavioral truth the server does.
        try:
            from metrics.logger import log as metric_log
            metric_log(2, f"cli turn: user='{text[:80]}{'…' if len(text) > 80 else ''}'",
                       profile=self.profile.name, source="cli", tool="chat",
                       action="turn_send")
        except Exception:
            pass
        # The CLI is Athena's own terminal: chat runs on the SYSTEM
        # channel (the full toolbox — all 25 wrappers, terminal, execute).
        # The user channel (read + memory + vault) is for external users.
        event = {
            "session_id": self.session_id,
            "content": text,
            "channel": "system",
        }
        ack = self.loop.handle_event(event)
        # The Discord-style typing line runs during the turn; finish it
        # (clear the spinner) before the reply prints.
        if self._flow is not None:
            try:
                self._flow.finish()
            except Exception:
                pass
        self.loop.drain()
        for response in self.loop.responses:
            if response.get("event_id") == ack.get("event_id"):
                # THE THINKING FLOW (the Operator's 08-12 spec,
                # adapted): print the turn's calls before the reply —
                # Agent ›› Thinking (the calls) ›› Response. The flow
                # carries only real tool/system calls (skills are
                # context, never shown).
                flow = response.get("flow") or []
                if flow:
                    # THE 08-16 PERSISTENT-WINDOW PATH: the thinking-flow
                    # lines go INTO the body, never printed outside. The
                    # tag is PLAIN "[athena]" (no ANSI escapes — the
                    # window renders its own colors; escapes would show
                    # literally as ^[[31m).
                    from cli.colors import is_dark_mode
                    _plain_tag = "[athena]"
                    flow_lines = [f"{_plain_tag} Thinking:"]
                    for call in flow:
                        em = {'system': '⚙️', 'tool': '🛠️'}.get(call.get('kind'), '•')
                        name = call.get('name') or 'call'
                        line = f"  {em} {name}"
                        # THE 08-16 THINKING-BLOCK FIX: the args are
                        # summarized as key=value (no raw JSON dump) and
                        # the result as a single-line preview (newlines
                        # collapsed) — the block stays clean + compact.
                        if call.get('args'):
                            a = call['args']
                            if isinstance(a, dict):
                                parts = [f"{k}={v}" for k, v in
                                         list(a.items())[:3]]
                                brief = ", ".join(parts)
                                if len(a) > 3:
                                    brief += ", …"
                            else:
                                brief = str(a)
                            line += f" — {brief[:70]}"
                        if call.get('result'):
                            r = str(call['result'])
                            r = " ".join(r.split())   # collapse newlines
                            line += f" → {r[:60]}"
                        flow_lines.append(line)
                    try:
                        if self._persist_window:
                            for fl in flow_lines:
                                self._persist_window(fl + "\n", flow=True)
                        else:
                            # The plain-CLI fallback: use the colored tag.
                            for fl in flow_lines:
                                print(fl.replace(_plain_tag, tag()))
                    except Exception:
                        for fl in flow_lines:
                            print(fl)
                # THE 08-16 PERSISTENT-WINDOW PATH: the reply goes INTO
                # the body (the window), never printed outside it.
                reply = response.get("reply") or ""
                # THE 08-16 REPLY METRIC: the turn's outcome (reply length,
                # flow calls) — diagnosable CLI behavior.
                try:
                    from metrics.logger import log as metric_log
                    metric_log(1, f"cli turn done: reply={len(reply)} chars "
                                  f"flow={len(response.get('flow') or [])}",
                               profile=self.profile.name, source="cli",
                               tool="chat", action="turn_done")
                except Exception:
                    pass
                try:
                    if self._persist_window:
                        self._persist_window(reply, final=True)
                        continue
                except Exception:
                    pass
                print(f"{tag()} {reply}")
            else:
                # A response for a DIFFERENT event — ignore it.
                continue

    def cmd_session(self, args: list[str]) -> None:
        if args and args[0] == "new":
            db_layer.set_session_state(self.session_id, "ended", profile=self.profile.name)
            self.session_id = str(uuid.uuid4())
            self.loop.session_id = self.session_id
            print(f"{tag()} fresh session: {orange(self.session_id)}")
        elif args and args[0] == "list":
            for sid in db_layer.uuid_session_ids(profile=self.profile.name):
                marker = "*" if sid == self.session_id else " "
                print(f"{tag()} {orange(marker)} {sid}")
        else:
            print(f"{tag()} session: {orange(self.session_id)}")

    def cmd_health(self) -> None:
        h = health(profile=self.profile.name)
        state = orange("OK") if all(h.values()) else red("PROBLEM")
        print(f"{tag()} db health: {state} {h}")

    def cmd_provider(self, args: list[str]) -> None:
        """provider list | catalog | add | switch <name> | model <sub>"""
        from providers import switch as switch_mod

        sub = args[0].lower() if args else "list"
        if sub == "list":
            info = switch_mod.list_providers()
            if not info["providers"]:
                print(f"{tag()} no providers configured yet — {orange('provider add <name>')}")
                return
            for p in info["providers"]:
                marker = "►" if p.get("primary") else " "
                am = switch_mod.active_model_for(p["name"])
                print(f"{tag()} {marker} {orange(p['name'])}: "
                      f"active model {orange(am or '(default)')} | {len(p.get('models', []))} models")
        elif sub == "switch":
            if len(args) < 2:
                print(f"{tag()} usage: {orange('provider switch <name>')}")
                return
            r = switch_mod.switch_provider(args[1])
            if r.get("ok"):
                print(f"{tag()} {green(r['detail'])}")
            else:
                print(f"{tag()} {red('switch failed')}: {r.get('detail')} "
                      f"(known: {r.get('known', [])})")
        elif sub == "model":
            # provider model list | provider model switch <name>
            if len(args) < 2:
                print(f"{tag()} usage: {orange('provider model list|switch <name>')}")
                return
            msub = args[1].lower()
            if msub == "list":
                info = switch_mod.list_providers()
                for p in info["providers"]:
                    am = switch_mod.active_model_for(p["name"])
                    print(f"{tag()} {orange(p['name'])}: active {orange(am or '(default)')}")
                    for m in p.get("models", [])[:8]:
                        marker = "●" if m == am else " "
                        print(f"  {marker} {m}")
            elif msub == "switch" and len(args) >= 3:
                # model switch <name> — set the REASON model on its
                # currently selected provider.
                r = switch_mod.switch_reason_model(args[2])
                if r.get("ok"):
                    print(f"{tag()} {green(r['detail'])}")
                else:
                    print(f"{tag()} {red('switch failed')}: {r.get('detail')} "
                          f"(available: {r.get('available', [])})")
            else:
                print(f"{tag()} usage: {orange('provider model list|switch <name>')}")
        elif sub == "catalog":
            for name in sorted(list_catalog()):
                print(f"  {orange(name)}")
        elif sub == "add":
            if len(args) < 2:
                print(f"{tag()} usage: {orange('provider add <name> [api_key]')}")
                return
            name = args[1]
            key = args[2] if len(args) > 2 else ""
            result = setup.add_provider(name, key)
            if result.get("success"):
                print(f"{tag()} added provider {orange(name)} — "
                      f"models discovered: {result.get('models_discovered', 0)}")
            else:
                print(f"{tag()} {red('add failed')}: {result.get('error')}")
        else:
            print(f"{tag()} provider: {orange('list | catalog | add <name> [key] | switch <name> | model list|switch <name>')}")

    def cmd_emotion(self, args: list[str]) -> None:
        """emotion | emotion set <axis> <value> | emotion reset | emotion combo"""
        from core.emotion import (read_emotion, write_emotion, default_emotion,
                                  combine, active_combinations, AXES)

        sub = args[0].lower() if args else "show"
        if sub == "reset":
            ok_a = write_emotion("assistant", self.profile.name, default_emotion())
            ok_u = write_emotion("user", self.profile.name, default_emotion())
            print(f"{tag()} emotions reset to neutral: "
                  f"{green('ok') if ok_a and ok_u else red('failed')}")
            return
        if sub == "set":
            if len(args) < 3:
                print(f"{tag()} usage: {orange('emotion set <axis> <value>')} "
                      f"({', '.join(AXES)})")
                return
            axis = args[1].lower()
            if axis not in AXES:
                print(f"{tag()} {red('unknown axis')}: {axis} "
                      f"({', '.join(AXES)})")
                return
            try:
                val = float(args[2])
            except ValueError:
                print(f"{tag()} {red('value must be -1..+1')}")
                return
            val = max(-1.0, min(1.0, val))
            side = args[3].lower() if len(args) > 3 else "assistant"
            if side not in ("assistant", "user"):
                side = "assistant"
            emo = read_emotion(side, self.profile.name)
            vec = emo.get("vector", default_emotion())
            vec[axis] = val
            ok = write_emotion(side, self.profile.name, vec)
            print(f"{tag()} {side} {axis} → {orange(f'{val:+.2f}')} "
                  f"({green('ok') if ok else red('failed')})")
            return
        if sub == "combo":
            emo = read_emotion("assistant", self.profile.name)
            combos = active_combinations(emo.get("vector", {}))
            if not combos:
                print(f"{tag()} no active combination — neutral")
                return
            for c in combos:
                print(f"{tag()} {orange(c['pair'][0])} + {orange(c['pair'][1])} "
                      f"→ {green(c['canonical'])} ({c['synonym']})")
            return
        # show (default)
        for side in ("assistant", "user"):
            emo = read_emotion(side, self.profile.name)
            label = "Agent" if side == "assistant" else "Operator"
            print(f"{tag()} {label}: {orange(emo.get('current', 'neutral'))}")
            vec = emo.get("vector", {})
            line = "  ".join(f"{a}:{vec.get(a, 0.0):+.2f}" for a in AXES)
            print(f"{tag()}   {line}")
        emo = read_emotion("assistant", self.profile.name)
        combos = active_combinations(emo.get("vector", {}))
        if combos:
            c = combos[0]
            print(f"{tag()} active: {orange(c['pair'][0])} + {orange(c['pair'][1])} "
                  f"→ {green(c['canonical'])} ({c['synonym']})")

    def cmd_mdformat(self, args: list[str]) -> None:
        """mdformat [tree | profiles] — normalize the --- delimiters of
        every .md in .athena (the delimiter contract)."""
        from core.md_format import format_tree, format_profile_files
        sub = args[0].lower() if args else "tree"
        if sub in ("profiles", "system"):
            r = format_profile_files()
        else:
            r = format_tree()
        if r["changed"]:
            for f in r["files"]:
                print(f"{tag()} {green('formatted')} {f}")
        print(f"{tag()} checked {orange(r['checked'])} files — "
              f"{green(str(r['changed']))} changed, "
              f"{orange(str(r['checked'] - r['changed']))} already clean")

    def cmd_index(self, args: list[str]) -> None:
        """index rebuild | list | query <category> | import <file.db> | export <file.db>"""
        sub = args[0].lower() if args else "list"
        if sub == "import":
            # THE 08-17 VAULT IMPORT (the Operator's spec): copy-first,
            # universal, strict 1:1 variable match. Export source → JSONL →
            # import into THIS profile's vault under the current session.
            if len(args) < 2:
                print(f"{tag()} usage: {orange('vault import <file.db> [--session <uuid>]')}")
                return
            _src = args[1]
            _sess = None
            if "--session" in args:
                _sess = args[args.index("--session") + 1] if len(args) > args.index("--session") + 1 else None
            from knowledge.vault_transfer import export_to_jsonl, import_jsonl
            import tempfile, os as _os
            _tmp = _os.path.join(tempfile.gettempdir(), f"athena-import-{self.profile.name}.jsonl")
            print(f"{tag()} exporting {orange(_src)} → JSONL...")
            _er = export_to_jsonl(_src, _tmp)
            if not _er.get("ok"):
                print(f"{tag()} export failed: {orange(str(_er.get('error')))}")
                return
            print(f"{tag()} exported {_er['exported']} rows (of {_er['rows_read']}) "
                  f"from table '{orange(_er.get('table_used'))}'")
            _sess = _sess or str(self.loop and getattr(self.loop, "session_id", "") or "")
            print(f"{tag()} importing into {orange(self.profile.name)} vault "
                  f"(session {orange(_sess or 'new UUID')})...")
            _ir = import_jsonl(_tmp, profile=self.profile.name, session_id=_sess)
            print(f"{tag()} imported {orange(_ir.get('imported', 0))} rows "
                  f"({_ir.get('skipped', 0)} skipped) — session {orange(_ir.get('session_id',''))}")
            print(f"{tag()} copy-first: original vault untouched, added only.")
            return
        if sub == "export":
            if len(args) < 2:
                print(f"{tag()} usage: {orange('vault export <source.db> [--out <file.jsonl>] [--table <name>]')}")
                return
            _src = args[1]
            _out = None
            _tbl = None
            if "--out" in args:
                _out = args[args.index("--out") + 1]
            if "--table" in args:
                _tbl = args[args.index("--table") + 1]
            from knowledge.vault_transfer import export_to_jsonl
            _out = _out or _src.rsplit(".", 1)[0] + ".jsonl"
            _er = export_to_jsonl(_src, _out, table=_tbl or "")
            print(f"{tag()} exported {_er.get('exported', 0)} rows (of {_er.get('rows_read', 0)}) "
                  f"→ {orange(_er.get('output',''))}")
            if not _er.get("ok", True):
                print(f"{tag()} export failed: {orange(str(_er.get('error')))}")
            return
        if sub == "rebuild":
            result = db_layer.build_index(profile=self.profile.name)
            print(f"{tag()} index rebuilt: {orange(result['sections'])} sections "
                  f"over {result['entries']} entries")
        elif sub == "list":
            for sec in db_layer.list_index(profile=self.profile.name):
                print(f"{tag()} {orange(sec['category']):32s} rows {sec['range_from']:>6d}.."
                      f"{sec['range_to']:<6d} count={sec['count']}")
        elif sub == "query":
            if len(args) < 2:
                print(f"{tag()} usage: {orange('index query <category>')}")
                return
            category = " ".join(args[1:])
            hits = db_layer.query_index(category, profile=self.profile.name)
            if not hits:
                print(f"{tag()} no section: {orange(category)}")
                return
            sec = hits[0]
            print(f"{tag()} {orange(sec['category'])}: rows {sec['range_from']}.."
                  f"{sec['range_to']} count={sec['count']}")
            for sample in sec.get("sample", []):
                rowid = sample.get("rowid", "?")
                kind = sample.get("type", sample.get("kind", ""))
                role = sample.get("role", "")
                content = sample.get("content", "")
                print(f"  row {rowid:>6} [{dim(kind)}/{dim(role)}] {content}")
        else:
            print(f"{tag()} index: {orange('rebuild | list | query <category>')}")

    def cmd_kanban(self, args: list[str]) -> None:
        """kanban board | list [assignee] | add <title> [assignee] | update <id> <status> | decompose <id> | judge <id>"""
        from autonomy import kanban
        from providers.provider import ProviderChain

        if not args:
            summary = kanban.board_summary()
            by_status = summary.get("by_status", {})
            by_agent = summary.get("by_agent", {})
            statuses = ", ".join(f"{k}={v}" for k, v in by_status.items()) or "empty"
            agents = ", ".join(f"{k}:{v}" for k, v in by_agent.items()) or "unassigned"
            print(f"{tag()} board — statuses: {orange(statuses)} | agents: {orange(agents)}")
            return
        sub = args[0].lower()
        if sub in ("board", "list", "ls"):
            assignee = args[1] if len(args) > 1 else ""
            rows = kanban.list_tasks(assignee=assignee, limit=20)
            for t in rows:
                print(f"{tag()} {orange(t['id'][:8])} [{t['status']}] {t['title']} → {t['assignee'] or '-'}")
        elif sub in ("add", "create"):
            if len(args) < 2:
                print(f"{tag()} usage: {orange('kanban add <title> [assignee]')}")
                return
            title = args[1]
            assignee = args[2] if len(args) > 2 else ""
            task = kanban.add_task(title, assignee=assignee, created_by=self.profile.name)
            print(f"{tag()} added {orange(task['id'][:8])} [{task['status']}] {task['title']} → {assignee or 'unassigned'}")
            if assignee == "nurse":
                print(f"{tag()} {orange('nurse consulted')} — she will diagnose and repair on her next pass")
        elif sub in ("delegate", "command", "queen"):
            """delegate <title> <assignee> — Athena assigns work (top priority)."""
            if len(args) < 3:
                print(f"{tag()} usage: {orange('kanban delegate <title> <assignee>')}")
                return
            title = args[1]
            assignee = args[2]
            task = kanban.delegate(title, assignee, created_by=self.profile.name)
            tier = "Athena/administrator (top priority)" if self.profile.is_default else f"{self.profile.name} (command)"
            print(f"{tag()} delegated {orange(task['id'][:8])} → {orange(assignee)} "
                  f"[priority {task['priority']}] ({tier})")
        elif sub in ("ask", "help", "request"):
            """ask <title> <assignee> — request help from a peer agent (lower priority)."""
            if len(args) < 3:
                print(f"{tag()} usage: {orange('kanban ask <title> <assignee>')}")
                return
            title = args[1]
            assignee = args[2]
            task = kanban.delegate(title, assignee, created_by=self.profile.name, priority=5)
            print(f"{tag()} asked {orange(assignee)} for help: {orange(task['id'][:8])} "
                  f"[priority {task['priority']}] (peer request)")
        elif sub in ("spawn", "subagent", "worker"):
            """spawn <title> <body> — spawn an unnamed subagent worker."""
            if len(args) < 3:
                print(f"{tag()} usage: {orange('kanban spawn <title> <body>')}")
                return
            title = args[1]
            body = " ".join(args[2:])
            sub = kanban.spawn_subagent(self.profile.name, title, body)
            print(f"{tag()} spawned subagent {orange(sub['id'][:8])} "
                  f"(parent: {self.profile.name}) — queued, will run on the next tick")
        elif sub in ("subagents", "workers", "pool"):
            """subagents [parent] — list the subagent pool."""
            parent = args[1] if len(args) > 1 else ""
            subs = kanban.list_subagents(parent=parent)
            if not subs:
                print(f"{tag()} no subagents")
                return
            print(f"{tag()} subagents ({len(subs)}):")
            for s in subs[:15]:
                print(f"  {orange(s['id'][:8])} [{s['status']}] parent={s['parent']} — {s['title'][:40]}")
        elif sub in ("update", "set"):
            if len(args) < 3:
                print(f"{tag()} usage: {orange('kanban update <id> <status>')}")
                return
            task = kanban.update_task(args[1], status=args[2])
            print(f"{tag()} {orange(task['id'][:8])} → [{task['status']}]")
        elif sub in ("decompose", "split"):
            if len(args) < 2:
                print(f"{tag()} usage: {orange('kanban decompose <id>')}")
                return
            result = kanban.decompose(args[1], providers=ProviderChain())
            if result.get("success"):
                print(f"{tag()} decomposed into {orange(len(result['subtasks']))} subtasks")
                for st in result["subtasks"]:
                    print(f"  {st['id'][:8]} [{st['status']}] {st['title']}")
            else:
                print(f"{tag()} {red('decompose failed')}: {result.get('error')}")
        elif sub in ("judge", "review"):
            if len(args) < 2:
                print(f"{tag()} usage: {orange('kanban judge <id>')}")
                return
            result = kanban.judge(args[1], providers=ProviderChain())
            if result.get("success"):
                print(f"{tag()} complete: {orange(result['complete'])} — {result.get('reason', '')}")
            else:
                print(f"{tag()} {red('judge failed')}: {result.get('error')}")
        else:
            print(f"{tag()} kanban: {orange('board | list [assignee] | add <title> [assignee] | update <id> <status> | decompose <id> | judge <id>')}")

    def cmd_cron(self, args: list[str]) -> None:
        """cron list | add <name> <schedule> <prompt> | remove <name>"""
        from autonomy.scheduler import add_job, list_jobs, remove_job

        if not args or args[0] in ("list", "ls"):
            jobs = list_jobs()
            if not jobs:
                print(f"{tag()} no scheduled jobs — {orange('cron add <name> <schedule> <prompt>')}")
                return
            for j in jobs:
                print(f"{tag()} {orange(j['name'])} {j['schedule']} | next: {j.get('next_run_at')}")
        elif args[0] in ("add", "create"):
            if len(args) < 4:
                print(f"{tag()} usage: {orange('cron add <name> <schedule> <prompt>')}")
                print(f"{tag()} schedules: full cron {orange('0 3 * * *')} | condensed {orange('03***')} | "
                      f"interval {orange('every 30m')} | short {orange('30m')} | one-shot {orange('2026-08-07T09:00:00')}")
                return
            # The schedule may be MULTI-TOKEN ("every 1h", "0 3 * * *").
            # Find the first token that looks like a schedule; everything
            # after it is the prompt.
            schedule_tokens: list[str] = []
            prompt_tokens: list[str] = []
            for tok in args[2:]:
                if not schedule_tokens and tok in ("every", "*/", "*"):
                    schedule_tokens.append(tok)
                    continue
                if not schedule_tokens:
                    schedule_tokens.append(tok)
                    continue
                if schedule_tokens and len(schedule_tokens) < 5 and _looks_like_schedule_part(tok):
                    schedule_tokens.append(tok)
                    continue
                prompt_tokens.append(tok)
            schedule = " ".join(schedule_tokens)
            prompt = " ".join(prompt_tokens)
            job = add_job(args[1], schedule, prompt)
            print(f"{tag()} scheduled {orange(job['name'])} ({job['schedule']})")
        elif args[0] in ("remove", "rm", "delete"):
            if len(args) < 2:
                print(f"{tag()} usage: {orange('cron remove <name>')}")
                return
            remove_job(args[1])
            print(f"{tag()} removed {orange(args[1])}")
        else:
            print(f"{tag()} cron: {orange('list | add <name> <schedule> <prompt> | remove <name>')}")

    def cmd_profile(self, args: list[str]) -> None:
        """profile list | show <name> | switch <name> | create <name>"""
        from intelligence.profiles import list_profiles, get_profile, create_profile, current_profile

        if not args or args[0] in ("list", "ls"):
            cur = current_profile()
            for name in list_profiles():
                marker = "►" if name.name == cur.name else " "
                print(f"{tag()} {orange(marker)} {name.name}")
        elif args[0] in ("show", "info"):
            name = args[1] if len(args) > 1 else self.profile.name
            p = get_profile(name)
            if p:
                print(f"{tag()} profile: {orange(p.name)} | root: {p.root}")
            else:
                print(f"{tag()} {red('profile not found')}: {name}")
        elif args[0] in ("switch", "use"):
            if len(args) < 2:
                print(f"{tag()} usage: {orange('profile switch <name>')}")
                return
            from athena import _run_profile_cmd
            rc = _run_profile_cmd(["switch", args[1]])
            if rc == 0:
                print(f"{tag()} active profile → {orange(args[1])} (next turn uses it)")
        elif args[0] in ("current",):
            cur = current_profile()
            print(f"{tag()} active profile: {orange(cur.name)} | root: {cur.root}")
        elif args[0] in ("create", "new"):
            if len(args) < 2:
                print(f"{tag()} usage: {orange('profile create <name>')}")
                return
            result = create_profile(args[1])
            print(f"{tag()} created profile {orange(args[1])}: {result}")
        else:
            print(f"{tag()} profile: {orange('list | show <name> | switch <name> | current | create <name>')}")

    def cmd_security(self, args: list[str]) -> None:
        """security | integrity — check the baseline manifest."""
        from security.integrity import scan, build_manifest, MANIFEST_PATH

        if not MANIFEST_PATH.exists():
            print(f"{tag()} no baseline — building now")
            build_manifest()
        report = scan()
        if report.get("ok"):
            print(f"{tag()} integrity: {orange('OK')} ({report.get('tracked')} files tracked)")
        else:
            print(f"{tag()} {red('integrity ALERT')}: changed {report.get('changed')}")

    def cmd_backup(self, args: list[str]) -> None:
        """backup [-q] [-l LABEL] | import <zip> — backups."""
        from data.backup import cmd_backup as _cmd_backup
        from data.backup import cmd_import as _cmd_import

        if args and args[0] in ("import", "restore"):
            class _Args:
                archive = args[1] if len(args) > 1 else ""
                args = args[1:]
            _cmd_import(_Args())
            return
        class _Args:
            quick = any(a in ("-q", "--quick") for a in args)
            output = ""
            label = ""
        for i, a in enumerate(args):
            if a in ("-o", "--output") and i + 1 < len(args):
                _Args.output = args[i + 1]
            if a in ("-l", "--label") and i + 1 < len(args):
                _Args.label = args[i + 1]
        _cmd_backup(_Args())

    def cmd_snapshot(self, args: list[str] | None = None) -> None:
        """snapshot — the CURRENT version, immutable, rollback target."""
        from data.backup import cmd_snapshot as _cs
        class _Args:
            pass
        _cs(_Args())

    def cmd_rollback(self, args: list[str] | None = None) -> None:
        """rollback [1-3] — roll back athena-system to a snapshot."""
        from data.backup import cmd_rollback as _cr
        class _Args:
            version = args[0] if args else "1"
        _cr(_Args())

    def cmd_patch(self, args: list[str] | None = None) -> None:
        """patch [apply|apply-safe] — fetch the GitHub repo patch, then
        apply it (A: overwrite) or let the nurse/janitor apply it
        manually (B: safe apply, architecture preserved)."""
        from data.backup import cmd_patch as _cp
        class _Args:
            action = args[0] if args else ""
            overwrite = True
        _cp(_Args())

    def cmd_skills(self) -> None:
        from intelligence.skills import load_skills, skills_index

        skills = load_skills(profile_dir=None if self.profile.is_default else self.profile.root)
        print(skills_index(skills) or f"{tag()} no skills loaded")

    def cmd_plugins(self) -> None:
        from intelligence.plugins import load_all

        summary = load_all()
        if not summary["plugins"]:
            print(f"{tag()} no plugins discovered")
            return
        for p in summary["plugins"]:
            print(f"{tag()} {orange(p['plugin'])} v{p['version']} "
                  f"| tools: {len(p['tools_registered'])} | skills: {len(p['skills'])}")

    def cmd_tools(self) -> None:
        from filesystem.tools import TOOLS

        if not TOOLS:
            print(f"{tag()} no tools registered")
            return
        for name in sorted(TOOLS):
            print(f"{tag()} {orange(name)}")

    def cmd_config(self) -> None:
        from core.config import load_config, CONFIG_PATH

        print(f"{tag()} config file: {CONFIG_PATH}")
        cfg = load_config()
        for section, value in cfg.items():
            if section == "provider":
                sel = value.get("selection", {})
                print(f"{tag()} provider selection: {sel}")
            else:
                print(f"{tag()} {section}: {value}")

    def cmd_streaming(self, args: list[str] | None = None) -> None:
        """THE STREAMING COMMAND (the 08-16 spec): show + set the reply
        streaming knob.

        /streaming                — show the current state
        /streaming set true       — replies TYPE OUT live (deltas)
        /streaming set false      — replies appear whole when done
        /streaming true|false     — the short form

        The choice persists to config.yaml (provider.streaming) and takes
        effect on the NEXT turn (the loop reads it per turn).
        """
        from core.config import load_config, save_config
        sub = (args[0] if args else "current").lower()
        if sub == "set":
            sub = (args[1] if len(args) > 1 else "current").lower()
        # The active loop (the CLI's ConversationLoop).
        loop = getattr(self, "loop", None)
        if sub in ("true", "1", "on", "yes"):
            val = True
        elif sub in ("false", "0", "off", "no"):
            val = False
        elif sub in ("current", ""):
            cur = bool(loop and getattr(loop, "_streaming_override", None))
            if loop is None or getattr(loop, "_streaming_override", None) is None:
                cur = bool(load_config().get("provider", {}).get("streaming", True))
            print(f"{tag()} streaming: {'true' if cur else 'false'} "
                  f"({'live typing' if cur else 'whole reply when done'})")
            return
        else:
            print(f"{tag()} usage: /streaming set {orange('true|false')} "
                  f"or /streaming")
            return
        # Set the runtime loop + persist the config.
        try:
            if loop is not None and hasattr(loop, "set_streaming"):
                loop.set_streaming(val)
        except Exception:
            pass
        try:
            cfg = load_config()
            cfg.setdefault("provider", {})["streaming"] = val
            save_config(cfg)
            print(f"{tag()} streaming: {'true' if val else 'false'} — "
                  f"applies to the next turn + saved to config.yaml")
        except Exception as exc:
            print(f"{tag()} streaming set failed: {exc}")

    def cmd_version(self) -> None:
        import sys as _sys
        from core.config import VERSION
        print(f"Athena version: {VERSION}")
        print(f"Install directory: {Path(__file__).parent.parent}")
        print(f"Python: {_sys.version.split()[0]}")

    def cmd_doctor(self) -> None:
        from core.db import health as db_health
        from providers.provider import ProviderChain

        ok = True
        db = db_health(profile=self.profile.name)
        print(f"{tag()} db: {'OK' if all(db.values()) else 'PROBLEM'} {db}")
        ok = ok and all(db.values())
        try:
            rp = ProviderChain().ready_provider()
            if rp:
                print(f"{tag()} provider: OK ({rp.name})")
            else:
                print(f"{tag()} provider: NO READY PROVIDER")
                ok = False
        except Exception as exc:  # noqa: BLE001
            print(f"{tag()} provider: PROBLEM ({exc})")
            ok = False
        from security.integrity import scan, MANIFEST_PATH
        if not MANIFEST_PATH.exists():
            print(f"{tag()} integrity: no baseline (run /security)")
            ok = False
        else:
            report = scan()
            if report.get("ok"):
                print(f"{tag()} integrity: OK")
            else:
                print(f"{tag()} integrity: ALERT {report.get('changed')}")
                ok = False
        print(f"{tag()} doctor: {orange('ALL CLEAR') if ok else red('ISSUES FOUND')}")

    def cmd_wipe_test(self, args: list[str] | None = None) -> None:
        """wipe-test — the OPERATOR-ONLY developer survival test.

        Simulates wiping .athena down to the 4 keep files
        (athena-system, config.yaml, authentication.json, .secret) and
        verifies Athena springs back: profiles register, layouts + the
        6-file set rebuild, the built-ins seed, and every system .md
        matches the Standard Markdown Schema.

        OPERATOR ONLY — agents (nurse, scheduler, any runtime) must
        NEVER use it. It requires an interactive approval that states
        what it does. The approval token is process-scoped (env var),
        never persisted, and only this CLI path sets it.
        """
        # ── THE APPROVAL MESSAGE (the Operator's 08-12 spec) ───────────────
        print(red("WIPE-TEST — OPERATOR ONLY DEVELOPER TEST"))
        print("This test SIMULATES WIPING Athena: it deletes everything")
        print("except the 4 keep files/dirs:")
        print("    athena-system/  config.yaml  authentication.json  .secret")
        print("It then verifies Athena repopulates all profiles, layouts,")
        print("built-in skills/tools, and schema-conforming .md files.")
        print()
        print(orange("It is a DEVELOPMENT TEST — the agents (nurse, scheduler,"))
        print(orange("any runtime) must NEVER use it. OPERATOR ONLY."))
        print(red("It WILL wipe everything the Operator has made thus far."))
        print(red("Ensure your work is BACKED UP before proceeding."))
        print()
        try:
            resp = input("Type 'WIPE' to approve and run, anything else to cancel: ")
        except EOFError:
            print(f"{tag()} cancelled (no input)")
            return
        if resp.strip().upper() != "WIPE":
            print(f"{tag()} cancelled — no changes made")
            return
        # The process-scoped approval token (never persisted).
        import os as _os
        _os.environ["ATHENA_WIPE_APPROVED"] = "1"
        try:
            from doctor.run import run_isolated, report as render
            r = run_isolated(category="systems", timeout=300)
            print(render(r, colored=True))
            from core.logging import log_event
            log_event(2, "wipe-test: operator ran the survival test",
                      source="doctor", action="wipe_test")
        finally:
            _os.environ.pop("ATHENA_WIPE_APPROVED", None)

    def cmd_release_propose(self, args: list[str] | None = None) -> None:
        """release-propose <title> [--tier stable|beta|alpha] — create a
        release-proposal document for a local optimization.

        THE DOCTRINE (the Operator's 08-12 spec): the wiki is the stable
        doctrine. A local change that diverges from it is PROPOSED as a
        document with a release tier — NEVER silently applied. Only the
        Operator can green-light a release; this command only DRAFTS the
        proposal for his decision.
        """
        import datetime
        from core.config import ATHENA_ROOT, WIKI_URL
        args = args or []
        title_parts = []
        tier = "Beta"
        i = 0
        while i < len(args):
            if args[i] in ("--tier", "-t") and i + 1 < len(args):
                t = args[i + 1].lower()
                if t in ("stable", "beta", "alpha"):
                    tier = t.capitalize()
                i += 2
                continue
            title_parts.append(args[i])
            i += 1
        title = " ".join(title_parts).strip()
        if not title:
            print(f"{tag()} usage: athena release-propose <title> "
                  f"[--tier stable|beta|alpha]")
            return
        if tier == "Stable":
            print(red("Only the Operator can propose a STABLE release tier. "))
            print("Drafting as Beta instead — he can re-tier it at approval.")
            tier = "Beta"
        tmpl = Path(ATHENA_ROOT / "athena-system" / "templates" /
                    "release-proposal.md")
        try:
            text = tmpl.read_text(encoding="utf-8")
        except OSError:
            print(f"{tag()} template missing: {tmpl}")
            return
        now = datetime.datetime.now().isoformat(timespec="seconds")
        slug = title.strip().lower().replace(" ", "-")[:40]
        out_dir = ATHENA_ROOT / "operations" / "release-proposals"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{now[:10]}-{slug}.md"
        agent = self.profile.name if not self.profile.is_default else "Athena"
        text = (text.replace("{TITLE}", title)
                    .replace("{DATE}", now)
                    .replace("{AGENT}", agent)
                    .replace("{TIER}", tier))
        out.write_text(text, encoding="utf-8")
        print(f"{tag()} {green('proposal drafted')}: {out}")
        print(f"{tag()} wiki doctrine: {WIKI_URL}")
        print(f"{tag()} {orange('NOT APPLIED — only the Operator decides.')}")

    def cmd_wiki(self, args: list[str] | None = None) -> None:
        """wiki [sync|status] — the local wiki mirror (.athena/.wiki/).

        The wiki is the STABLE DOCTRINE — the known-good reference for
        how Athena operates. The local clone lets the agents read it
        OFFLINE (no browser per consultation). This command:

          sync    — pull the latest from the remote (the known-good)
          status  — page count + the tree (default)

        The wiki is capped at ~20 pages (the Operator's 08-12 rule): when
        the count approaches the cap, new topics GENERALIZE into
        existing sections instead of adding pages.
        """
        from core.config import WIKI_DIR, WIKI_REPO, WIKI_URL
        sub = (args[0].lower() if args else "status")
        if sub == "sync":
            # THE 1:1 RULE with the ATOMIC SWAP (the Operator's 08-12 spec):
            # fresh clone to .wiki.new → swap in → delete old, so the
            # local mirror ALWAYS exists (never a missing folder for a
            # reading agent) and is always the exact remote state.
            from core.wiki import sync_wiki
            res = sync_wiki()
            if not res.get("ok"):
                print(f"{tag()} {red('wiki sync failed')}: {res.get('detail')}")
                return
            print(f"{tag()} {green('wiki synced 1:1')} → {WIKI_DIR} "
                  f"({res.get('pages')} pages)")
            from core.logging import log_event
            log_event(2, f"wiki sync: fresh clone ({res.get('pages')} pages)",
                      source="core", action="wiki_sync")
        # status (default): page count + the tree.
        if not (WIKI_DIR / "Home.md").exists():
            print(f"{tag()} wiki not cloned yet — run: athena wiki sync")
            return
        pages = sorted(WIKI_DIR.glob("*.md"))
        print(f"{tag()} wiki: {green(str(len(pages)))} pages "
              f"(cap 20) — {WIKI_URL}")
        print(f"{tag()} local mirror: {WIKI_DIR}")
        for p in pages:
            print(f"  - {p.stem}")
        if len(pages) >= 20:
            print(f"{tag()} {orange('AT CAP — new topics must generalize')} "
                  f"into existing sections.")

    def cmd_logs(self, args: list[str] | None = None) -> None:
        """logs [profile] — read the metric logs (metrics/logs/<profile>/)."""
        from metrics.logger import LOGS_DIR

        args = args or []
        profile = args[0] if args and not args[0].startswith("-") else \
            self.profile.name if not self.profile.is_default else "default"
        pd = LOGS_DIR / profile
        if not pd.exists():
            print(f"{tag()} no metric logs for profile: {profile}")
            return
        logs = sorted(pd.glob("*_metric.log"))
        if not logs:
            print(f"{tag()} no log files for profile: {profile}")
            return
        latest = logs[-1]
        lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
        print(f"{tag()} {latest.name} (last {len(lines)} lines):")
        for line in lines:
            print(f"  {line}")

    def cmd_logs_color(self, args: list[str] | None = None) -> None:
        """logs --color — JSONL entries with severity colors (1 green … 5 red)."""
        from metrics.logger import LOGS_DIR, parse_entries, colorize_level

        args = args or []
        profile = args[0] if args and not args[0].startswith("-") else \
            self.profile.name if not self.profile.is_default else "default"
        pd = LOGS_DIR / profile
        if not pd.exists():
            print(f"{tag()} no metric logs for profile: {profile}")
            return
        logs = sorted(pd.glob("*_metric.log"))
        if not logs:
            print(f"{tag()} no log files for profile: {profile}")
            return
        latest = logs[-1]
        entries = parse_entries(latest.read_text(encoding="utf-8", errors="replace"))
        print(f"{tag()} {latest.name} ({len(entries)} entries):")
        for e in entries[-20:]:
            label = colorize_level(e["level"], f"L{e['level']}")
            status = colorize_level(e["level"], e["status"])
            print(f"  {label} {status:8s} [{e['tool']}] {e['message']}")

    def cmd_lifecycle(self, args: list[str]) -> None:
        """lifecycle start|shutdown|restart|refresh — the four server methods."""
        from autonomy.lifecycle import run

        if not args:
            print(f"{tag()} usage: {orange('lifecycle start|shutdown|restart|refresh')}")
            print(f"{tag()}   start    — HARD start everything")
            print(f"{tag()}   shutdown — HARD kill everything (online or offline)")
            print(f"{tag()}   restart  — SOFT restart (graceful stop, then start)")
            print(f"{tag()}   refresh  — SOFT reload commands/plugins/skills (no kill)")
            return
        result = run(args[0])
        print(f"{tag()} {result}")

    def cmd_events(self, args: list[str] | None = None) -> None:
        """events [profile] — the agent activity log (levels 1-2 only)."""
        from metrics.events import read_events, usage_summary
        from metrics.logger import colorize_level

        args = args or []
        profile = args[0] if args and not args[0].startswith("-") else \
            self.profile.name if not self.profile.is_default else "default"
        if args and args[0] in ("usage", "summary"):
            s = usage_summary(profile)
            print(f"{tag()} {orange(profile)} usage — {s['total']} events")
            for tool, count in sorted(s["counts"].items(), key=lambda x: -x[1])[:15]:
                print(f"  {orange(tool):20s} {count}")
            return
        entries = read_events(profile, limit=20)
        if not entries:
            print(f"{tag()} no events for profile: {profile}")
            return
        print(f"{tag()} events for {orange(profile)} (last {len(entries)}):")
        for e in entries:
            lvl = colorize_level(e.get("level", 1), f"L{e.get('level', 1)}")
            print(f"  {lvl} {e.get('status', 'INFO'):5s} [{e.get('tool', '?')}] "
                  f"{e.get('action', '')} {e.get('target', '')} — {e.get('result', '')[:60]}")

    def cmd_nurse(self, args: list[str]) -> None:
        """nurse consult [task_id] | nurse status — the repair agent.

        The nurse is an agent on the board: consult her by assigning a
        kanban task to 'nurse', or call consult directly. She diagnoses
        and repairs the SYSTEM — ordinary agents request, she fixes.
        """
        from doctor.nurse import consult, NURSE_AGENT, nurse_scope

        if args and args[0] in ("status", "state"):
            from autonomy.kanban import open_work_for
            work = open_work_for(NURSE_AGENT)
            print(f"{tag()} nurse agent: {orange(NURSE_AGENT)} | in scope: {nurse_scope()}")
            print(f"{tag()} consultations queued: {len(work)}")
            for t in work:
                print(f"  {orange(t['id'][:8])} [{t['status']}] {t['title']}")
            return
        # nurse consult [task_id] — or a direct diagnosis if no task.
        task_id = args[1] if len(args) > 1 else ""
        if not task_id:
            print(f"{tag()} usage: {orange('nurse consult <task_id>')} | {orange('nurse status')}")
            print(f"{tag()} or consult via {orange('kanban add <title> nurse')}")
            return
        print(f"{tag()} nurse consulting...")
        result = consult(task_id)
        print(f"{tag()} consultation: {orange(result['task'])} — {result['failures']} failures found")
        if result.get("repair"):
            r = result["repair"]
            print(f"{tag()} repair: attempted={orange(r.get('attempted'))} "
                  f"fixed={orange(r.get('fixed'))} still={orange(r.get('still_failing'))}")
        if result.get("still_failing"):
            print(f"{tag()} {red('still failing:')} {result['still_failing']}")

    def cmd_help(self) -> None:
        """THE RICH HELP (the Operator's 08-16 spec): a table mirroring
        the website's nav — the user sees every module + what it does,
        exactly like the GUI's page list. Falls back to plain text."""
        try:
            from rich.console import Console
            from rich.table import Table
            # THE 08-16 WIDTH-AWARE CONSOLE (Option C): under the
            # persistent-window capture, build the console at the
            # WINDOW's width so the table renders correctly (a bare
            # StringIO width collapsed the columns).
            try:
                _w = (self._repl_app_ref.output.get_size().columns
                      if getattr(self, "_repl_app_ref", None) else None)
            except Exception:
                _w = None
            console = Console(width=_w) if _w else Console()
            table = Table(title="Athena Commands — the terminal's nav",
                          border_style="orange1", header_style="bold yellow")
            table.add_column("Command", style="bold")
            table.add_column("What it does", style="dim")
            table.add_column("Aliases", style="green")
            rows = [
                ("send <text>", "send a message (or just type)", "say, msg"),
                ("status", "the health/status panel", "health"),
                ("session", "switch/list sessions", "sessions"),
                ("vault", "the archive (search/query)", "index"),
                ("kanban", "the task board", "board"),
                ("cron", "scheduled jobs", "schedule, jobs"),
                ("profile", "switch/list profiles", "profiles, agent"),
                ("provider", "providers + models", "providers, model, auth"),
                ("emotion", "the emotion/mood display", "feelings, mood"),
                ("config", "view the configuration", ""),
                ("doctor", "run the diagnostics", ""),
                ("nurse", "repair what the doctor found", ""),
                ("backup", "snapshot the data", ""),
                ("rollback", "restore a snapshot", "rb"),
                ("skills", "list the skills", ""),
                ("plugins", "list the plugins", ""),
                ("tools", "list the tools", ""),
                ("logs", "the metric log", ""),
                ("events", "the event stream", ""),
                ("theme", "toggle dark/light (the GUI's ☀/🌙)", ""),
                ("help", "this table", ""),
                ("quit", "exit the terminal", "exit"),
            ]
            for cmd, desc, alias in rows:
                table.add_row(f"/{cmd}", desc, alias)
            console.print(table)
            console.print("[dim]start a command with / or \\ — e.g. "
                          "/kanban list or \\status[/dim]")
            console.print("[dim]or just type to chat with Athena[/dim]")
        except Exception:
            print(f"{tag()} commands: send <text> | session | kanban | cron | profile | status | "
                  f"security | backup | skills | plugins | tools | config | version | doctor | "
                  f"logs | events | theme | help | quit")
            print(f"{tag()} start a command with / or \\\\ — e.g. /kanban list or \\\\status")

    def cmd_theme(self, args: list[str] | None = None) -> None:
        """THE THEME COMMAND (the GUI's ☀/🌙, terminal-side).

        /theme                — toggle light/dark
        /theme light          — set light
        /theme dark           — set dark
        /theme set light      — set light (the explicit form)
        /theme set dark       — set dark
        /theme current        — show the active mode + palette

        The window re-renders with the palette's 5 colors (the SAME
        palettes the website uses — config theme.light/dark).
        """
        from cli.colors import set_dark_mode, is_dark_mode, theme_colors
        sub = (args[0] if args else "toggle").lower()
        if sub in ("set",):
            sub = (args[1] if len(args) > 1 else "toggle").lower()
        if sub == "light":
            set_dark_mode(False)
            print(f"{tag()} theme: light")
        elif sub == "dark":
            set_dark_mode(True)
            print(f"{tag()} theme: dark")
        elif sub == "current":
            mode = "dark" if is_dark_mode() else "light"
            c = theme_colors()
            print(f"{tag()} theme: {mode} — "
                  f"primary {c['primary']} · secondary {c['secondary']} "
                  f"· text {c['text']}")
        else:
            set_dark_mode(not is_dark_mode())
            print(f"{tag()} theme: {'dark' if is_dark_mode() else 'light'}")
        # Re-apply the window style (the persistent-window app).
        try:
            if getattr(self, "_repl_app_ref", None) is not None:
                self._repl_app_ref.style = self._current_style()
                self._repl_app_ref.invalidate()
        except Exception:
            pass

    def repl(self) -> int:
        from metrics.logger import log as metric_log

        state = "resumed" if self.resumed else "new"
        profile_name = self.profile.name
        # THE NAMES (the Operator's 08-16 spec): from the identity files —
        # ASSISTANT.md → the agent (default Athena), USER.md → the operator
        # (default User). Defined ONCE here — used by the persistent window
        # AND the fallback.
        try:
            from core.config import flow_names
            agent_name, operator_name = flow_names()
        except Exception:
            agent_name, operator_name = "Athena", "User"
        try:
            metric_log(1, "cli session started", profile=profile_name, source="cli")
        except Exception:
            pass
        # The banner (rich ASCII art + the flow diagram).
        # THE 08-16 NO-FLICKER FIX: the welcome is SEEDED INTO the body
        # (banner art + the status panel) instead of printed + then
        # replaced by the full-screen window. ONE continuous window —
        # the welcome IS the body's first lines, the chat appends below.
        welcome_lines: list = []
        try:
            # THE 08-16 BLACK-SCREEN FIX: the seed is built FIRST on the
            # visible shell screen — the clear happens LATER, immediately
            # before the window opens. A slow status build (fresh install,
            # cold imports) never leaves a long black gap.
            from cli.banner import (banner_text, hotbar_plain, runtime_footer)
            try:
                # THE 08-16 GRADIENT FIX: each logo row gets its OWN
                # gradient style (red → red-orange → orange → orange-yellow
                # → yellow) — the window renders the 5-tone banner, not a
                # flat single-color strip.
                from cli.banner import banner_rows
                for style, row in banner_rows():
                    welcome_lines.append((style, row))
            except Exception:
                try:
                    from cli.banner import banner_art
                    welcome_lines.append(("class:body-banner", banner_art()))
                except Exception:
                    welcome_lines.append(("class:body-banner", banner_text()))
            # The status section (the welcome statistics) — PLAIN text
            # (the window themes it; no raw Rich markup).
            try:
                from cli.banner import build_status_plain
                for line in build_status_plain():
                    welcome_lines.append(("class:body-status", "  " + line))
            except Exception:
                try:
                    from cli.banner import build_status_section
                    for line in build_status_section():
                        welcome_lines.append(("class:body-status", "  " + line))
                except Exception:
                    pass
            welcome_lines.append(("class:body-dim", ""))
        except (Exception, KeyboardInterrupt):
            # THE 08-16 BLACK-SCREEN FIX: catch KeyboardInterrupt here too —
            # a Ctrl+C during the seed (the ~3s status build on a fresh
            # install) must not CRASH the CLI. It just skips the welcome
            # content; the persistent window still opens below.
            pass
        # THE 08-16 SECOND-SCREEN FIX: the "CLI (new session)" marker is
        # REMOVED — it printed outside the persistent window and created
        # the second screen. The window IS the whole terminal now.
        # THE "commands start" hint goes INTO the body seed (below the
        # status), never printed outside the window.
        try:
            welcome_lines.append(("class:body-dim",
                                  f"  commands start with / or \\ — "
                                  f"e.g. /kanban list"))
        except Exception:
            pass
        # THE 08-16 PERSISTENT WINDOW (the Operator's spec): the terminal
        # becomes a CONSISTENT window — header (the pages) > body (the
        # alternating history) > footer (the hotbar + input). Same engine
        # as the setup wizard (prompt_toolkit full-screen diff rendering —
        # ONE live copy). Keys:
        #   [  /  ]     — swap the page (header tabs)
        #   ↑ / ↓       — move within the operator input
        #   enter       — send / confirm
        #   backspace   — cancel (clear the input)
        #   CTRL+E      — exit the program
        #   CTRL+C      — stop Athena from reasoning THIS turn (not exit)
        try:
            from prompt_toolkit.application import Application
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.layout import (Layout, HSplit, VSplit, Window,
                                               FormattedTextControl, Dimension)
            from prompt_toolkit.formatted_text import FormattedText
            from prompt_toolkit.styles import Style
            from prompt_toolkit.completion import Completer, Completion
            from prompt_toolkit.filters import Condition
            from prompt_toolkit.layout.controls import BufferControl
            from prompt_toolkit.layout.processors import BeforeInput
            from prompt_toolkit.buffer import Buffer
            from prompt_toolkit.history import InMemoryHistory

            command_names = list(_command_names())

            class _AthenaCompleter(Completer):
                def get_completions(self, document, complete_event):
                    word = document.get_word_before_cursor()
                    if word.startswith(("/", "\\")):
                        prefix = word[1:].lower()
                        for name in command_names:
                            if name.startswith(prefix):
                                yield Completion(name, start_position=-len(word))
                    elif word:
                        for name in command_names:
                            if name.startswith(word.lower()):
                                yield Completion("/" + name, start_position=-len(word))

            # THE STATE: the page + the body lines (the alternating history).
            pages = ["Home", "Chat", "Sessions", "Vault", "Usage", "Settings"]
            page_idx = [1]                     # default to Chat
            # THE WELCOME SEED (the no-flicker fix): the banner + status
            # are the body's FIRST lines; chat lines append below.
            body_lines: list = list(welcome_lines)   # [(style, text)]
            input_text = [""]

            def _render_header() -> FormattedText:
                """The header: Athena identity + the page tabs ([] swap)."""
                frags = []
                frags.append(("class:head-brand",
                              f"  ATHENA · {profile_name} · v{self._version()}  "))
                frags.append(("class:head-dim", "  ["))
                for i, p in enumerate(pages):
                    if i == page_idx[0]:
                        frags.append(("class:head-tab-active", f" {p} "))
                    else:
                        frags.append(("class:head-tab", f" {p} "))
                    if i < len(pages) - 1:
                        frags.append(("class:head-dim", "|"))
                frags.append(("class:head-dim", "]  ([ / ] swap)\n"))
                frags.append(("class:head-border", "  " + "─" * 66 + "\n"))
                return FormattedText(frags)

            def _render_body() -> FormattedText:
                """The body: the ACTIVE PAGE's content. THE 08-16 FIX: the
                [ / ] keys switch page_idx — the body now renders the
                page's content (previously it always showed the chat, so
                the pages appeared to do nothing).

                Pages: Home (welcome) · Chat (history) · Sessions ·
                Vault · Usage · Settings.
                NOTE: the newline goes INSIDE each fragment's text (a
                standalone ("\\n", "") fragment is ignored by prompt_toolkit
                — that was the single-line bug)."""
                frags = []
                _page = pages[page_idx[0]].lower()
                # HOME — the seeded welcome (banner + status).
                if _page == "home":
                    for style, text in welcome_lines:
                        frags.append((style, text + "\n"))
                    return FormattedText(frags)
                # CHAT — the alternating history (the default).
                if _page == "chat":
                    if not body_lines:
                        frags.append(("class:body-dim",
                                      "  Empty history — the terminal has no "
                                      "history for this session yet.\n"))
                    else:
                        max_lines = 30
                        tail = body_lines[-max_lines:]
                        for style, text in tail:
                            frags.append((style, text + "\n"))
                    return FormattedText(frags)
                # SESSIONS — the session list.
                if _page == "sessions":
                    try:
                        from core import db as _db
                        last = _db.find_last_session(profile=self.profile.name)
                        frags.append(("class:head-tab-active",
                                      "  Sessions — the recent sessions\n"))
                        frags.append(("class:body-agent",
                                      f"  current: {self.session_id[:12]}…\n"))
                        if last:
                            frags.append(("class:body-status",
                                          f"  last:    {last[:12]}…\n"))
                        frags.append(("class:body-dim",
                                      "  (/session new to start a fresh one)\n"))
                    except Exception:
                        frags.append(("class:body-dim",
                                      "  (sessions unavailable)\n"))
                    return FormattedText(frags)
                # VAULT — the archive query.
                if _page == "vault":
                    try:
                        from core import db as _db
                        rows = _db.query_index("chat", limit=8,
                                               profile=self.profile.name)
                        frags.append(("class:head-tab-active",
                                      "  Vault — the archive (recent)\n"))
                        if rows:
                            for row in rows[:8]:
                                txt = str(row.get("content") or row.get("text")
                                          or row)[:60]
                                frags.append(("class:body-status",
                                              f"  • {txt}\n"))
                        else:
                            frags.append(("class:body-dim",
                                          "  (no vault rows yet)\n"))
                    except Exception:
                        frags.append(("class:body-dim",
                                      "  (vault unavailable)\n"))
                    return FormattedText(frags)
                # USAGE — the token/cost meter.
                if _page == "usage":
                    try:
                        from context.compression import usage_since_baseline
                        from core.config import load_config
                        _u = usage_since_baseline(
                            self.profile.name if not self.profile.is_default else "")
                        _cfg = load_config()
                        _budget = _cfg.get("iteration_budget", {}) or {}
                        _avail = (int(_budget.get("main_max_tokens", 5120) or 5120)
                                  * int(_budget.get("main_iterations", 100) or 100))
                        _pct = (_u / _avail * 100.0) if _avail else 0.0
                        frags.append(("class:head-tab-active",
                                      "  Usage — the token meter\n"))
                        frags.append(("class:body-status",
                                      f"  used: {_u:,} tokens ({_pct:.1f}%)\n"))
                        frags.append(("class:body-status",
                                      f"  budget: {_avail:,} tokens\n"))
                    except Exception:
                        frags.append(("class:body-dim",
                                      "  (usage unavailable)\n"))
                    return FormattedText(frags)
                # SETTINGS — the config view.
                if _page == "settings":
                    try:
                        from core.config import load_config, CONFIG_PATH
                        _cfg = load_config()
                        frags.append(("class:head-tab-active",
                                      f"  Settings — {CONFIG_PATH}\n"))
                        _sel = (_cfg.get("provider", {}).get("selection", {})
                                or {})
                        _stream = _cfg.get("provider", {}).get("streaming", True)
                        frags.append(("class:body-status",
                                      f"  provider: {_sel.get('provider', 'none')}\n"))
                        frags.append(("class:body-status",
                                      f"  model:    {_sel.get('model', 'not set')}\n"))
                        frags.append(("class:body-status",
                                      f"  streaming: {'true' if _stream else 'false'}\n"))
                        frags.append(("class:body-dim",
                                      "  (/streaming set true|false to toggle)\n"))
                    except Exception:
                        frags.append(("class:body-dim",
                                      "  (settings unavailable)\n"))
                    return FormattedText(frags)
                # Fallback — the chat.
                for style, text in body_lines[-20:]:
                    frags.append((style, text + "\n"))
                return FormattedText(frags)

            def _render_footer() -> FormattedText:
                """The footer: the hotbar + the runtime statuses (the
                website footer's Left/Center/Right elements)."""
                frags = []
                frags.append(("class:foot-border", "  " + "─" * 66 + "\n"))
                # Left: version/server/runtime · Center: tokens · Right: pages
                try:
                    from cli.banner import runtime_footer
                    rf = runtime_footer(profile=profile_name,
                                        session_id=getattr(
                                            self.loop, "session_id", "") or "")
                    # THE 08-16 WIDTH FIX: the status line is TRUNCATED
                    # to the window width (no wrap → it never overlaps the
                    # hotbar below — that was the digit-garbage corruption).
                    try:
                        _w = _repl_app.output.get_size().columns if _repl_app else 120
                    except Exception:
                        _w = 120
                    rf = rf[: max(_w - 4, 20)]
                    frags.append(("class:foot-status", f"  {rf}\n"))
                except Exception:
                    frags.append(("class:foot-status", "\n"))
                frags.append(("class:foot-hotbar", "  " + hotbar_plain() + "\n"))
                return FormattedText(frags)

            # THE INPUT LINE: a Buffer at the bottom of the body.
            input_buffer = Buffer(history=InMemoryHistory(),
                                  completer=_AthenaCompleter(),
                                  complete_while_typing=False,
                                  multiline=False)

            def _accept(buff: Buffer) -> bool:
                text = buff.text.strip()
                if not text:
                    return True
                input_text[0] = text
                buff.text = ""
                # THE 08-16 ONE-PAGE FIX: the app NEVER exits for a
                # message. The turn runs as a BACKGROUND TASK on the app's
                # OWN event loop (create_background_task) — the window
                # keeps rendering, the thinking/reply stream into the
                # body, and there is NO cross-thread freeze (the previous
                # raw-thread + invalidate approach could stall in some
                # prompt_toolkit builds).
                if _repl_app:
                    import asyncio as _aio
                    async def _run_turn():
                        loop = _aio.get_running_loop()
                        # The synchronous turn runs in the executor; the
                        # coroutine awaits it so the event loop stays free.
                        await loop.run_in_executor(None, _process_line, text)
                    _repl_app.create_background_task(_run_turn())
                return True

            def _process_line(line: str) -> None:
                """Process a submitted line (command or chat) in a
                background thread — the app keeps rendering."""
                # Commands run synchronously (fast); chat runs async.
                parsed = self.parse_command(line)
                if parsed is not None:
                    module, args = parsed
                    body_lines.append(("class:body-op",
                                       f"  {operator_name} >>>  {line}"))
                    # THE 08-16 COMMAND-OUTPUT ROUTING: the command's
                    # print() output goes INTO the body (captured), never
                    # to stdout where it would escape the window + glue
                    # into the input line. THE 08-16 WIDTH FIX (Option C):
                    # Rich tables (/help etc.) render at the WINDOW's real
                    # width (via a width-aware Console + export_text) —
                    # never the narrow StringIO width that broke the
                    # columns.
                    try:
                        import io as _io, contextlib as _ctx
                        # The window width (for the Rich console).
                        try:
                            _win_w = (_repl_app.output.get_size().columns
                                      if _repl_app else 120)
                        except Exception:
                            _win_w = 120
                        _buf = _io.StringIO()
                        with _ctx.redirect_stdout(_buf):
                            self.run_command(module, args)
                        _out = _buf.getvalue().strip()
                        if _out:
                            for _ol in _out.splitlines():
                                body_lines.append(("class:body-agent",
                                                   f"  {_ol}"))
                    except SystemExit:
                        return
                    except Exception as exc:  # noqa: BLE001
                        try:
                            from core.logging import log_event
                            log_event(4, f"cli command '{module}' failed: {exc}",
                                      source="cli", action=f"cmd_{module}",
                                      profile=self.profile.name)
                        except Exception:
                            pass
                        body_lines.append(("class:body-stop",
                                           f"  ✗ command failed: {exc}"))
                    try:
                        _repl_app.invalidate()
                    except Exception:
                        pass
                    return
                # A chat message — the alternating display, streamed.
                body_lines.append(("class:body-op",
                                   f"  {operator_name} >>>  {line}"))
                # THE START MARKER (kept — the Operator's spec): who is
                # talking. The END marker ((done)) is gone.
                body_lines.append(("class:body-thinking",
                                   f"  <<< {agent_name} is Thinking…"))
                try:
                    _repl_app.invalidate()
                except Exception:
                    pass
                # THE LIVE-STREAM HOOK: thinking + reply → the body.
                _pending_reply = [""]

                def _persist(delta: str, final: bool = False,
                             flow: bool = False) -> None:
                    """Append the reply/flow stream to the body (live).

                    delta       — a reply chunk or a flow line
                    final=True  — replace the current line with the full
                                  reply (from the loop's response)
                    flow=True   — a THINKING-FLOW line (append a new line)
                    """
                    if flow:
                        # THINKING-FLOW lines = ORANGE (the thinking role).
                        body_lines.append(("class:body-thinking",
                                           "  " + delta.rstrip()))
                    elif final:
                        _pending_reply[0] = delta
                        body_lines[-1] = (
                            "class:body-agent",
                            f"  <<< {agent_name}  {_pending_reply[0]}")
                    else:
                        _pending_reply[0] += delta
                        body_lines[-1] = (
                            "class:body-agent",
                            f"  <<< {agent_name}  {_pending_reply[0]}")
                        # THE 08-16 STREAM PACING: give the renderer a
                        # tick per delta so the text TYPES OUT visibly
                        # (without this, the deltas batch and the reply
                        # appears whole when the turn ends).
                        try:
                            import time as _t
                            _t.sleep(0.012)
                        except Exception:
                            pass
                    try:
                        _repl_app.invalidate()
                    except Exception:
                        pass

                self._persist_window = _persist
                try:
                    self.cmd_send(line)
                except Exception as exc:  # noqa: BLE001
                    # THE SEND-FAILURE LOG: a failed send lands in the metric
                    # stream (L4) — then retry once (the original intent).
                    try:
                        from core.logging import log_event
                        log_event(4, f"cli send failed: {exc}",
                                  source="cli", action="send",
                                  profile=self.profile.name)
                    except Exception:
                        pass
                    try:
                        self.cmd_send(line)
                    except Exception as exc2:  # noqa: BLE001
                        try:
                            from core.logging import log_event
                            log_event(4, f"cli send retry failed: {exc2}",
                                      source="cli", action="send",
                                      profile=self.profile.name)
                        except Exception:
                            pass
                        raise
                self._persist_window = None
                try:
                    _repl_app.invalidate()
                except Exception:
                    pass

            input_buffer.accept_handler = _accept
            _repl_app = None   # set after creation (the accept handler refs it)

            kb = KeyBindings()

            @kb.add("[")
            def _(event):
                page_idx[0] = (page_idx[0] - 1) % len(pages)
                event.app.invalidate()

            @kb.add("]")
            def _(event):
                page_idx[0] = (page_idx[0] + 1) % len(pages)
                event.app.invalidate()

            @kb.add("c-e")
            def _(event):
                event.app.exit(result=("exit", ""))

            @kb.add("c-c")
            def _(event):
                # THE 08-16 STOP: CTRL+C stops Athena from reasoning THIS
                # turn (the same mechanism the website's Stop button uses) —
                # the program stays, the turn cancels.
                try:
                    self.loop._interrupt.set()
                except Exception:
                    pass
                body_lines.append(("class:body-stop",
                                   "  ⏹ [CTRL+C] stopped Athena's current turn"))
                event.app.invalidate()

            # The layout: header (top) / body (middle, input at its bottom) /
            # footer (bottom). The input is a BufferControl inside the body.
            header = Window(FormattedTextControl(_render_header),
                            dont_extend_height=True, wrap_lines=True)
            footer = Window(FormattedTextControl(_render_footer),
                            dont_extend_height=True, wrap_lines=True)
            input_win = Window(BufferControl(
                buffer=input_buffer,
                focusable=True,
                input_processors=[
                    BeforeInput(FormattedText(
                        [("class:prompt",
                          f"{operator_name} Input >>> ")]))]),
                dont_extend_height=True)
            body = Window(FormattedTextControl(_render_body), wrap_lines=True)
            layout = Layout(
                HSplit([
                    header,
                    HSplit([body, input_win]),
                    footer,
                ])
            )

            # THE 08-16 THEME (the Operator's spec): the CLI window uses
            # the SAME 5-color palettes as the website (config theme).
            # /theme set {light|dark} flips the mode; the window re-renders
            # with the palette's colors (primary/secondary/text).
            def _current_style() -> Style:
                from cli.colors import theme_colors
                c = theme_colors()          # reads the active mode
                return Style.from_dict({
                    "head-brand":   f"bold {c['secondary']}",  # the highlight
                    "head-tab":     c["primary"],               # the accent
                    "head-tab-active": f"bold {c['primary']}",
                    "head-dim":     "#888888",
                    "head-border":  c["primary"],
                    "foot-border":  c["primary"],
                    "foot-status":  c["secondary"],
                    "foot-hotbar":  f"bold {c['primary']}",
                    "body-dim":     "#888888",
                    "body-stop":    f"bold {c['primary']}",
                    "body-banner":  f"bold {c['secondary']}",
                    # THE 3-COLOR BANNER (the 08-16 spec): red → orange →
                    # yellow (2 rows each — the logo's gradient).
                    "body-banner-ff3b30": "bold #FF3B30",   # red
                    "body-banner-ff8c00": "bold #FF8C00",   # orange (saturated)
                    "body-banner-ffcc00": "bold #FFCC00",   # yellow (saturated)
                    "body-status":  c["primary"],
                    # THE TURN COLORS (the Operator's 08-16 spec):
                    #   OPERATOR (user) = YELLOW, THINKING = ORANGE,
                    #   AGENT = RED — the fixed roles, any mode.
                    "body-op":      "bold #FFCC00",   # user = yellow
                    "body-thinking": "bold #FF8C00",  # thinking = orange
                    "body-agent":   "bold #FF3B30",   # agent = red
                    "prompt":       "bold #FFCC00",   # the input prompt = yellow
                })

            _repl_app = Application(
                layout=layout,
                key_bindings=kb,
                style=_current_style(),
                full_screen=True,
                mouse_support=False,
            )
            # THE THEME HOOK: /theme re-applies the window style live.
            self._repl_app_ref = _repl_app
            self._current_style = _current_style

            # THE 08-16 BLACK-SCREEN FIX: clear NOW — the seed is already
            # built, so the window opens on the fresh screen with the
            # welcome already in the body (no black gap from a slow seed).
            try:
                from cli.banner import clear_screen
                clear_screen()
            except Exception:
                pass

            # THE MAIN LOOP: the app runs continuously — lines are
            # processed in background threads (the window stays on ONE
            # page). Ctrl+E returns ("exit", "").
            while True:
                try:
                    result = _repl_app.run()
                except Exception:
                    break
                if result == ("exit", "") or (
                        isinstance(result, tuple) and result[0] == "exit"):
                    break
        except (Exception, KeyboardInterrupt) as _repl_exc:
            # THE 08-16 BLACK-SCREEN FIX: catch KeyboardInterrupt too — a
            # Ctrl+C during setup (the user pressing it on a still-cleared
            # screen) is a BaseException, NOT an Exception, so the old
            # handler let it CRASH the CLI. Now it falls back to the
            # input() loop (which handles Ctrl+C cleanly) instead.
            try:
                metric_log(2, "cli persistent window unavailable — "
                              f"falling back ({_repl_exc})",
                           profile=profile_name, source="cli")
            except Exception:
                pass
            # Fallback: the original input() loop (no prompt_toolkit).
            print(f"{tag()} persistent window unavailable — "
                  f"falling back ({_repl_exc})")
            try:
                while True:
                    try:
                        line = input(bold(orange(f"{operator_name} Input >>> "))).strip()
                    except (EOFError, KeyboardInterrupt):
                        print()
                        break
                    if not line:
                        continue
                    parsed = self.parse_command(line)
                    if parsed is not None:
                        module, args = parsed
                        try:
                            self.run_command(module, args)
                        except SystemExit:
                            break
                        except Exception as exc:  # noqa: BLE001
                            try:
                                from core.logging import log_event
                                log_event(4, f"cli command '{module}' failed: {exc}",
                                          source="cli", action=f"cmd_{module}")
                            except Exception:
                                pass
                            print(f"{tag()} {red('command failed')}: {exc}")
                        continue
                    # Not a slash command — treat as a chat message (native).
                    self._show_flow("user")
                    try:
                        from cli.banner import LiveFlow
                        from core.config import flow_names
                        agent, _operator = flow_names()
                        self._flow = LiveFlow()
                        self._flow.start(f"{agent} is Thinking…")
                    except Exception:
                        self._flow = None
                    try:
                        self.cmd_send(line)
                    except Exception as exc:  # noqa: BLE001
                        # THE SEND-FAILURE LOG (fallback path): same as the
                        # persistent-window path — L4, retry once, L4 again,
                        # then re-raise (the watchdog records the crash).
                        try:
                            from core.logging import log_event
                            log_event(4, f"cli send failed: {exc}",
                                      source="cli", action="send",
                                      profile=self.profile.name)
                        except Exception:
                            pass
                        try:
                            self.cmd_send(line)
                        except Exception as exc2:  # noqa: BLE001
                            try:
                                from core.logging import log_event
                                log_event(4, f"cli send retry failed: {exc2}",
                                          source="cli", action="send",
                                          profile=self.profile.name)
                            except Exception:
                                pass
                            raise
                    self._flow = None
                    self._show_flow("assistant")
            finally:
                pass
        # THE CLEANUP (preserved from the original): log the CLI end +
        # stop the sidecar server BEFORE the session closes.
        try:
            metric_log(1, "cli session ended", profile=profile_name, source="cli")
        except Exception:
            pass
        try:
            self._stop_server()
        except Exception:
            pass
        return 0

    def _version(self) -> str:
        try:
            from core.config import VERSION
            return VERSION
        except Exception:
            return ""

    def _show_flow(self, stage: str) -> None:
        """The {Operator} ›› {Agent} is Thinking ›› {Agent} flow per stage.

        Shows the turn as it happens: the OPERATOR's input enters, the
        AGENT thinks (the system/events layer works), the ASSISTANT side
        (the agent's name) returns the output. The live tool lines render
        between the two markers.
        """
        try:
            from core.config import flow_names
            from rich.console import Console
            from cli.colors import dim
            agent, operator = flow_names()
            console = Console()
            if stage == "user":
                console.print(dim(f"  [{operator}] input entered ›› {agent} is Thinking"))
            else:
                console.print(dim(f"  {agent} is Thinking ›› [{agent}] output returned"))
        except Exception:
            pass


def _core_command_names() -> list[str]:
    """The CORE command names (no tool wrappers).

    Bare-form parsing checks these — a sentence starting with a tool
    name ('list the files…') stays chat; only real core commands
    (provider, model, kanban, …) trigger bare-form command parsing.
    """
    return ["send", "session", "status", "kanban", "cron", "profile",
            "security", "backup", "skills", "plugins", "tools", "config",
            "version", "doctor", "logs", "lifecycle", "nurse", "events",
            "curator", "provider", "model", "emotion", "mdformat",
            "vault", "index", "theme", "streaming", "help", "quit", "exit"]


def _command_names() -> list[str]:
    """All registered command names (the auto-complete vocabulary).

    Commands register from the core surface AND from the tool registry
    automatically — the completer always reflects what's available.
    """
    try:
        from autonomy.commands import register_core_commands, list_commands
        register_core_commands()
        return list_commands()
    except Exception:
        return ["send", "session", "kanban", "cron", "profile", "status",
                "security", "backup", "skills", "plugins", "tools", "config",
                "version", "doctor", "logs", "lifecycle", "nurse", "events",
                "curator", "provider", "model", "help", "quit"]


def _install_metrics_watchdog(profile: str = "") -> None:
    """THE METRICS WATCHDOG for the CLI process (mirrors the server's in
    core/main.py): an unhandled exception in the MAIN thread OR the daemon
    server thread lands in the consolidated log stream as an L5 CRITICAL
    entry, so a CLI crash is always recorded ("captures everything until
    it cannot anymore"). Installed BEFORE the runtime is constructed so a
    boot-time failure is caught too.
    """
    import sys as _sys
    import threading as _thr
    import traceback as _tb
    from metrics.logger import log

    _orig_excepthook = _sys.excepthook

    def _metrics_excepthook(exc_type, exc_value, exc_tb):
        try:
            _detail = "".join(_tb.format_exception(exc_type, exc_value, exc_tb))
            log(5, f"unhandled exception: {exc_value}\n{_detail}",
                profile=profile, source="cli", tool="watchdog", action="crash")
        except Exception:
            pass
        _orig_excepthook(exc_type, exc_value, exc_tb)

    _sys.excepthook = _metrics_excepthook

    # THE DAEMON-THREAD WATCHDOG: the server loop the CLI spawns beside the
    # REPL runs on a daemon thread (cli/main.py _start_server) — a crash
    # there must also land in the metric stream (threads skip sys.excepthook).
    try:
        _orig_threadhook = _thr.excepthook

        def _metrics_threadhook(args):
            try:
                _detail = "".join(_tb.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback))
                log(5, f"unhandled thread exception: {args.exc_value}\n{_detail}",
                    profile=profile, source="cli", tool="watchdog", action="crash")
            except Exception:
                pass
            _orig_threadhook(args)

        _thr.excepthook = _metrics_threadhook
    except Exception:
        pass

    try:
        import atexit as _atexit

        def _metrics_atexit():
            try:
                from metrics.logger import log
                log(2, "process exiting — watchdog atexit fired",
                    profile=profile, source="cli", tool="watchdog", action="exit")
            except Exception:
                pass
        _atexit.register(_metrics_atexit)
    except Exception:
        pass


def main(profile: str = "") -> int:
    # THE METRICS WATCHDOG (the Operator's 08-12 spec): the CLI records its
    # own crashes — unhandled exceptions and the process exit — the same way
    # the server does. Resolve the canonical profile name so the entries land
    # in the SAME log file as the CLI session markers.
    try:
        from intelligence.profiles import get_profile, default_profile
        _p = get_profile(profile) or default_profile()
        _install_metrics_watchdog(_p.name)
    except Exception:
        try:
            _install_metrics_watchdog(profile or "")
        except Exception:
            pass
    return CLI(profile=profile).repl()


if __name__ == "__main__":
    raise SystemExit(main())

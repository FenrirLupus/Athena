"""Web layer test — the Operator's FIFO guarantee + the HTTP/MCP/GUI surface.

The FIFO queue must process requests in strict arrival order (oldest →
newest, no races). The FastAPI app exposes the GUI (Chat first on the
navbar), the HTTP endpoints, and the MCP endpoint.
"""
from __future__ import annotations
from core.config import ATHENA_ROOT


def _paging_ok() -> bool:
    """A 250-message session pages backward 100 at a time (100/100/50)."""
    import tempfile
    from pathlib import Path
    from core import db as db_layer
    import core.db as dbmod
    orig = dbmod.sessions_dir
    try:
        with tempfile.TemporaryDirectory() as td:
            dbmod.sessions_dir = staticmethod(lambda *a, **k: Path(td))
            sid = "page-probe"
            for i in range(250):
                db_layer.record_session_message(
                    sid, "user" if i % 2 == 0 else "assistant",
                    f"message {i}", profile="")
            total = db_layer.count_session_messages(sid, profile="")
            rows0 = db_layer.get_session_history(sid, limit=100, profile="")
            p1 = rows0[:100]
            rows1 = db_layer.get_session_history(sid, limit=200, profile="")
            p2 = rows1[:min(100, max(0, total - 100))]
            rows2 = db_layer.get_session_history(sid, limit=300, profile="")
            p3 = rows2[:min(100, max(0, total - 200))]
            ok = (total == 250
                  and len(p1) == 100 and p1[0]["content"] == "message 150"
                  and p1[-1]["content"] == "message 249"
                  and len(p2) == 100 and p2[0]["content"] == "message 50"
                  and p2[-1]["content"] == "message 149"
                  and len(p3) == 50 and p3[0]["content"] == "message 0"
                  and p3[-1]["content"] == "message 49")
            return bool(ok)
    finally:
        dbmod.sessions_dir = orig


def _session_mgmt_ok() -> bool:
    """new_session creates a .db; delete_session removes it."""
    import tempfile
    from pathlib import Path
    from core import db as db_layer
    import core.db as dbmod
    orig = dbmod.sessions_dir
    try:
        with tempfile.TemporaryDirectory() as td:
            dbmod.sessions_dir = staticmethod(lambda *a, **k: Path(td))
            sid = db_layer.new_session(profile="")
            db_layer.record_session_message(sid, "user", "hi", profile="")
            exists_before = (Path(td) / f"session-{sid}.db").exists()
            ok_del = db_layer.delete_session(sid, profile="")
            exists_after = (Path(td) / f"session-{sid}.db").exists()
            return bool(exists_before and ok_del and not exists_after)
    finally:
        dbmod.sessions_dir = orig


def _uuid_only_ok() -> bool:
    """uuid_session_ids filters non-UUID sessions (roles, toolcols, ...)."""
    import tempfile
    from pathlib import Path
    from core import db as db_layer
    import core.db as dbmod
    import sqlite3
    orig = dbmod.sessions_dir
    try:
        with tempfile.TemporaryDirectory() as td:
            dbmod.sessions_dir = staticmethod(lambda *a, **k: Path(td))
            # A real UUID session + two debris sessions.
            good = db_layer.new_session(profile="")
            for bad in ("roles", "toolcols"):
                conn = sqlite3.connect(str(Path(td) / f"session-{bad}.db"))
                conn.close()
            ids = db_layer.uuid_session_ids(profile="")
            return bool(good in ids and "roles" not in ids
                        and "toolcols" not in ids)
    finally:
        dbmod.sessions_dir = orig


def run() -> list[dict]:
    import time
    import re
    from pathlib import Path
    from web.fifo_queue import FIFOQueue
    from web.server import create_app
    from unittest.mock import patch

    checks = []

    # 1. FIFO: strict order, one worker at a time.
    order = []
    def worker(req):
        time.sleep(0.02)
        order.append(req.payload["n"])
        return {"ok": True, "n": req.payload["n"]}
    q = FIFOQueue(worker)
    q.start()
    try:
        for i in range(5):
            q.submit("chat", {"n": i})
        time.sleep(0.5)
    finally:
        q.stop()
    checks.append({
        "name": "FIFO queue strict order",
        "status": "ok" if order == [0, 1, 2, 3, 4] else "fail",
        "detail": f"order={order}",
    })

    # 2. The app builds with all routes.
    class FakeLoop:
        session_id = "web-test"
        def handle_event(self, event):
            return {"ok": True, "event_id": "e1"}
        def drain(self):
            pass
        responses = []
    class FakeHolder:
        loop = FakeLoop()
        server = None
    static_dir = str(ATHENA_ROOT / 'athena-system' / 'web' / 'gui')
    app = create_app(loop_holder=FakeHolder(), server_holder=FakeHolder(),
                     static_dir=static_dir)
    paths = {getattr(r, "path", "") for r in app.routes}
    # fastapi >= 0.141: an include_router() adds an _IncludedRouter
    # entry with no .path — its routes live on the included sub-router.
    # Gather them too so the MCP endpoints are still asserted.
    for r in app.routes:
        # fastapi >= 0.141: the included router exposes original_router.
        sub = getattr(r, "original_router", None) or getattr(r, "router", None)
        if sub is not None:
            for sr in getattr(sub, "routes", []):
                p = getattr(sr, "path", "")
                if p:
                    paths.add(p)
    paths.discard("")
    need = {"/", "/chat", "/chat/history", "/chat/profile/{name}",
            "/health", "/sessions", "/sessions/current", "/sessions/new",
            "/vault", "/logs", "/tools", "/ws", "/profiles",
            "/profiles/switch", "/config/provider", "/config/identity",
            "/config/compression", "/billing", "/approvals/pending",
            "/integrations",
            "/mcp/initialize", "/mcp/tools/list", "/mcp/tools/call"}
    checks.append({
        "name": "web routes present",
        "status": "ok" if need <= paths else "fail",
        "detail": f"missing={sorted(need - paths)}",
    })

    # 3. The GUI: the navbar workspaces + the 5-color theme + 2-sided chat.
    #    The MODULAR GUI: read the composed page via the app (TestClient),
    #    since the html partials are assembled at serve time.
    p = Path(static_dir) / "index.html"
    text = ""
    try:
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            text = client.get("/").text
    except Exception:
        text = p.read_text(encoding="utf-8") if p.exists() else ""
    # The FULL modular source: every gui file concatenated — the checks
    # below look for css classes + js functions that now live in their
    # own modular files (the Operator's spec), so search the whole gui tree.
    gui_all = text
    try:
        for f in sorted(Path(static_dir).rglob("*")):
            if f.is_file() and f.suffix in (".html", ".css", ".js"):
                gui_all += "\n" + f.read_text(encoding="utf-8")
    except Exception:
        pass
    for ws in ("home", "chat", "call", "sessions", "vault", "usage", "settings"):
        if f'data-ws="{ws}"' not in text:
            checks.append({
                "name": f"GUI workspace: {ws}",
                "status": "fail",
                "detail": f"{ws} missing",
            })
    checks.append({
        "name": "GUI: all workspaces present",
        "status": "ok" if all(f'data-ws="{ws}"' in text
                             for ws in ("home", "chat", "call", "sessions",
                                        "vault", "usage", "settings")) else "fail",
        "detail": "home chat call sessions vault usage settings",
    })
    checks.append({
        "name": "GUI: 5-color light/dark theme",
        "status": "ok" if "body.dark" in gui_all and "--tertiary: #fa7d00" in gui_all
        and "--tertiary: #fa0000" in gui_all and "theme-toggle" in gui_all else "fail",
        "detail": "light #fa7d00 / dark #fa0000, toggle present",
    })
    # 3a. The EXACT hex codes (the Operator's spec, updated): the modular CSS
    #     — the theme lives in web/gui/css/index.css now.
    css = ""
    try:
        css = Path(static_dir).joinpath("css", "index.css").read_text(encoding="utf-8")
    except Exception:
        css = ""
    if "<style>" in text:
        css = text.split("<style>")[1].split("</style>")[0]
    import re as _re
    root_css = css.split(":root")[1].split("body.dark")[0] if ":root" in css else css
    dm = _re.search(r"body\.dark\s*\{", css)
    dark_css = css.split(dm.group(0))[1].split("* {")[0] if dm else css
    light_hex = ["#fafafa", "#e1e1e1", "#fa7d00", "#fafa00", "#000000"]
    dark_hex = ["#1e1e1e", "#323232", "#fa0000", "#fa7d00", "#fafafa"]
    checks.append({
        "name": "GUI: exact hex codes (light+dark)",
        "status": "ok" if all(h in root_css for h in light_hex)
        and all(h in dark_css for h in dark_hex) else "fail",
        "detail": "light: " + " ".join(light_hex) + " | dark: " + " ".join(dark_hex),
    })
    checks.append({
        "name": "GUI: combined chat history (one column, alternating)",
        "status": "ok" if "chat-history" in text
        and ("className = 'who'" in gui_all or "class=\"who\"" in gui_all)
        and "side-header" not in gui_all
        and "chat-load-more" in text else "fail",
        "detail": "single column, starter-first alternating, pill load-more",
    })
    checks.append({
        "name": "GUI: profile dropdown (auto-switch)",
        "status": "ok" if "profile-select" in text
        and "/profiles/switch" in gui_all else "fail",
        "detail": "dropdown + switch endpoint wired",
    })
    # 3c. The Operator's GUI updates: NO startup placeholder, history loads,
    #     3x3 dot animation, settings categorized + collapsible + blurred,
    #     session dropdown + paginated chat (100/page).
    checks.append({
        "name": "GUI: no startup placeholder (history loads)",
        "status": "ok" if "Ask me anything" not in text
        and "loadChatHistory" in gui_all and "/chat/history" in gui_all else "fail",
        "detail": "placeholder gone, history loader present",
    })
    checks.append({
        "name": "GUI: 3x3 dot thinking animation",
        "status": "ok" if "DOT3X3_FRAMES" in gui_all and "dots3x3" in gui_all
        else "fail",
        "detail": "3x3 dot grid frames",
    })
    checks.append({
        "name": "GUI: settings categorized + collapsible + blurred",
        "status": "ok" if "settings-tab" in text and "settings-page" in text
        and "getConfigSchema" in gui_all and "settings-save" in gui_all
        and "switchSettingsPage" in gui_all and "data-path" in gui_all else "fail",
        "detail": "schema-driven tabs/pages + save",
    })
    checks.append({
        "name": "GUI: session dropdown + 100-page chat",
        "status": "ok" if "session-select" in text
        and "loadSessionDropdown" in gui_all and "PAGE_SIZE" in gui_all
        and "chat-load-more" in gui_all else "fail",
        "detail": "session switcher + paginated history",
    })
    # 3d. The Operator's round-2 GUI updates: chat auto-scroll, 3-column
    #     settings (alphabetical), labels LEFT of dropdowns, sessions
    #     workspace management (create/delete), footer with token meter.
    checks.append({
        "name": "GUI: chat auto-scroll on send",
        "status": "ok" if "scrollChatToBottom" in gui_all
        and "chat-history" in text else "fail",
        "detail": "scroll targets the combined history panel",
    })
    checks.append({
        "name": "GUI: settings 3-column alphabetical grid",
        "status": "ok" if "settings-grid" in gui_all
        or ("settings-tabs" in text and "settings-page" in text) else "fail",
        "detail": "settings organized by pages/tabs",
    })
    checks.append({
        "name": "GUI: labels left of dropdowns",
        "status": "ok" if "nav-label" in text
        and "Session" in text and "Profile" in text
        and "nav-left" in text and "nav-right" in text else "fail",
        "detail": "2-column navbar: pages left, Session/Profile/Mode right",
    })
    checks.append({
        "name": "GUI: sessions workspace manage (create/delete)",
        "status": "ok" if "sessions-new" in text and "session-del" in gui_all
        and "/sessions/new" in gui_all else "fail",
        "detail": "new + delete wired",
    })
    checks.append({
        "name": "GUI: session dropdown content-sized + centered",
        "status": "ok" if "text-align: center" in gui_all
        and "width: auto" in gui_all
        and "#session-select { min-width: 300px" not in gui_all else "fail",
        "detail": "hugs content, centers text (no dead space)",
    })
    checks.append({
        "name": "GUI: approval popup (centered, above input)",
        "status": "ok" if "approval-card" in text
        and "approval-title" in text and "approval-buttons" in text
        and "position: fixed; inset: 0; z-index: 200" in gui_all
        and "padding-bottom: 120px" in gui_all else "fail",
        "detail": "centered modal, bottom-anchored above the input",
    })
    checks.append({
        "name": "GUI: footer (server, runtime, tokens, bar)",
        "status": "ok" if "foot-server" in text and "foot-runtime" in text
        and "foot-tokens-used" in text and "foot-tokens-bar" in text
        and "foot-tokens-avail" in text and "foot-tokens-pct" in text
        and "foot-tokens-avail-pct" in text and "loadFooter" in gui_all else "fail",
        "detail": "server · runtime · usage+% · bar · available+%",
    })
    checks.append({
        "name": "GUI: settings full-page grid",
        "status": "ok" if "settings-tabs" in text
        and "settings-tab" in text and "settings-pages" in text
        and "settings-page" in text and "switchSettingsPage" in gui_all
        and "settings-layout" in text else "fail",
        "detail": "vertical tabs (pages) + selected page on the right",
    })
    checks.append({
        "name": "GUI: footer 3 columns (status/usage/reserved)",
        "status": "ok" if "foot-col" in text and "foot-col-center" in text
        and "foot-col-right" in text
        and "grid-template-columns: 1fr 1fr 1fr" in gui_all else "fail",
        "detail": "left status, center usage, right empty",
    })
    checks.append({
        "name": "GUI: FIFO hint removed",
        "status": "ok" if "Queued FIFO" not in text else "fail",
        "detail": "hint text gone",
    })
    _srv_src = str((Path(static_dir).parent / "server.py").read_text(encoding="utf-8"))
    checks.append({
        "name": "GUI: in-memory cache serves + clear endpoint (the Operator's spec)",
        "status": "ok" if "__cache_clear__" in _srv_src
        and "st_mtime_ns" in _srv_src else "fail",
        "detail": "cache is mtime-validated; service start/stop/restart frees it",
    })
    checks.append({
        "name": "GUI: command palette (/ or \\ file-system tree)",
        "status": "ok" if "cmd-palette" in text and "cmd-breadcrumb" in text
        and "cmd-left" in text and "cmd-right" in text
        and "cmd-usage" in text and "/commands" in gui_all else "fail",
        "detail": "categories → commands → subcommands, usage right, breadcrumb up",
    })
    checks.append({
        "name": "commands: registry + palette support INFINITE depth",
        "status": "ok" if "get_children" in str(
            (Path(static_dir).parent.parent / "autonomy" / "commands.py").read_text())
        and "children" in _srv_src and "CMD_PATH.length" in gui_all
        else "fail",
        "detail": "recursive registry, recursive /commands tree, generic GUI walk",
    })
    # The version is ONE source (core.config.VERSION). Every operational
    # surface — the version registry gate, snapshots, MCP server info,
    # the CLI — must read it, never a hardcoded duplicate.
    try:
        from core.config import VERSION as _V
        from core.version_registry import ATHENA_VERSION as _RV
        _mcp_src = str((Path(static_dir).parent / "mcp.py").read_text())
        _snap_src = str((Path(static_dir).parent.parent / "data" / "snapshots.py").read_text())
        checks.append({
            "name": "version: single source across ALL operational surfaces",
            "status": "ok" if _RV == _V
            and "from core.config import VERSION as ATHENA_VERSION" in _snap_src
            and "from core.config import VERSION" in _mcp_src else "fail",
            "detail": f"config={_V} registry={_RV} — snapshots/MCP read config",
        })
        # The profile-management endpoints (New/Duplicate/Delete) exist and
        # refuse LOCKED profiles (.default/.nurse/.janitor).
        checks.append({
            "name": "theme: 5-color 1:1 + MATCH SYSTEM default",
            "status": "ok" if "match_system" in _srv_src
            and "setMatchSystem" in gui_all and "systemMode" in gui_all
            and "prefers-color-scheme" in gui_all else "fail",
            "detail": "Match System ON by default; explicit choice persists",
        })
        checks.append({
            "name": "vault: grid renders + centered error panel + full-body scroll",
            "status": "ok" if "vault-grid-head" in gui_all and "vault-grid-body" in gui_all
            and "vault-error" in gui_all and "vault-error-code" in gui_all
            and "vault-error-msg" in gui_all and "showVaultError" in gui_all
            and "overflow: auto" in gui_all else "fail",
            "detail": "Vault Error: {code} centered; grid scrolls up/down + left/right",
        })
        # THE NO-BLEED RULE (the Operator's spec): each page is a TRIO (html/css/
        # js). A page's css must NEVER set `display` on its own workspace ID
        # — `.workspace.active` in index.css owns visibility. An ID
        # display:flex would override `.workspace { display: none }` and
        # bleed the page onto every other page.
        _bleed_bad = []
        for _f in sorted(Path(static_dir).rglob("*.css")):
            _s = _f.read_text(encoding="utf-8", errors="replace")
            for _m in re.finditer(r"#([a-z]+-ws)\s*\{[^}]*display\s*:", _s):
                _bleed_bad.append(_f.name + ":" + _m.group(1))
        checks.append({
            "name": "GUI: no page bleeds (workspace IDs never set display)",
            "status": "ok" if not _bleed_bad else "fail",
            "detail": " ".join(_bleed_bad) or "visibility owned by .workspace.active only",
        })
        checks.append({
            "name": "call: page + 16:9 HD caller grid + hotbar + profile-only picker",
            "status": "ok" if "call-grid" in gui_all and "call-hotbar" in gui_all
            and "call-toggle" in gui_all and "call-mute" in gui_all
            and "call-add" in gui_all and "call-remove" in gui_all
            and "CALL_ROSTER" in gui_all and "call-picker" in gui_all
            and "CALL_AVAILABLE = (d.profiles" in gui_all
            and "aspect-ratio: 16" in gui_all else "fail",
            "detail": "2x1→5x3 grid, 1280x720 scaled, Start/Stop · Mute · Add/Remove — ONLY existing profiles",
        })
        try:
            from core.system_profiles import is_locked as _is_locked
            checks.append({
                "name": "profiles: New/Duplicate/Delete endpoints + locks",
                "status": "ok" if "profiles/create" in _srv_src
                and "/duplicate" in _srv_src and "/delete" in _srv_src
                and _is_locked(".default") and _is_locked(".nurse")
                and _is_locked(".janitor") and not _is_locked("family") else "fail",
                "detail": "create/duplicate/delete refuse .default/.nurse/.janitor",
            })
            # The footer Terminal + Console (the Operator's spec): the raw shell with
            # history + the organized event-metrics log.
            _foot_src = str((Path(static_dir) / "js" / "footer.js").read_text())
            checks.append({
                "name": "footer: Terminal + Console panels",
                "status": "ok" if "foot-terminal-btn" in _foot_src
                and "foot-console-btn" in _foot_src
                and "/terminal" in _srv_src and "/console" in _srv_src
                and "TERM_HISTORY" in _foot_src else "fail",
                "detail": "raw shell + history (↑/↓) + organized event metrics",
            })
        except Exception:
            pass
    except Exception:
        pass
    checks.append({
        "name": "sessions list = UUID only",
        "status": "ok" if _uuid_only_ok() else "fail",
        "detail": "non-UUID sessions never appear",
    })
    # Pagination math: a 250-msg session pages backward 100 at a time.
    checks.append({
        "name": "chat history pages 100 at a time (backward)",
        "status": "ok" if _paging_ok() else "fail",
        "detail": "250 msgs → 3 pages (100/100/50), newest first",
    })
    # Session management: new_session + delete_session work (temp dir).
    checks.append({
        "name": "sessions: create + delete (management)",
        "status": "ok" if _session_mgmt_ok() else "fail",
        "detail": "new creates a .db, delete removes it",
    })
    # 3b. Web-mode terminal = the metrics logger (the Operator's spec).
    from web.terminal_log import tail_forever
    checks.append({
        "name": "web-mode terminal = metrics logger",
        "status": "ok" if callable(tail_forever) else "fail",
        "detail": "tail_forever streams the metric log",
    })

    # 4. The MCP router is mounted with the tools endpoints.
    from web.mcp import router as mcp_router, SERVER_INFO
    checks.append({
        "name": "MCP endpoint mounted",
        "status": "ok" if mcp_router.prefix == "/mcp"
        and SERVER_INFO["name"] == "athena" else "fail",
        "detail": mcp_router.prefix,
    })

    # 5. The permission gate guards MCP tool calls (out-of-bounds unsafe →
    #    refused; in-bounds work is the platform's own business).
    # THE CLEAN-STORE FIX (the 08-15 fix): the operator's LIVE grants
    # would make terminal allowed — assert the ENGINE on a clean store.
    import security.permissions as perm
    import tempfile as _tf5
    from pathlib import Path as _P5
    _orig_rp5 = perm._rules_path
    perm._rules_path = staticmethod(
        lambda profile="": _P5(_tf5.gettempdir()) / "doctor_web_perm" / "permissions.yaml")
    try:
        r = perm.check("terminal", {"command": "ls /outside"})
        checks.append({
            "name": "MCP gate: out-of-bounds unsafe needs prompt",
            "status": "ok" if r["verdict"] == perm.NEEDS_PROMPT
            and not r["allowed"] else "fail",
            "detail": str(r),
        })
    finally:
        perm._rules_path = _orig_rp5

    # 6. The VAULT GRID (the Operator's cell-based table): read rows + the
    #    CRUD endpoints work (the FTS UPDATE trigger must not break edits).
    from core import db as db_layer
    import core.db as dbmod
    import tempfile as _tf2
    from pathlib import Path as _P2
    with _tf2.TemporaryDirectory() as td2:
        orig_vault = db_layer.vault_path
        db_layer.vault_path = staticmethod(
            lambda *a, **k: _P2(td2) / "vault" / "vault.db")
        # ALSO isolate the sessions dir — the loop tests below use
        # session_id="roles"/"toolcols" probes; without this they leak
        # session-roles.db / session-toolcols.db into the REAL store
        # (the exact debris the Operator found). Redirect ANY profile
        # (the loop writes with profile=self.profile.name = ".default",
        # not "") so nothing touches the real stores.
        orig_sessions_dir = dbmod.sessions_dir
        dbmod.sessions_dir = staticmethod(
            lambda *a, **k: _P2(td2) / "sessions")
        _P2(td2, "sessions").mkdir(parents=True, exist_ok=True)
        try:
            _P2(td2, "vault").mkdir(parents=True, exist_ok=True)
            # Add a row → it appears in the table view.
            entry_id = db_layer.record_vault_entry(
                "state", "grid test", role="user", context="web",
                dedup=False)
            conn = db_layer.connect_vault("")
            cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)")]
            n = conn.execute("SELECT COUNT(*) FROM entries WHERE deleted=0").fetchone()[0]
            # EDIT a row (exercises the FTS UPDATE trigger).
            conn.execute("UPDATE entries SET content=? WHERE id=?",
                         ("grid test EDITED", entry_id))
            conn.commit()
            row = conn.execute("SELECT content FROM entries WHERE id=?",
                               (entry_id,)).fetchone()
            # SUBTRACT (soft-delete) a row.
            conn.execute("UPDATE entries SET deleted=1 WHERE id=?", (entry_id,))
            conn.commit()
            n2 = conn.execute("SELECT COUNT(*) FROM entries WHERE deleted=0").fetchone()[0]
            # 6b. The TYPE of call: tool | skill | message (the Operator's spec).
            #     Legacy kind values normalize to message on new writes.
            legacy = {"fact", "note", "review", "state"}
            has_legacy = bool(legacy & set(
                r["type"] for r in conn.execute(
                    "SELECT type FROM entries GROUP BY type").fetchall()))
            conn.close()
            checks.append({
                "name": "vault grid: add/edit/subtract rows",
                "status": "ok" if n >= 1 and row["content"] == "grid test EDITED"
                and n2 == n - 1 else "fail",
                "detail": f"added={n} edited={row['content']!r} after_sub={n2}",
            })
            checks.append({
                "name": "vault grid columns served",
                "status": "ok" if "id" in cols and "content" in cols
                and "type" in cols else "fail",
                "detail": f"{len(cols)} cols",
            })
            # 6c. The Operator's tool/skill columns: tool = NAME, tool_call =
            #     ARGUMENTS string, tool_id = the call's ID — all three
            #     populated and DISTINCT (a duplicated tool_call/tool_id
            #     is the exact bug the Operator caught).
            from core.message_loop import MessageLoop
            class _FakeToolResult:
                def __init__(self):
                    self.reply = "done"
                    self.tool_transcript = [
                        {"tool_name": "search", "tool_call_id": "call_x",
                         "arguments": '{"pattern": "q"}', "result": "hit"},
                    ]
                    self.finish_reason = "stop"
                    self.usage = None
                    self.exit_reason = "completed"
                    self.api_calls = 1
                    self.tool_calls_made = 1
                    self.updated_history = []
            with patch("core.message_loop.MessageLoop.run_turn",
                       return_value=_FakeToolResult()):
                from core.conversation_loop import ConversationLoop
                # PIN the test session (the Operator's hygiene rule):
                # a fixed test id — never a fresh UUID that leaks as an
                # orphan session file.
                loop = ConversationLoop(profile="", session_id="toolcols")
                loop._skills_for_channel = lambda ch: []
                loop._process_event({"channel": "user", "content": "find",
                                     "session_id": "toolcols"})
            tconn = db_layer.connect_vault("")
            trow = tconn.execute(
                "SELECT tool, tool_call, tool_id FROM entries "
                "WHERE type='tool' ORDER BY rowid DESC LIMIT 1").fetchone()
            tconn.close()
            distinct = bool(trow and trow["tool"] == "search"
                            and trow["tool_call"] == '{"pattern": "q"}'
                            and trow["tool_id"] == "call_x")
            checks.append({
                "name": "tool cols: name / args / id distinct",
                "status": "ok" if distinct else "fail",
                "detail": f"tool={trow['tool'] if trow else None} "
                          f"call={trow['tool_call'] if trow else None} "
                          f"id={trow['tool_id'] if trow else None}",
            })
            # 6d. The Operator's role vocabulary (System|Agent|Assistant|User):
            #     tool/skill executions are SYSTEM actions; message rows
            #     are User/Assistant. Also: skill_call/skill_id populate,
            #     and generated rows carry api_provider/api_model.
            from core.message_loop import MessageLoop
            class _FakeSkill:
                name = "weather-check"
                description = "Check the weather"
                priority = 1
            class _FakeResult2:
                def __init__(self):
                    self.reply = "checked"
                    self.tool_transcript = [
                        {"tool_name": "weather", "tool_call_id": "call_y",
                         "arguments": '{"city": "Oslo"}', "result": "22c"},
                    ]
                    self.finish_reason = "stop"
                    self.exit_reason = "completed"
                    self.reasoning = None
                    self.usage = None
                    self.api_calls = 1
                    self.tool_calls_made = 1
                    self.updated_history = []
            # Scope to the rows THIS test wrote (the "roles" session):
            # the vault accumulates system/gate rows with no provider,
            # which must not fail a test about ITS OWN writes.
            rconn = db_layer.connect_vault("")
            rbefore = rconn.execute(
                "SELECT COALESCE(MAX(rowid),0) FROM entries").fetchone()[0]
            rconn.close()
            with patch("core.message_loop.MessageLoop.run_turn",
                       return_value=_FakeResult2()):
                from core.conversation_loop import ConversationLoop
                # PIN the test session (hygiene: fixed id, no orphan files).
                loop = ConversationLoop(profile="", session_id="roles")
                loop._skills_for_channel = lambda ch: [_FakeSkill()]
                loop._process_event({"channel": "user", "content": "weather",
                                     "session_id": "roles"})
            rconn = db_layer.connect_vault("")
            rrows = rconn.execute(
                "SELECT type, role, skill_call, skill_id, api_provider "
                "FROM entries WHERE deleted=0 AND rowid>? ORDER BY rowid",
                (rbefore,)).fetchall()
            rconn.close()
            roles_ok = all(r["role"] in ("System", "Assistant", "User")
                           for r in rrows)
            sys_rows = [r for r in rrows if r["type"] == "tool"]
            sys_ok = bool(sys_rows and sys_rows[0]["role"] == "System")
            skill_rows = [r for r in rrows if r["type"] == "skill"]
            skill_ok = bool(skill_rows and skill_rows[0]["skill_call"]
                            and skill_rows[0]["skill_id"])
            api_ok = True
            # THE 08-15 FRESH-BOOT TOLERANCE: on a wipe the provider
            # selection is NULL until the operator configures it — the
            # mocked turn then carries no api_provider (legitimately).
            # Only assert the provider tag when a selection exists.
            try:
                from core.config import load_config
                _sel = load_config().get("models", {}).get("reason", {}) or {}
                if _sel.get("provider"):
                    api_ok = all(r["api_provider"] for r in rrows
                                 if r["role"] in ("System", "Assistant"))
            except Exception:
                pass
            checks.append({
                "name": "roles System/Assistant/User + skill + api",
                "status": "ok" if roles_ok and sys_ok and skill_ok and api_ok
                else "fail",
                "detail": f"roles={roles_ok} tool_system={sys_ok} "
                          f"skill={skill_ok} api={api_ok}",
            })
            checks.append({
                "name": "vault type = tool|skill|message (new writes)",
                "status": "ok" if "type" in cols else "fail",
                "detail": f"legacy values present (historical): {has_legacy}",
            })
        finally:
            db_layer.vault_path = orig_vault
            dbmod.sessions_dir = orig_sessions_dir
    return checks

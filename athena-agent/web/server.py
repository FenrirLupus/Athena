"""The Athena HTTP server (FastAPI) — the web door (Workstream 2).

The server loop + message loop live BEHIND this HTTP layer. Everything
enters the FIFO queue and is handled in arrival order (oldest → newest,
no races) — the Operator's ordering guarantee.

Endpoints:
    GET  /              → the GUI (chat workspace first on the navbar)
    GET  /health        → server loop status (gates, scheduler, nurse)
    POST /chat          → enqueue a message → the FIFO worker replies
    GET  /sessions      → session list
    GET  /sessions/{id} → session history (JSONL shape)
    GET  /vault?query=  → retrieval ladder over HTTP
    GET  /logs          → metric log reader
    GET  /tools         → registered tools (for MCP + GUI)
    WS   /ws            → live events (the flow markers over the wire)

The MCP endpoint lives in mcp.py and mounts at /mcp.
"""
from __future__ import annotations

import json
import threading
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


def parse_frontmatter(path: Path) -> dict:
    """Parse the --- frontmatter of an identity file. Best effort."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _athena_version() -> str:
    """The single-source version (core.config.VERSION) for the API."""
    try:
        from core.config import VERSION
        return VERSION
    except Exception:
        return "0.1.0"

from web.fifo_queue import FIFOQueue

STATIC_DIR = None  # set by create_app (avoids import-time path issues)


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    profile: Optional[str] = None


class ChatResponse(BaseModel):
    ok: bool
    reply: str = ""
    session_id: str = ""
    error: str = ""
    flow: list = []


def create_app(*, loop_holder=None, server_holder=None,
               static_dir: str = "") -> FastAPI:
    """Build the FastAPI app.

    loop_holder: an object with a .loop (ConversationLoop) — created
        lazily/once and shared; or a callable returning the loop.
    server_holder: an object with a .server (ServerLoop) for status.
    """
    app = FastAPI(title="Athena", version=_athena_version())

    # The MCP endpoint (third-party apps) — mounted at /mcp.
    from web.mcp import router as mcp_router
    app.include_router(mcp_router)

    # The GUI static files — the MODULAR web GUI (the Operator's spec):
    # web/gui/ holds html/ css/ js/ subfolders. /gui serves them from
    # the in-memory cache; / composes the page from the html partials.

    # THE IN-MEMORY GUI CACHE (the Operator's spec): the server loads the
    # ENTIRE website into memory at boot. Idle parts are FREED from
    # memory (an LRU cap) and re-read from disk on the next access —
    # so unused modules never sit resident, and hot ones never touch
    # the disk twice. Each entry carries its file MTIME: on access the
    # file is stat'ed (cheap) and re-read only if it changed — so edits
    # to a css/js/html file are served fresh without a restart.
    import threading as _threading
    import time as _time
    gui_cache = {}      # rel -> (mtime_ns, content)
    gui_cache_lock = _threading.Lock()
    GUI_CACHE_MAX = 64  # the resident-file ceiling (LRU evicts beyond)

    def _gui_read(rel: str) -> str | None:
        """Read a gui file, cached in memory with LRU eviction + mtime
        validation (an edited file is re-read on the next access)."""
        from pathlib import Path
        p = Path(static_dir) / rel
        try:
            mtime = p.stat().st_mtime_ns
        except OSError:
            return None
        with gui_cache_lock:
            entry = gui_cache.get(rel)
            if entry is not None and entry[0] == mtime:
                gui_cache[rel] = gui_cache.pop(rel)  # LRU touch
                return entry[1]
        # Miss or stale → read from disk.
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            return None
        with gui_cache_lock:
            gui_cache[rel] = (mtime, text)
            while len(gui_cache) > GUI_CACHE_MAX:
                # Evict the least-recently-touched (the first key).
                gui_cache.pop(next(iter(gui_cache)), None)
        return text

    @app.get("/gui/{rel:path}")
    def gui_file(rel: str):
        """Serve a gui file from the IN-MEMORY cache (freed when idle)."""
        if rel == "__cache_clear__":
            # the Operator's spec: the service lifecycle (start/stop/restart)
            # frees the cache so the website ALWAYS shows the files on
            # disk — no stale css/js/html can ever be served.
            with gui_cache_lock:
                n = len(gui_cache)
                gui_cache.clear()
            return JSONResponse({"ok": True, "cleared": n})
        content = _gui_read(rel)
        if content is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        ctype = "text/plain"
        if rel.endswith(".css"):
            ctype = "text/css"
        elif rel.endswith(".js"):
            ctype = "application/javascript"
        elif rel.endswith(".html"):
            ctype = "text/html"
        # NO-CACHE (the Operator's 08-12 fix): the GUI cache already
        # re-reads edited files by mtime; the BROWSER must never serve a
        # stale js/css/html from its own cache — a cached old boot.js is
        # exactly how the startup gate "stayed stuck" after a fix. Always
        # revalidate from the server.
        return Response(content=content, media_type=ctype,
                        headers={"Cache-Control": "no-cache"})

    # The workspace partials the / endpoint composes (in order).
    GUI_PARTIALS = ["nav", "approval", "chat", "call", "home", "sessions",
                    "vault", "behavior", "usage", "settings", "footer",
                    "cell-editor"]

    state = {"loop": None, "server": None, "holder": loop_holder,
             "server_holder": server_holder}

    def get_loop():
        if state["loop"] is None and state["holder"] is not None:
            h = state["holder"]
            state["loop"] = h.loop if hasattr(h, "loop") else h()
        return state["loop"]

    def get_server():
        if state["server"] is None and state["server_holder"] is not None:
            h = state["server_holder"]
            state["server"] = h.server if hasattr(h, "server") else h()
        return state["server"]

    # -- The FIFO queue: every /chat enters, handled oldest → newest. ---
    def _chat_worker(req):
        try:
            loop = get_loop()
            if loop is None:
                return {"ok": False, "error": "no conversation loop",
                        "session_id": ""}
            payload = req.payload
            session_id = payload.get("session_id") or loop.session_id
            # THE STREAM OBSERVER (the Operator's 08-12 queue spec): for
            # chat_stream requests, the worker attaches the live observer
            # to the loop so on_event entries push into the request's
            # event sink AS the turn runs — the SSE generator reads them
            # in order. The queue is single-lane, so no two turns share
            # the observer at once.
            sink = getattr(req, "event_sink", None)
            old_ev = getattr(loop, "on_event", None)
            if sink is not None:
                def _push(kind, detail, extra=""):
                    sink.put((kind, detail, extra))
                loop.on_event = _push
            try:
                # The same flow the CLI uses: enqueue → drain → read
                # responses.
                # THE CHANNEL FIX (the Operator's 08-12 chat-readiness bug):
                # the OPERATOR's chat is the USER channel — NOT "system".
                # The system channel's instructions ("You are performing a
                # system operation. Report clearly...") made the model terse
                # ("hello athena" → "ok"). The user channel carries the
                # conversational seed + the widened read/explore toolset.
                ack = loop.handle_event({
                    "session_id": session_id,
                    "content": payload.get("message", ""),
                    "channel": "user",   # the operator's chat — conversational
                })
                loop.drain()
                reply = ""
                flow = []
                for response in loop.responses:
                    if response.get("event_id") == ack.get("event_id"):
                        reply = response.get("reply", "")
                        flow = response.get("flow") or []
                        break
                return {"ok": True, "reply": reply, "session_id": session_id,
                        "flow": flow}
            finally:
                loop.on_event = old_ev
        except Exception as exc:
            from core.logging import log_event
            log_event(4, f"web chat worker error: {exc}",
                      source="platform", action="web_chat")
            return {"ok": False, "error": str(exc), "session_id": ""}

    queue_holder = {"queue": None, "lanes": {}}

    def _lane_name() -> str:
        """The lane for the ACTIVE profile (the 08-15 isolation spec):
        each profile owns its own queue lane. The active profile (or the
        default when none is active) is the lane the operator's chat
        uses; the nurse/janitor/named profiles get their own lanes when
        they work — so one profile's provider calls never stall another.
        """
        try:
            from intelligence.profiles import current_profile
            p = getattr(current_profile(), "name", "") or ""
            if p:
                return p
        except Exception:
            pass
        return "default"

    def get_queue(profile: str = "") -> FIFOQueue:
        """The lane for a profile — one FIFOQueue per profile, each with
        its OWN dynamic worker pool (the 08-15 parallel + isolation
        spec). profile="" routes to the ACTIVE profile's lane."""
        name = profile or _lane_name()
        q = queue_holder["lanes"].get(name)
        if q is None:
            q = FIFOQueue(_chat_worker)
            q.start()
            queue_holder["lanes"][name] = q
            if queue_holder["queue"] is None:
                queue_holder["queue"] = q
        return q

    def get_lane_stats() -> dict:
        return {name: q.stats() for name, q in
                queue_holder["lanes"].items()}

    @app.on_event("shutdown")
    def _shutdown():
        for q in queue_holder["lanes"].values():
            q.stop()
        queue_holder["lanes"].clear()

    # -- The GUI -------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index():
        """Compose the modular GUI: the html partials inlined into the
        base index.html at their <!--@name--> markers. All reads go
        through the IN-MEMORY cache (the Operator's spec)."""
        base = _gui_read("html/index.html")
        if base is None:
            return "<h1>Athena</h1><p>GUI index.html not found.</p>"
        page = base
        for name in GUI_PARTIALS:
            part = _gui_read(f"html/{name}.html")
            if part is not None:
                page = page.replace(f"<!--@{name}-->", part)
        return page

    # -- The VERSION (single source: core.config.VERSION) ----------------
    @app.get("/version")
    def version():
        return {"version": _athena_version()}

    # -- Health --------------------------------------------------------
    @app.get("/health")
    def health():
        server = get_server()
        status = {}
        if server is not None:
            try:
                status = server.status() if callable(getattr(server, "status", None)) else {}
            except Exception:
                status = {}
        # TOKEN USAGE (the footer's meter): tokens used SINCE the last
        # compression (the established model — compress at 80%, meter resets
        # toward 0) vs the iteration budget's allowance.
        from core.config import load_config
        _cfg = load_config()
        _budget = _cfg.get("iteration_budget", {}) or {}
        available = int(_budget.get("main_max_tokens", 5120) or 5120) * \
                    int(_budget.get("main_iterations", 100) or 100)
        used = 0
        try:
            from context.compression import usage_since_baseline
            used = usage_since_baseline("")
        except Exception:
            used = 0
        pct = (used / available * 100.0) if available else 0.0
        # THE LOADED COUNTS (the Operator's 08-12 footer scheme): exactly
        # what has loaded on startup — plugins, tools, skills — so the
        # footer shows quick numbers without digging anywhere.
        counts = {"plugins": 0, "tools": 0, "skills": 0}
        try:
            from intelligence.plugins import discover_plugins
            counts["plugins"] = len(discover_plugins())
        except Exception:
            pass
        try:
            from core.builtin_tools import register_builtin_tools
            counts["tools"] = len(register_builtin_tools())
        except Exception:
            pass
        try:
            from intelligence.skills import load_skills
            counts["skills"] = len(load_skills())
        except Exception:
            pass
        return {"ok": True, "server": status, "queue": get_queue().stats(),
                "tokens": {"used": used, "available": available,
                           "percent": round(pct, 2)},
                "loaded": counts}

    # -- READY (the Operator's 08-12 startup-gate spec): the page stays
    #    blocked until the startup systems are FULLY done. /ready reports
    #    the boot lifecycle + the THREE LAYERS (server → mcp → runtime —
    #    the biggest to the smallest). The GUI polls it every 1s and
    #    unblocks when ready=true.
    @app.get("/ready")
    def ready():
        try:
            from core import readiness
            st = readiness.get_state("boot")
            state = (st or {}).get("state", "starting")
            detail = (st or {}).get("detail", "")
            layers = {}
            for name in ("server", "mcp", "runtime"):
                ls = readiness.get_state(name) or {}
                layers[name] = {
                    "state": ls.get("state", "starting"),
                    "detail": ls.get("detail", ""),
                }
            return {"ready": state == "ready",
                    "state": state, "detail": detail,
                    "layers": layers}
        except Exception as exc:
            return {"ready": False, "state": "starting",
                    "detail": str(exc), "layers": {}}

    # -- Chat (FIFO) ---------------------------------------------------
    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest):
        import time
        q = get_queue()
        req_id = q.submit("chat", req.model_dump())
        # Poll the result (the queue is FIFO — this request's turn comes
        # in arrival order, after everything already queued).
        deadline = time.time() + 120.0
        while time.time() < deadline:
            r = q.last_result(req_id)
            if r is not None:
                return ChatResponse(**r)
            time.sleep(0.1)
        return ChatResponse(ok=False, error="timeout", session_id="")

    # -- Chat INTERRUPT (the Operator's 08-12 spec): a NEW message sent
    # -- while Athena is thinking cuts the running turn. The GUI calls
    # -- this BEFORE submitting the new message — the flag makes the
    # -- MessageLoop stop at its next check, then the new message queues
    # -- and processes.
    @app.get("/queue/stats")
    def queue_stats():
        """The lane stats (the 08-15 parallel queue): per-profile lanes,
        each with its dynamic worker pool + the reorder buffer."""
        return {"lanes": get_lane_stats()}

    @app.post("/chat/interrupt")
    def chat_interrupt():
        loop = get_loop()
        if loop is None:
            return {"ok": False}
        try:
            loop._interrupt.set()
        except Exception:
            return {"ok": False}
        return {"ok": True}

    # -- SYSTEM COMMANDS (the Operator's 08-15 spec): the operator can
    # -- STOP the agent mid-turn and RESTART/REFRESH the server runtime
    # -- from the chat — no shell needed.
    @app.post("/system/restart")
    def system_restart():
        """Soft restart the runtime (the lifecycle restart) — a refresh
        of the agent's world without killing the web server."""
        try:
            from autonomy.lifecycle import restart
            _msg = restart()
            from core.logging import log_event
            log_event(2, f"system restart: {_msg[:120]}",
                      source="server", action="system_restart")
            return {"ok": True, "detail": _msg}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    @app.post("/system/refresh")
    def system_refresh():
        """Soft refresh: reload commands/plugins/skills — no restart."""
        try:
            from autonomy.lifecycle import refresh
            _msg = refresh()
            return {"ok": True, "detail": _msg}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    # -- Chat STREAM (the Operator's 08-12 spec): the
    # -- GUI receives the turn LIVE — every system/tool/skill call as it
    # -- fires (SSE), then the final reply. The thinking block streams
    # -- instead of arriving as one big block. The request RIDES THE FIFO
    # -- QUEUE (one lane, strict order — the queue handles the workload;
    # -- the worker pushes live events into the sink).
    @app.post("/chat/stream")
    async def chat_stream(request: Request):
        from fastapi.responses import StreamingResponse
        import asyncio
        import queue as _q
        body = await request.json() or {}
        text = str((body or {}).get("message", "")).strip()
        loop = get_loop()
        if loop is None:
            return JSONResponse({"ok": False, "error": "no loop"},
                                status_code=503)
        session_id = str((body or {}).get("session_id") or loop.session_id)
        q = get_queue()
        evq: "_q.Queue" = _q.Queue()
        # Enter the FIFO lane — the queue processes requests in arrival
        # order; this stream waits its turn like every other chat.
        req_id = q.submit("chat", {"message": text,
                                   "session_id": session_id},
                          event_sink=evq)

        async def gen():
            import json as _json
            # Stream live call events from the sink (they fire while the
            # worker runs the turn in FIFO order).
            done = False
            reply = ""
            flow = []
            deadline = 0
            while not done:
                try:
                    kind, a, b = evq.get_nowait()
                    if kind == "delta":
                        # A reply-token delta — the GUI types it live.
                        payload = _json.dumps({"type": "delta", "text": a})
                        yield "data: " + payload + "\n\n"
                        continue
                    if kind == "reason":
                        # A reasoning-token delta (the model's thinking
                        # chain) — the GUI shows it live in the block.
                        payload = _json.dumps({"type": "reason", "text": a})
                        yield "data: " + payload + "\n\n"
                        continue
                    if kind == "call":
                        payload = _json.dumps({"type": "call", "kind": a,
                                               "detail": b, "extra": ""})
                        yield "data: " + payload + "\n\n"
                        continue
                    if kind == "state":
                        # THE WORKING-STATE EVENT (the 08-15 fix): the loop
                        # emits "working, iteration N" at each iteration
                        # start — the GUI uses it to detect a CONTINUATION
                        # (a new response after a previous one) and opens a
                        # new turn wrapper. Forward it live.
                        payload = _json.dumps({"type": "state", "detail": a,
                                               "extra": b})
                        yield "data: " + payload + "\n\n"
                        continue
                    if kind in ("tool", "tool.result", "skill", "system"):
                        # A tool/skill call or its completion: detail =
                        # "name args", extra = the result preview.
                        payload = _json.dumps({"type": "call", "kind": kind,
                                               "detail": a, "extra": b})
                        yield "data: " + payload + "\n\n"
                        continue
                except _q.Empty:
                    pass
                # The request is done when the queue has its result.
                r = q.last_result(req_id)
                if r is not None:
                    done = True
                    reply = r.get("reply", "")
                    flow = r.get("flow") or []
                    break
                deadline += 1
                if deadline > 600:  # 60s safety
                    done = True
                    break
                await asyncio.sleep(0.1)
            # Drain any final call events that arrived with the result.
            while True:
                try:
                    kind, a, b = evq.get_nowait()
                    if kind == "delta":
                        payload = _json.dumps({"type": "delta", "text": a})
                        yield "data: " + payload + "\n\n"
                        continue
                    if kind == "reason":
                        payload = _json.dumps({"type": "reason", "text": a})
                        yield "data: " + payload + "\n\n"
                        continue
                    if kind in ("tool", "tool.result", "skill", "system",
                                "call"):
                        payload = _json.dumps({"type": "call", "kind": kind,
                                               "detail": a, "extra": b})
                        yield "data: " + payload + "\n\n"
                except _q.Empty:
                    break
            payload_flow = _json.dumps({"type": "flow", "flow": flow})
            yield "data: " + payload_flow + "\n\n"
            payload_reply = _json.dumps({"type": "reply", "reply": reply,
                                         "session_id": session_id})
            yield "data: " + payload_reply + "\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    # -- Attachments (the Operator's 08-12 chat spec): the operator can
    # -- attach FILES to a chat message. Each file is:
    # --   1. copied into the active profile's documents/<type>/ folder,
    # --      organized by file type (images/, docs/, audio/, video/,
    # --      archives/, data/, other/) — the agent's organized document
    # --      store;
    # --   2. recorded in the session as a user message so it renders in
    # --      the chat history (the attachment reference).
    # -- The GUI's Attachments button posts multipart here.
    @app.post("/chat/attach")
    async def chat_attach(request: Request):
        import shutil as _shutil
        from pathlib import Path as _P
        from core.config import ATHENA_ROOT
        form = await request.form()
        up = form.get("file")
        if up is None:
            return {"ok": False, "error": "no file in request"}
        try:
            raw = await up.read()
        except Exception:
            raw = getattr(up, "file", None).read() if getattr(up, "file", None) else b""
        name = str(getattr(up, "filename", "") or "attachment")
        # The file-type folder (the Operator's spec: organized by type).
        ext = _P(name).suffix.lower().lstrip(".")
        type_map = {
            **{e: "images" for e in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico")},
            **{e: "docs" for e in ("pdf", "txt", "md", "doc", "docx", "odt", "rtf", "csv", "xls", "xlsx", "ppt", "pptx")},
            **{e: "audio" for e in ("mp3", "wav", "ogg", "flac", "m4a", "aac")},
            **{e: "video" for e in ("mp4", "mkv", "webm", "mov", "avi")},
            **{e: "archives" for e in ("zip", "tar", "gz", "bz2", "xz", "7z", "rar")},
            **{e: "data" for e in ("json", "yaml", "yml", "toml", "xml", "db", "sqlite", "sql")},
        }
        ftype = type_map.get(ext, "other")
        # The active profile's documents/ (the agent's organized store).
        prof = str(form.get("profile") or ".default")
        if prof == "default":
            prof = ".default"
        docs = ATHENA_ROOT / "profiles" / prof / "documents" / ftype
        docs.mkdir(parents=True, exist_ok=True)
        # Keep the original name (sanitized); a duplicate gets a suffix.
        safe = _P(name).name.replace("/", "_").replace("..", "_")
        dest = docs / safe
        n = 2
        while dest.exists():
            dest = docs / f"{_P(safe).stem}-{n}{_P(safe).suffix}"
            n += 1
        dest.write_bytes(raw or b"")
        # Record the attachment in the session (renders in chat history).
        loop = get_loop()
        sid = str(form.get("session_id") or "")
        if not sid and loop is not None:
            sid = getattr(loop, "session_id", "") or ""
        if not sid:
            from core import db as db_layer
            sid = db_layer.find_last_session(profile="") or ""
        attach_line = f"[📎 attachment: {name} → documents/{ftype}/{dest.name}]"
        if loop is not None:
            try:
                loop._process_event({
                    "channel": "user",
                    "content": attach_line,
                    "session_id": sid,
                })
            except Exception:
                pass
        return {"ok": True, "filename": name, "type": ftype,
                "path": str(dest), "session_id": sid, "line": attach_line}

    # -- Gateway routing (the Operator's server-as-parent spec) --------------
    # The parent (this server) routes a platform message to the RIGHT
    # profile runtime: the embedded default (Athena) handles it directly;
    # a child runtime gets the event POSTed to its loopback door.
    @app.post("/chat/profile/{name}")
    async def chat_profile(name: str, request: Request):
        from fastapi.responses import JSONResponse as _JR
        body = await request.json()
        name = name.strip().lower()
        if name in ("", "default", "athena"):
            # The embedded admin — the default loop handles it natively.
            q = get_queue()
            req_id = q.submit("chat", body)
            import time as _t
            deadline = _t.time() + 120.0
            while _t.time() < deadline:
                r = q.last_result(req_id)
                if r is not None:
                    return ChatResponse(**r)
                _t.sleep(0.1)
            return _JR({"ok": False, "error": "timeout"})
        # A CHILD runtime: forward to its loopback door.
        from core.loopback_door import post_event
        event = {
            "channel": "user",
            "content": str(body.get("message", body.get("content", ""))),
            "session_id": str(body.get("session_id", "") or ""),
        }
        ack = post_event(name, event)
        if not ack.get("ok"):
            return _JR({"ok": False, "error": ack.get("detail", "child down")})
        return _JR({"ok": True, "detail": f"delivered to {name}",
                    "ack": ack})

    # -- Chat history (the GUI loads the session's messages, PAGINATED) -
    # the Operator's spec: 100 messages at a time — the chat loads pages of
    # history from the session .db. offset=0 is the NEWEST page (the
    # bottom of the chat); higher offsets page further back (older).
    @app.get("/chat/history")
    def chat_history(offset: int = 0, limit: int = 100,
                     session_id: str = ""):
        from core import db as db_layer
        loop = get_loop()
        sid = session_id or ""
        if not sid and loop is not None:
            sid = getattr(loop, "session_id", "") or ""
        if not sid:
            sid = db_layer.find_last_session(profile="") or ""
        # THE 08-17 SESSION-OWNER RESOLUTION (the Operator's doctrine): a
        # session may belong to any profile — resolve ITS owner so history
        # loads from the right profile's store (never the loop's default).
        prof = getattr(getattr(loop, "profile", None), "name", "") if loop else ""
        owner = db_layer.find_session_profile(sid, start_profile=prof)
        if owner:
            prof = owner
        try:
            total = db_layer.count_session_messages(sid, profile=prof)
            # Page BACKWARD from the newest: get_session_history returns
            # the newest N messages ordered oldest→newest. For offset o,
            # the desired page is the messages [total-o-limit : total-o].
            # The fetched window (newest limit+offset) is
            # [total-(limit+offset) : total]; the page is the FIRST
            # `want` rows of that window (oldest-first, correct coverage:
            # page o covers [total-o-limit : total-o], disjoint + complete).
            rows = db_layer.get_session_history(
                sid, limit=limit + offset, profile=prof)
            want = min(limit, max(0, total - offset))
            page = rows[:want] if want else []
            msgs = []
            for r in page:
                meta = r.get("meta")
                flow = None
                if isinstance(meta, str):
                    try:
                        import json as _json
                        flow = (_json.loads(meta) or {}).get("flow")
                    except Exception:
                        flow = None
                elif isinstance(meta, dict):
                    flow = meta.get("flow")
                msgs.append({
                    "role": r.get("role", ""),
                    "content": r.get("content", ""),
                    "name": (r.get("name_nick") or r.get("name")
                             or ""),
                    "reason": r.get("reason") or None,
                    "flow": flow or None,
                })
            return {"session_id": sid, "messages": msgs,
                    "total": total, "offset": offset, "limit": limit,
                    "has_more": (offset + limit) < total}
        except Exception as exc:
            return {"session_id": sid, "messages": [], "error": str(exc),
                    "total": 0, "offset": offset, "limit": limit,
                    "has_more": False}

    # -- Sessions (the GUI's session dropdown: switch on the fly) ------
    @app.get("/sessions/current")
    def sessions_current():
        from core import db as db_layer
        loop = get_loop()
        sid = ""
        if loop is not None:
            sid = getattr(loop, "session_id", "") or ""
        if not sid:
            sid = db_layer.find_last_session(profile="") or ""
        profile = ""
        if loop is not None:
            profile = getattr(getattr(loop, "profile", None), "name", "") or ""
        # UUID-ONLY (the Operator's 08-12 strict-name rule) — the
        # session-follow tick + dropdown must never list test debris.
        sids = db_layer.uuid_session_ids(profile=profile)
        labels = db_layer.load_session_labels(profile=profile)
        return {"current": sid, "sessions": sids[-20:],
                "labels": labels, "profile": profile}

    # -- Session management (the Operator's sessions workspace: create/delete) -
    @app.post("/sessions/new")
    def sessions_new(profile: str = ""):
        from core import db as db_layer
        try:
            sid = db_layer.new_session(profile=profile)
            # Point the loop at the fresh session so chat continues there.
            loop = get_loop()
            if loop is not None:
                loop.session_id = sid
            return {"ok": True, "session_id": sid}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)},
                                status_code=400)

    @app.delete("/sessions/{sid}")
    def sessions_delete(sid: str, profile: str = ""):
        from core import db as db_layer
        try:
            ok = db_layer.delete_session(sid, profile=profile)
            return {"ok": ok, "session_id": sid}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)},
                                status_code=400)

    # -- Profiles ------------------------------------------------------
    @app.get("/profiles")
    def profiles_list():
        from intelligence.profiles import list_profiles, current_profile
        from core.system_profiles import is_locked
        cur = current_profile()
        profs = []
        for p in list_profiles():
            profs.append({
                "name": p.name,
                "is_default": p.is_default,
                "locked": is_locked(p.name),
            })
        return {"current": cur.name, "profiles": profs}

    @app.get("/profiles/{name}")
    def profiles_get(name: str):
        """A profile's editable identity settings (frontmatter)."""
        from intelligence.profiles import get_profile
        from core.system_profiles import is_locked
        p = get_profile(name)
        if p is None:
            return JSONResponse({"ok": False, "error": f"profile not found: {name}"},
                                status_code=404)
        ai, ui = {}, {}
        try:
            ai = parse_frontmatter(p.assistant_identity)
        except Exception:
            pass
        try:
            ui = parse_frontmatter(p.user_identity)
        except Exception:
            pass
        return {
            "name": p.name,
            "locked": is_locked(p.name),
            "identity": {
                "agent_first": ai.get("name_first", ""),
                "agent_last": ai.get("name_last", ""),
                "agent_nick": ai.get("name_nick", ""),
                "role": ai.get("role", ""),
                "operator_first": ui.get("name_first", ""),
                "operator_last": ui.get("name_last", ""),
            },
        }

    @app.post("/profiles/{name}/identity")
    async def profiles_identity_set(name: str, request: Request):
        """Write a profile's identity frontmatter. Locked profiles refuse."""
        from intelligence.profiles import get_profile
        from core.system_profiles import is_locked
        if is_locked(name):
            return JSONResponse({"ok": False, "error": "locked profile — no modifications allowed"},
                                status_code=403)
        p = get_profile(name)
        if p is None:
            return JSONResponse({"ok": False, "error": f"profile not found: {name}"},
                                status_code=404)
        body = await request.json()
        ident = (body or {}).get("identity", {}) or {}

        def rewrite(path, mapping, agent=True):
            """Update the frontmatter keys of an identity file.

            Bootstraps the file when missing (a brand-new profile has
            empty dirs until its first identity save).
            """
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("---\n---\n\n", encoding="utf-8")
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                return
            if not text.startswith("---"):
                path.write_text("---\n---\n\n" + text, encoding="utf-8")
                text = path.read_text(encoding="utf-8")
            end = text.find("\n---", 3)
            if end < 0:
                return
            head = text[3:end]
            lines = head.splitlines()
            for k, v in mapping.items():
                found = False
                for i, line in enumerate(lines):
                    if line.split(":", 1)[0].strip() == k:
                        lines[i] = f'{k}: "{v}"'
                        found = True
                        break
                if not found:
                    lines.append(f'{k}: "{v}"')
            text = "---\n" + "\n".join(lines) + text[end:]
            path.write_text(text, encoding="utf-8")

        amap = {
            "name_first": ident.get("agent_first", ""),
            "name_last": ident.get("agent_last", ""),
            "name_nick": ident.get("agent_nick", ""),
            "role": ident.get("role", ""),
        }
        umap = {
            "name_first": ident.get("operator_first", ""),
            "name_last": ident.get("operator_last", ""),
        }
        try:
            rewrite(p.assistant_identity, amap)
            rewrite(p.user_identity, umap)
            return {"ok": True, "detail": f"{p.name} identity saved"}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    # ── Profile management (the Operator's spec): New / Duplicate / Delete ──
    @app.post("/profiles/create")
    async def profiles_create(request: Request):
        """Create a brand-new profile (never a locked name)."""
        from intelligence.profiles import create_profile, PROFILES_DIR
        from core.system_profiles import is_locked
        body = await request.json()
        name = str((body or {}).get("name", "")).strip().lower()
        if not name:
            return JSONResponse({"ok": False, "error": "name required"},
                                status_code=400)
        if is_locked(name):
            return JSONResponse({"ok": False, "error": "locked name — not allowed"},
                                status_code=403)
        if (PROFILES_DIR / name).exists():
            return JSONResponse({"ok": False, "error": f"profile already exists: {name}"},
                                status_code=409)
        try:
            p = create_profile(name)
            return {"ok": True, "profile": p.name}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)},
                                status_code=400)

    @app.post("/profiles/{name}/duplicate")
    async def profiles_duplicate(name: str, request: Request):
        """Duplicate an existing profile — an EXACT copy to customize.
        The source may be locked (you can copy .default to learn from
        it); the NEW name must not be locked."""
        from intelligence.profiles import get_profile, PROFILES_DIR
        from core.system_profiles import is_locked
        import shutil
        body = await request.json()
        new_name = str((body or {}).get("new_name", "")).strip().lower()
        if not new_name:
            return JSONResponse({"ok": False, "error": "new_name required"},
                                status_code=400)
        if is_locked(new_name):
            return JSONResponse({"ok": False, "error": "locked name — not allowed"},
                                status_code=403)
        src = get_profile(name)
        if src is None:
            return JSONResponse({"ok": False, "error": f"profile not found: {name}"},
                                status_code=404)
        dst = PROFILES_DIR / new_name
        if dst.exists():
            return JSONResponse({"ok": False, "error": f"profile already exists: {new_name}"},
                                status_code=409)
        try:
            shutil.copytree(src.root, dst)
            return {"ok": True, "profile": new_name, "source": src.name}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)},
                                status_code=400)

    @app.post("/profiles/{name}/delete")
    async def profiles_delete(name: str):
        """Delete a profile. LOCKED profiles (.default/.nurse/.janitor)
        are architecture-critical — deletion is refused."""
        from intelligence.profiles import get_profile
        from core.system_profiles import is_locked
        import shutil
        if is_locked(name):
            return JSONResponse({"ok": False, "error": "locked profile — no delete allowed"},
                                status_code=403)
        p = get_profile(name)
        if p is None:
            return JSONResponse({"ok": False, "error": f"profile not found: {name}"},
                                status_code=404)
        try:
            shutil.rmtree(p.root)
            return {"ok": True, "deleted": p.name}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)},
                                status_code=400)

    @app.post("/profiles/switch")
    async def profiles_switch(request: Request):
        from intelligence.profiles import get_profile
        from core.config import ATHENA_ROOT
        from core import db as db_layer
        from uuid import uuid4
        body = await request.json()
        target = str(body.get("profile", ""))
        p = get_profile(target)
        if p is None:
            return JSONResponse({"ok": False, "error": f"profile not found: {target}"},
                                status_code=404)
        # Persist the switch (the config variable the CLI also writes).
        from core.config import set_active_profile
        if not set_active_profile(p.name):
            return JSONResponse({"ok": False,
                                 "error": "could not write config.yaml"},
                                status_code=400)
        # Update the RUNNING loop so /chat immediately uses the new profile.
        loop = get_loop()
        if loop is not None:
            try:
                from intelligence.profiles import get_profile as _gp
                newp = _gp(p.name)
                if newp is not None:
                    loop.profile = newp
                    loop.session_id = db_layer.find_last_session(
                        profile=newp.name) or str(uuid4())
            except Exception:
                pass
        return {"ok": True, "profile": p.name}

    # -- Integrations (third-party connections) -------------------------
    @app.get("/integrations")
    def integrations_status():
        from integrations import status
        return status()

    @app.post("/integrations/connect")
    async def integrations_connect(request: Request):
        from fastapi.responses import JSONResponse as _JR
        from integrations import connect
        try:
            body = await request.json()
        except Exception:
            body = {}
        r = connect(str(body.get("name", "")))
        return _JR(r)

    @app.post("/integrations/disconnect")
    async def integrations_disconnect(request: Request):
        from fastapi.responses import JSONResponse as _JR
        from integrations import disconnect
        try:
            body = await request.json()
        except Exception:
            body = {}
        r = disconnect(str(body.get("name", "")))
        return _JR(r)

    # -- Approvals (the GUI's interactive permission surface) ------------
    @app.get("/approvals/pending")
    def approvals_pending():
        from core import approvals
        return {"pending": approvals.pending_approvals(),
                "count": approvals.pending_count()}

    @app.post("/approvals/{aid}")
    async def approvals_decide(aid: str, request: Request):
        from fastapi.responses import JSONResponse as _JR
        from core import approvals
        try:
            body = await request.json()
        except Exception:
            body = {}
        verdict = str(body.get("verdict", "deny"))
        scope = str(body.get("scope", "once"))
        if verdict not in ("allow", "deny", "block"):
            return _JR({"ok": False, "error": "verdict must be allow|deny|block"})
        r = approvals.resolve_approval(aid, verdict, scope)
        return _JR(r)

    @app.get("/approvals/history")
    def approvals_history(limit: int = 50):
        from core import approvals
        return {"history": approvals.approval_history(limit=limit)}

    # -- Billing / usage (the Operator's spec: fully set up) -----------------
    @app.get("/billing")
    def billing(profile: str = ""):
        from core.billing import usage_summary, per_provider, per_session
        return {
            "profile": profile or "default",
            "summary": usage_summary(profile=profile),
            "per_provider": per_provider(profile=profile),
            "per_session": per_session(profile=profile, limit=20),
        }

    # -- Config (the GUI's Settings workspace) --------------------------
    @app.get("/config/provider")
    def config_provider():
        from core.config import load_config
        _cfg = load_config()
        _sel = _cfg.get("provider", {}).get("selection", {}).get(
            "reason", {}) or {}
        provider = str(_sel.get("provider") or "")
        model = str(_sel.get("model") or "")
        api_key = ""
        try:
            from providers.auth_store import get_api_key as _gk
            api_key = _gk(provider) if provider else ""
        except Exception:
            pass
        return {"provider": provider, "model": model, "api_key": api_key}

    @app.get("/providers/list")
    def providers_list():
        """The provider landscape for the settings page: configured
        providers + per-provider models + the active selection."""
        from providers.switch import list_providers
        return list_providers()

    @app.get("/providers/catalog")
    def providers_catalog():
        """The KNOWN provider catalog for the settings page dropdown:
        every provider Athena can use, with its default base_url and
        whether it's a local endpoint. Config choice, never credentials."""
        from providers.provider_catalog import list_catalog
        cat = list_catalog()
        # THE FULL SHAPE (the Operator's 08-10 rule): every entry carries
        # name + base_url + local + key_env — null when unknown, never
        # a missing key. api_key NEVER leaves .secret.
        out = []
        for name in sorted(cat):
            e = cat[name] or {}
            out.append({
                "name": name,
                "base_url": e.get("base_url") or "",
                "local": bool(e.get("local")),
                "key_env": (e.get("key_env") or [None])[0],
            })
        return {"catalog": out}

    @app.post("/providers/save")
    async def providers_save(request: Request):
        """Save (create or edit) a provider: name + base_url go to
        authentication.json (config); the api_key goes to .secret ONLY
        (the Operator's spec — credentials never live in config). Models are
        then probed from /models and auto-populated."""
        body = await request.json() or {}
        name = str((body or {}).get("name", "")).strip().lower()
        base_url = str((body or {}).get("base_url", "")).strip()
        api_key = str((body or {}).get("api_key", "")).strip()
        if not name:
            return {"ok": False, "detail": "provider name required"}
        if not base_url:
            return {"ok": False, "detail": "base url required"}
        try:
            from providers import auth_store
            if api_key:
                from core.secret_store import set_api_key
                set_api_key(name, api_key)
            entry = auth_store.get_provider(name) or {}
            entry["base_url"] = base_url.rstrip("/")
            auth_store.save_provider(name, entry)
            # Probe models (best-effort — never fails the save).
            discovered = auth_store.probe_models(base_url, api_key or auth_store.get_api_key(name))
            if discovered:
                entry["models"] = discovered
                auth_store.save_provider(name, entry)
            return {"ok": True, "provider": name, "base_url": entry.get("base_url"),
                    "models": discovered, "models_discovered": len(discovered)}
        except Exception as exc:
            from core.logging import log_event
            log_event(4, f"provider save failed: {exc}", source="providers", action="save")
            return {"ok": False, "detail": f"save failed: {exc}"}

    @app.post("/providers/probe")
    async def providers_probe(request: Request):
        """Probe a provider's /models endpoint: fills the Model dropdown.
        The key is used transiently and never stored.

        REFRESH SEMANTICS (the Operator's spec): when the probed provider has
        an EXISTING config entry (a configured provider), the probe is a
        REFRESH — the fresh models + base_url are written back to
        authentication.json so the entry stays truthful. A brand-new
        provider stays transient until Save.
        """
        body = await request.json() or {}
        base_url = str((body or {}).get("base_url", "")).strip()
        api_key = str((body or {}).get("api_key", "")).strip()
        name = str((body or {}).get("name", "")).strip().lower()
        if not base_url:
            return {"ok": False, "detail": "base url required"}
        try:
            from providers import auth_store
            if not api_key and name:
                api_key = auth_store.get_api_key(name)
            models = auth_store.probe_models(base_url, api_key, timeout=12)
            saved = False
            if name:
                existing = auth_store.get_provider(name)
                if existing is not None:
                    existing["models"] = models
                    existing["base_url"] = base_url
                    auth_store.save_provider(name, existing)
                    saved = True
            return {"ok": True, "models": models, "count": len(models),
                    "saved": saved}
        except Exception as exc:
            return {"ok": False, "detail": f"probe failed: {exc}"}

    @app.post("/providers/delete")
    async def providers_delete(request: Request):
        """Delete a provider: removes its config entry AND its .secret
        key (the Operator's spec — a deleted provider holds no credentials)."""
        body = await request.json() or {}
        name = str((body or {}).get("name", "")).strip().lower()
        if not name:
            return {"ok": False, "detail": "provider name required"}
        try:
            from providers import auth_store
            removed = auth_store.delete_provider(name)
            from core.secret_store import set_api_key
            set_api_key(name, "")  # clear the credential too
            if not removed:
                return {"ok": False, "detail": f"provider not configured: {name}"}
            return {"ok": True, "provider": name}
        except Exception as exc:
            return {"ok": False, "detail": f"delete failed: {exc}"}

    @app.get("/config/identity")
    def config_identity():
        from core.identity import agent_identity, user_identity, display_name
        ai = agent_identity()
        ui = user_identity()
        return {"agent": display_name(ai, "Athena"),
                "operator": display_name(ui, "")}

    @app.get("/config/compression")
    def config_compression():
        from core.config import load_config
        _cfg = load_config()
        comp = _cfg.get("compression", {}) or {}
        return {
            "context_window": int(comp.get("context_window", 32000) or 32000),
            "upper_threshold": float(comp.get("upper_threshold", 0.8) or 0.8),
            "lower_threshold": float(comp.get("lower_threshold", 0.2) or 0.2),
        }

    # -- Settings WRITE endpoints (the Operator's spec: each settings page
    #    edits its applicable config.yaml values) ----------------------
    @app.post("/config/provider")
    async def config_provider_set(request: Request):
        body = await request.json()
        name = str((body or {}).get("provider", "")).strip()
        from providers.switch import switch_provider
        r = switch_provider(name)
        return {"ok": r.get("ok", False), "detail": r.get("detail", "")}

    # -- Generic config patch (the Operator's 08-12 spec): load-then-
    # -- MERGE — the Settings page sends partial config; real values are
    # -- never replaced by the merge. Used by the Streaming toggle.
    @app.post("/config/set")
    async def config_set(request: Request):
        from core.config import load_raw_config, save_config
        body = await request.json() or {}
        profile = str((body or {}).get("profile", "") or "")
        patch = dict((body or {}).get("patch", {}) or {})
        try:
            # THE RAW-CONFIG SAVE (the Operator's 08-14 fix): the
            # website's settings must round-trip the config.yaml schema
            # 1:1. save_config expects the RAW file (operator values
            # only — defaults are never baked in), so read load_raw_config
            # — NOT load_config (which merges DEFAULTS and would write
            # the whole flattened schema back, drifting the file).
            cfg = load_raw_config(profile)
            # Deep-ish merge one level (provider.* merges into provider).
            for k, v in patch.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
            ok = save_config(cfg, profile=profile)
            return {"ok": ok, "error": "" if ok else "save failed"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/config/model")
    async def config_model_set(request: Request):
        body = await request.json()
        model = str((body or {}).get("model", "")).strip()
        from providers.switch import switch_reason_model
        r = switch_reason_model(model)
        return {"ok": r.get("ok", False), "detail": r.get("detail", "")}

    # -- Per-profile MODEL settings (the Operator's provider/models split,
    #    08-10): the Provider page holds API keys + base urls ONLY
    #    (GLOBAL — authentication.json + .secret). The Models page holds
    #    the 6 model settings — Reason/Vision/Embedding, each with a
    #    primary (left) + fallback (right) — saved PER-PROFILE into that
    #    agent's own config.yaml. Each profile picks its own
    #    provider+model from the SHARED credential set. ---
    def _active_profile_for_models() -> str:
        """The profile whose config.yaml the Models page reads/writes:
        the ACTIVE profile (profile.active), else the default."""

        def _name(p):
            try:
                return getattr(p, "name", "") or ""
            except Exception:
                return ""

        try:
            from intelligence.profiles import current_profile
            return _name(current_profile())
        except Exception:
            return ""

    @app.get("/config/models")
    def config_models_get():
        """The Models page state: the ACTIVE profile's 6 model settings
        (reason/vision/embedding × primary/fallback) + the provider
        landscape (configured providers with their model lists — shared)."""
        from providers.selection import MODEL_TYPES, load_selection
        from providers.switch import list_providers
        profile = _active_profile_for_models()
        sel = load_selection(profile=profile)
        # THE FULL SHAPE (the Operator's 08-10 rule): every type always
        # carries all four keys — null when unconfigured.
        out = {}
        for t in MODEL_TYPES:
            e = sel.get(t) or {}
            out[t] = {
                "provider": e.get("provider") or "",
                "model": e.get("model") or "",
                "fallback_provider": e.get("fallback_provider") or "",
                "fallback_model": e.get("fallback_model") or "",
            }
        try:
            providers = list_providers().get("providers", [])
        except Exception:
            providers = []
        return {"profile": profile, "selection": out, "providers": providers}

    @app.post("/config/models")
    async def config_models_set(request: Request):
        """Save ALL six model settings for the ACTIVE profile (one POST,
        one write). entries = {reason|vision|embedding: {provider, model,
        fallback_provider, fallback_model}}. Validated against the
        catalog; absent sides become four-key nulls."""
        from providers.selection import set_models
        body = await request.json() or {}
        entries = (body or {}).get("selection") or {}
        if not isinstance(entries, dict):
            entries = body or {}
        profile = _active_profile_for_models()
        r = set_models(entries, profile=profile)
        return {"ok": r.get("ok", False), "detail": r.get("detail", ""),
                "profile": profile, "selection": r.get("selection")}

    @app.post("/config/theme")
    async def config_theme_set(request: Request):
        body = await request.json()
        theme = str((body or {}).get("theme", "")).strip().lower()
        if theme not in ("dark", "light"):
            return {"ok": False, "detail": "theme must be dark|light"}
        # match_system: the Yes/No checkbox (the Operator's spec) — when the
        # user picks a mode explicitly it flips to False so the choice
        # persists; when True the theme follows the OS.
        ms = (body or {}).get("match_system")
        try:
            from core.config import load_raw_config, save_config
            cfg = load_raw_config()
            th = cfg.setdefault("theme", {})
            th["mode"] = theme
            if ms is not None:
                th["match_system"] = bool(ms)
            save_config(cfg)
            return {"ok": True, "detail": f"theme → {theme}",
                    "match_system": th.get("match_system", True)}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    @app.get("/config/theme")
    def config_theme_get():
        from core.config import load_config
        _cfg = load_config()
        th = _cfg.get("theme", {})
        return {"mode": th.get("mode", "dark"),
                "match_system": th.get("match_system", True)}

    # The default 5-color palettes (the Operator's exact hex codes) — used
    # when config.yaml has no theme palettes yet.
    _DEFAULT_PALETTES = {
        "light": ["#fafafa", "#e1e1e1", "#fa7d00", "#fafa00", "#000000"],
        "dark": ["#1e1e1e", "#323232", "#fa0000", "#fa7d00", "#fafafa"],
    }

    @app.get("/config/theme/palette")
    def config_theme_palette_get():
        """The two theme palettes (light + dark, 5 hex colors each)."""
        from core.config import load_raw_config
        _t = (load_raw_config().get("theme") or {})
        pal = {k: list(_t.get(k) or []) for k in ("light", "dark")}
        for k in ("light", "dark"):
            if len(pal[k]) != 5:
                pal[k] = list(_DEFAULT_PALETTES[k])
        return {"palettes": pal}

    @app.post("/config/theme/palette")
    async def config_theme_palette_set(request: Request):
        """Save both palettes (light + dark, 5 hex colors each)."""
        body = await request.json()
        pal = (body or {}).get("palettes") or {}
        ok = True
        for k in ("light", "dark"):
            v = pal.get(k)
            if not isinstance(v, list) or len(v) != 5:
                return {"ok": False, "detail": f"{k} must be 5 hex colors"}
            if not all(isinstance(x, str) and x.startswith("#") for x in v):
                return {"ok": False, "detail": f"{k} colors must be hex"}
        try:
            from core.config import load_raw_config, save_config
            cfg = load_raw_config()
            cfg.setdefault("theme", {})["light"] = list(pal["light"])
            cfg.setdefault("theme", {})["dark"] = list(pal["dark"])
            save_config(cfg)
            return {"ok": True, "detail": "palettes saved"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    # -- The FULL config editor (the Operator's spec: EVERY setting is
    #    customizable — the settings page edits the whole config.yaml) ---
    @app.get("/config/all")
    def config_all():
        """The raw config.yaml (as written — no defaults merged), so the
        settings page can render EVERY section as editable fields."""
        from core.config import load_raw_config
        return {"config": load_raw_config()}

    @app.get("/config/emotion")
    def config_emotion_get():
        """The ACTIVE profile's emotional state: the agent + operator
        vectors, the current snapshots, the active pair combinations,
        and the full 24×24 emotion table with its highlight cells (the
        BEHAVIOR page + the Settings Emotion tab)."""
        from core.emotion import (read_emotion, active_combinations,
                                  table_grid, highlight_cells,
                                  AXES, WHEEL, EMOTION_ORDER)
        profile = _active_profile_for_models()
        agent = read_emotion("assistant", profile)
        operator = read_emotion("user", profile)
        combos = active_combinations(agent.get("vector", {}))
        return {
            "profile": profile,
            "axes": AXES,
            "wheel": {a: list(WHEEL[a]) for a in AXES},
            "bands": {"low": -1.0, "low_max": -0.33, "mid_max": 0.33, "high": 1.0},
            "emotion_order": EMOTION_ORDER,
            "table": table_grid(),
            "highlight": highlight_cells(agent.get("vector", {})),
            "agent": {"vector": agent.get("vector", {}),
                      "current": agent.get("current", ""),
                      "mood": agent.get("mood", ""),
                      "updated": agent.get("updated", "")},
            "operator": {"vector": operator.get("vector", {}),
                         "current": operator.get("current", ""),
                         "mood": operator.get("mood", ""),
                         "updated": operator.get("updated", "")},
            "combinations": combos,
        }

    @app.get("/config/emotion/history")
    def config_emotion_history(limit: int = 40):
        """The emotional time series for the ACTIVE profile (the polygraph):
        the post-turn emotion vectors from the vault, oldest → newest."""
        import json as _json
        from core.emotion import AXES
        profile = _active_profile_for_models()
        points = []
        try:
            from core import db as db_layer
            conn = db_layer.connect_vault(profile)
            rows = conn.execute(
                "SELECT emotion, time FROM entries "
                "WHERE deleted=0 AND emotion IS NOT NULL AND emotion != '' "
                "AND role='Assistant' ORDER BY rowid DESC LIMIT ?",
                (max(1, min(int(limit), 200)),)).fetchall()
            conn.close()
            for row in reversed(rows):
                try:
                    vec = _json.loads(row["emotion"])
                except Exception:
                    continue
                if not isinstance(vec, dict):
                    continue
                points.append({
                    "time": row["time"] or "",
                    "vector": {axis: float(vec.get(axis, 0.0)) for axis in AXES},
                })
        except Exception:
            points = []
        return {"profile": profile, "axes": AXES, "points": points}

    # ── PERMISSIONS (the Operator's 08-15 spec): the Permissions tab —
    #    the 4-channel store (per profile) + the loaded tools/skills list.
    @app.get("/permissions")
    def permissions_get(profile: str = ""):
        """The ACTIVE profile's 4-channel permissions store + the loaded
        tools/skills the tab can populate."""
        from security.permissions import list_rules
        from core.system_profiles import is_locked
        prof = _active_profile_for_models() if not profile else profile
        store = list_rules(prof)
        # The loaded tool/skill names (the registry the tab renders).
        tools = []
        skills = []
        try:
            from filesystem.tools import schemas_with_skills
            from intelligence.skills import load_skills
            for s in (schemas_with_skills(load_skills()) or []):
                fn = s.get("function", {}) or {}
                nm = fn.get("name", "")
                if not nm:
                    continue
                if str(nm).startswith(("skill:", "skill_")):
                    skills.append(str(nm))
                else:
                    tools.append(str(nm))
            tools = sorted(set(tools))
            skills = sorted(set(skills))
        except Exception:
            tools = []
            skills = []
        return {
            "profile": prof,
            "locked": is_locked(prof),
            "store": store,
            "loaded_tools": tools,
            "loaded_skills": skills,
        }

    @app.post("/permissions")
    async def permissions_set(request: Request):
        """Write a Permissions-tab change: a channel list entry (add/remove
        a tool/skill by name) OR the global channel's flags."""
        from security.permissions import (set_channel_entry,
                                          set_global_flags, _ALL_CHANNELS)
        body = await request.json() or {}
        profile = _active_profile_for_models()
        from core.system_profiles import is_locked
        if is_locked(profile):
            return {"ok": False, "detail": "locked profile — no modifications"}
        action = str(body.get("action", ""))
        try:
            if action == "entry":
                ch = str(body.get("channel", ""))
                if ch not in _ALL_CHANNELS:
                    return {"ok": False, "detail": f"unknown channel {ch}"}
                ok = set_channel_entry(
                    profile, ch, str(body.get("kind", "tools")),
                    str(body.get("name", "")),
                    bool(body.get("present", False)))
                return {"ok": ok, "detail": "permission updated" if ok
                        else "update failed"}
            if action == "global":
                ok = set_global_flags(
                    profile, str(body.get("kind", "tools")),
                    str(body.get("type", "allow")),
                    str(body.get("level", "session")))
                return {"ok": ok, "detail": "global permission updated"
                        if ok else "update failed"}
            return {"ok": False, "detail": "unknown action"}
        except Exception as exc:
            from core.logging import log_event
            log_event(4, f"permissions set failed: {exc}",
                      source="security", action="permissions_set")
            return {"ok": False, "detail": str(exc)}

    @app.post("/config/emotion")
    async def config_emotion_set(request: Request):
        """Write the ACTIVE profile's emotion vector (side = assistant |
        user; vector = {axis: -1..+1}) + the optional MOOD sentence (the
        08-15 spec: the <=64-word felt description, stored in EMOTION.md).
        Load-then-merge — never wipes the other side."""
        from core.emotion import write_emotion, AXES
        body = await request.json() or {}
        side = str((body or {}).get("side", "assistant")).lower()
        if side not in ("assistant", "user"):
            side = "assistant"
        vec = (body or {}).get("vector") or {}
        if not isinstance(vec, dict):
            return {"ok": False, "detail": "vector must be an object"}
        clean = {}
        for axis in AXES:
            try:
                v = float(vec.get(axis, 0.0))
            except (TypeError, ValueError):
                v = 0.0
            clean[axis] = max(-1.0, min(1.0, v))
        mood = str((body or {}).get("mood", "") or "").strip()
        profile = _active_profile_for_models()
        ok = write_emotion(side, profile, clean, mood=mood)
        return {"ok": ok, "detail": "emotion saved" if ok else "save failed",
                "profile": profile, "side": side, "vector": clean}

    @app.post("/config/all")
    async def config_all_save(request: Request):
        """Write the WHOLE edited config back to config.yaml.

        THE SCHEMA-1:1 SAVE (the Operator's 08-14 fix): the settings
        page POSTs only the fields it renders — keys it doesn't show
        (streaming, emotion.llm_gate, ...) would be DROPPED by a
        verbatim write. Before saving, fill any DEFAULTS keys missing
        from the posted config so config.yaml ALWAYS carries the full
        schema — the website's saved file matches the seed 1:1.
        """
        body = await request.json()
        cfg = (body or {}).get("config")
        if not isinstance(cfg, dict):
            return {"ok": False, "detail": "config must be an object"}
        try:
            from core.config import (save_config, DEFAULTS, deep_merge,
                                     load_raw_config)
            from core.system_profiles import _defaults_seed_cfg
            # THE SCHEMA-1:1 SAVE (the 08-14 fix): merge the posted
            # config OVER THE RAW FILE first (so keys the page doesn't
            # render — streaming, selection, emotion — keep their EXISTING
            # file values), THEN deep-fill any SEED keys still missing
            # (the SEED = DEFAULTS + the widened operator channel; the
            # file always carries the full schema). The operator's posted
            # values win; nothing else is lost.
            import copy
            base = load_raw_config()
            if not isinstance(base, dict):
                base = {}
            merged = deep_merge(base, cfg)
            merged = deep_merge(copy.deepcopy(_defaults_seed_cfg()), merged)
            ok = save_config(merged)
            return {"ok": ok, "detail": "config saved" if ok else "save failed"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    # -- The COMMAND REGISTRY (the Operator's spec): the GUI command palette.
    #    Every registered command (core + tools) with its usage syntax,
    #    organized as a FILE-SYSTEM TREE: categories (folders) →
    #    commands → subcommands, so the GUI can drill down like a
    #    filesystem and the user builds a command from what exists.
    def _cmd_help(name: str) -> str:
        """The usage/help text for a command (or a synthesized one)."""
        try:
            from autonomy.commands import _REGISTRY
            entry = _REGISTRY.get(name.lower())
            if entry and entry.get("help"):
                return entry["help"]
        except Exception:
            pass
        subs = []
        try:
            subs = get_subcommands(name)
        except Exception:
            pass
        return f"{name} [{' | '.join(subs)}]" if subs else f"{name}"

    # The category mapping (the Operator's file-system view): each command
    # belongs to one folder by function. Unknown tools fall back to
    # their origin (filesystem/web/memory).
    _CMD_CATEGORY = {
        # Agent basics.
        "send": "Core", "session": "Core", "status": "Core", "help": "Core",
        "quit": "Core", "version": "Core", "skills": "Core",
        "plugins": "Core", "tools": "Core",
        # System / housekeeping.
        "doctor": "System", "logs": "System", "security": "System",
        "backup": "System", "config": "System", "lifecycle": "System",
        "cron": "System",
        # The brain / learning.
        "curator": "Brain", "nurse": "Brain", "events": "Brain",
        "kanban": "Brain",
        # Provider / model.
        "provider": "Provider", "model": "Provider",
        # Profile.
        "profile": "Profile",
    }
    # TOOL CATEGORIES (the Operator's simplification): the GROUPING lives
    # here (Filesystem / Execute / Transfer / Memory / Vault), but the
    # NAME SET is derived from the registry's canonical tools — no
    # duplicated name list to drift from filesystem/tools.py. The
    # category map covers the canonical groups; anything else falls to
    # the registry check below.
    _TOOL_CATEGORY = {
        "append": "Filesystem", "compress": "Filesystem", "copy": "Filesystem",
        "delete": "Filesystem", "exists": "Filesystem", "find": "Filesystem",
        "fs_stat": "Filesystem", "hash": "Filesystem", "list": "Filesystem",
        "mkdir": "Filesystem", "move": "Filesystem", "patch": "Filesystem",
        "read_file": "Filesystem", "rename": "Filesystem",
        "replace": "Filesystem", "search": "Filesystem",
        "tree": "Filesystem", "write_file": "Filesystem",
        "terminal": "Execute", "process": "Execute", "kill": "Execute",
        "download": "Transfer", "upload": "Transfer", "extract": "Transfer",
        "memory_add": "Memory", "memory_list": "Memory",
        "vault_query": "Vault", "vault_semantic": "Vault", "vault_store": "Vault",
    }

    def _category_of(name: str) -> str:
        if name in _CMD_CATEGORY:
            return _CMD_CATEGORY[name]
        if name in _TOOL_CATEGORY:
            return _TOOL_CATEGORY[name]
        # ALIAS + registry-driven (the Operator's no-loss rule): alias names
        # (read/write/stat/execute) map to their canonical category via
        # the registry, and any registered tool not in the map gets a
        # sensible category instead of falling to "Other".
        try:
            from filesystem.tools import TOOLS, resolve
            if name in TOOLS:
                canon = resolve(name)
                if canon in _TOOL_CATEGORY:
                    return _TOOL_CATEGORY[canon]
                if name.startswith(("browser_", "web_")):
                    return "Web"
                return "Tool"
        except Exception:
            pass
        if name.startswith(("browser_", "web_")):
            return "Web"
        return "Other"

    # The LIVE ARGUMENT sources (the Operator's spec): commands whose next
    # layer is REAL data — /skills lists the actual skills, /tools the
    # actual tools, /provider the actual providers, etc. The palette
    # shows these as the {Argument/Action} layer, so each chain of the
    # command follows how commands are actually structured.
    def _live_args(name: str) -> list[str]:
        try:
            if name == "skills":
                from intelligence.skills import load_skills, skills_index
                import re as _re
                skills = load_skills()
                idx = skills_index(skills) or ""
                names = _re.findall(r"^\s*[•\-]\s*\[?([A-Za-z0-9_\-]+)\]?", idx, _re.M)
                return sorted(set(names)) or []
            if name in ("tools", "list"):
                from filesystem.tools import TOOLS
                return sorted(TOOLS)
            if name == "plugins":
                from intelligence.plugins import load_all
                ps = load_all().get("plugins", [])
                out = []
                for p in ps:
                    try:
                        out.append(p.name if hasattr(p, "name") else str(p))
                    except Exception:
                        out.append(str(p))
                return sorted(set(out))
            if name == "provider":
                from providers.switch import list_providers
                return [p["name"] for p in list_providers().get("providers", [])]
            if name == "model":
                from providers.switch import list_providers
                models = set()
                for p in list_providers().get("providers", []):
                    models.update(p.get("models", []) or [])
                return sorted(models)
            if name == "profile":
                from intelligence.profiles import list_profiles
                return [str(p.name) for p in list_profiles()]
            if name == "session":
                from core import db as db_layer
                return db_layer.uuid_session_ids(limit=20)
            if name == "config":
                from core.config import load_config
                return sorted(load_config().keys())
            if name == "doctor":
                import os
                docs = os.path.join(os.path.dirname(__file__), "..", "doctor")
                subs = []
                for root, dirs, files in os.walk(docs):
                    for f in files:
                        if f.startswith("20_") and f.endswith(".py"):
                            subs.append(f[3:-3])
                return sorted(subs)
        except Exception:
            pass
        return []

    @app.get("/commands")
    def commands():
        try:
            from autonomy.commands import (register_core_commands,
                                           list_commands,
                                           get_children,
                                           is_leaf)
            register_core_commands()
            # Build the recursive tree: categories → commands → children
            # → ... at ANY depth (the Operator's spec: infinitely deep chains).
            # Live args merge in at the first level and flow down.
            def build_children(name: str, path: list[str]) -> list:
                subs = get_children(name, path)
                # Live data for the FIRST level under the module (e.g.
                # /skills doctor) merges with static children.
                if not path:
                    live = _live_args(name)
                    subs = subs + [a for a in live if a not in subs]
                return [{
                    "name": s,
                    "children": build_children(name, path + [s]),
                } for s in sorted(subs)]

            cats: dict[str, list] = {}
            for name in list_commands():
                cats.setdefault(_category_of(name), []).append({
                    "name": name,
                    "children": build_children(name, []),
                    "help": _cmd_help(name),
                })
            tree = [{"name": c, "commands": sorted(cats[c], key=lambda x: x["name"])}
                    for c in sorted(cats)]
            return {"tree": tree, "count": len(list_commands())}
        except Exception as exc:
            return {"tree": [], "count": 0, "error": str(exc)}

    # -- Sessions ------------------------------------------------------
    @app.get("/sessions")
    def sessions(profile: str = ""):
        from core import db as db_layer
        # UUID-ONLY (the Operator's 08-12 strict-name rule): the UI
        # dropdown must NEVER show non-UUID sessions (toolcols, roles,
        # s1, nurse-* — doctor/test debris). list_session_ids returns
        # every file; uuid_session_ids filters to session-{UUID}.db.
        sids = db_layer.uuid_session_ids(profile=profile)
        # ACTIVITY (the Operator's spec): each session's last-active + staleness.
        try:
            activity = db_layer.session_activity(profile=profile)
        except Exception:
            activity = []
        labels = db_layer.load_session_labels(profile=profile)
        return {"profile": profile or "default",
                "sessions": sids[-30:],
                "labels": labels,
                "activity": activity}

    @app.put("/sessions/{sid}/label")
    async def session_label(sid: str, request: Request):
        """Rename a session: {UUID: label} in the registry (the Operator's
        08-12 spec — the system sees UUID, the user sees the label)."""
        from core import db as db_layer
        body = await request.json() or {}
        label = str((body or {}).get("label", "")).strip()
        profile = str((body or {}).get("profile", "")).strip()
        try:
            r = db_layer.set_session_label(sid, label, profile=profile)
            return r
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    @app.post("/sessions/delete-by-count")
    async def sessions_delete_by_count(request: Request):
        """MASS DELETE sessions by their ENTRY COUNT (the Operator's 08-11
        spec). Two modes:

          min_entries: delete every session with AT LEAST N entries
          max_entries: delete every session with AT MOST N entries

        Never deletes the CURRENT session (the caller picks a different
        one first). Returns how many were deleted + which.
        """
        body = await request.json() or {}
        profile = str((body or {}).get("profile", "")).strip()
        try:
            min_n = int((body or {}).get("min_entries") or 0)
        except (TypeError, ValueError):
            min_n = 0
        try:
            max_n = int((body or {}).get("max_entries") or 0)
        except (TypeError, ValueError):
            max_n = 0
        if min_n <= 0 and max_n <= 0:
            return JSONResponse(
                {"ok": False, "detail": "min_entries or max_entries required"},
                status_code=400)
        from core import db as db_layer
        deleted = []
        kept_current = ""
        try:
            # The current session: never delete the one in use.
            loop_obj = state.get("loop") or {}
            kept_current = str(getattr(loop_obj, "session_id", "") or "")
        except Exception:
            kept_current = ""
        for row in db_layer.session_activity(profile=profile):
            sid = row.get("session_id", "")
            if not sid or sid == kept_current:
                continue
            count = int(row.get("messages", 0) or 0)
            if (min_n > 0 and count >= min_n) or (max_n > 0 and count <= max_n):
                if db_layer.delete_session(sid, profile=profile):
                    deleted.append({"session_id": sid, "messages": count})
        return {"ok": True, "deleted": deleted,
                "deleted_count": len(deleted),
                "profile": profile or "default",
                "kept_current": kept_current}

    @app.get("/sessions/{sid}")
    def session_history(sid: str, limit: int = 50, profile: str = ""):
        from core import db as db_layer
        try:
            rows = db_layer.get_session_history(sid, limit=limit, profile=profile)
            return {"session_id": sid, "profile": profile or "default",
                    "messages": [db_layer._row_to_jsonl_entry(r) for r in rows]}
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    # -- Vault ---------------------------------------------------------
    @app.get("/vault")
    def vault(query: str = ""):
        from context.retrieval import retrieve
        try:
            r = retrieve(query, "", profile="")
            return {"query": query, "result": r}
        except Exception as exc:
            return {"query": query, "error": str(exc)}

    # -- Vault GRID (the Operator's cell-based table: X = columns, Y = rows) -
    @app.get("/vault/table")
    def vault_table(profile: str = "", limit: int = 500, offset: int = 0):
        """A PAGE of the vault as a grid: columns + rows (cells).

        THE 08-17 WEBSITE PAGINATION (the Operator's spec): the website
        vault loads 500 rows per page with a "Load older" button — offset
        pages further back (older entries), exactly like the chat page.
        The AGENT's vault_query tool (not this endpoint) sees the FULL
        vault; the website grid stays paginated for usability.
        """
        from core import db as db_layer
        conn = db_layer.connect_vault(profile)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)")]
            rows = conn.execute(
                f"SELECT {', '.join(cols)} FROM entries WHERE deleted=0"
                " ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (limit, offset)).fetchall()
            # THE 08-17 CHRONOLOGICAL ORDER (the Operator's spec): the vault
            # renders OLDEST→NEWEST top→bottom (like the chat history), the
            # newest entry at the bottom. The SQL fetches newest-first
            # (rowid DESC) for paging; REVERSED, each page reads oldest at
            # the top, newest at the bottom. "Load older" (higher offset)
            # fetches the page before and PREPENDS it above (the chat
            # load-more pattern).
            rows = rows[::-1]
            total = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE deleted=0").fetchone()[0]
            data = []
            for row in rows:
                d = dict(row)
                for k, v in d.items():
                    if isinstance(v, (bytes, bytearray)):
                        d[k] = v.decode("utf-8", "replace")
                data.append(d)
            return {"profile": profile or "default", "columns": cols,
                    "rows": data, "offset": offset, "limit": limit,
                    "total": total}
        finally:
            conn.close()

    @app.post("/vault/row")
    async def vault_row_add(request: Request):
        """Add a row to the vault (the Operator's ADD ROWS)."""
        from core import db as db_layer
        body = await request.json()
        profile = str(body.get("profile", ""))
        type = str(body.get("type", body.get("kind", "message")))
        content = str(body.get("content", ""))
        role = str(body.get("role", ""))
        try:
            entry_id = db_layer.record_vault_entry(
                type, content, profile=profile, role=role,
                context=str(body.get("context", "") or ""),
                dedup=False)
            return {"ok": True, "id": entry_id}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/vault/import-db")
    async def vault_import_db(request: Request):
        """THE 08-17 VAULT IMPORT (the Operator's spec): upload a .db, export
        it to JSONL (strict 1:1 match), then import into the profile's vault.
        Copy-first — the source is read-only, never modified; only matching
        variables cross over; the original vault is never touched."""
        import tempfile, shutil
        from knowledge.vault_transfer import export_to_jsonl, import_jsonl
        from pathlib import Path as _P
        form = await request.form()
        up = form.get("file")
        if up is None:
            return JSONResponse({"ok": False, "error": "no file in request"}, status_code=400)
        try:
            raw = await up.read()
        except Exception:
            raw = getattr(up, "file", None).read() if getattr(up, "file", None) else b""
        if not raw:
            return JSONResponse({"ok": False, "error": "empty file"}, status_code=400)
        prof = str(form.get("profile") or "")
        session = str(form.get("session_id") or "")
        # Save the upload to a temp .db, export to JSONL, import.
        tmp_db = _P(tempfile.gettempdir()) / f"athena-upload-{_P(str(getattr(up, 'filename', 'upload') or 'upload')).stem}-{__import__('uuid').uuid4().hex[:8]}.db"
        try:
            tmp_db.write_bytes(raw)
            export_r = export_to_jsonl(str(tmp_db), str(tmp_db.with_suffix('.jsonl')))
            if not export_r.get("ok"):
                return JSONResponse({"ok": False, "error": export_r.get("error", "export failed")}, status_code=400)
            import_r = import_jsonl(str(tmp_db.with_suffix('.jsonl')), profile=prof, session_id=session)
            return {"ok": True, **import_r, "exported_columns": export_r.get("columns_matched", [])}
        finally:
            try:
                tmp_db.unlink(missing_ok=True)
                tmp_db.with_suffix('.jsonl').unlink(missing_ok=True)
            except Exception:
                pass

    @app.put("/vault/row/{row_id}")
    async def vault_row_edit(row_id: str, request: Request):
        """Edit a row's cells (the Operator's EDIT ROWS)."""
        from core import db as db_layer
        body = await request.json()
        profile = str(body.get("profile", ""))
        cells = body.get("cells", {}) or {}
        if not isinstance(cells, dict) or not cells:
            return JSONResponse({"ok": False, "error": "no cells"}, status_code=400)
        conn = db_layer.connect_vault(profile)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(entries)")}
            sets = []
            vals = []
            for col, val in cells.items():
                if col not in cols or col == "id" or col == "deleted":
                    continue
                sets.append(f"{col}=?")
                vals.append(val if val is not None else None)
            if not sets:
                return JSONResponse({"ok": False, "error": "no valid cells"},
                                    status_code=400)
            vals.append(row_id)
            conn.execute(f"UPDATE entries SET {', '.join(sets)} WHERE id=?",
                         vals)
            conn.commit()
            return {"ok": True, "id": row_id}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        finally:
            conn.close()

    @app.delete("/vault/row/{row_id}")
    async def vault_row_delete(row_id: str, profile: str = ""):
        """Soft-delete a row (the Operator's SUBTRACT ROWS — recoverable)."""
        from core import db as db_layer
        conn = db_layer.connect_vault(profile)
        try:
            conn.execute("UPDATE entries SET deleted=1 WHERE id=?",
                         (row_id,))
            conn.commit()
            return {"ok": True, "id": row_id}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        finally:
            conn.close()

    # -- Logs ----------------------------------------------------------
    # The LOGS reader (the Operator's 08-12 spec):
    #      scope=all   → .athena/logs/ — the ROOT AGGREGATE (the appended
    #                    version of ALL profiles' logs in ONE file — the
    #                    Developer Terminal reads this)
    #      scope=<p>   → profiles/<p>/logs/ — that profile's OWN stream
    #      (default)   → the current/default profile's stream
    #    Raw JSONL text (the Terminal format the Operator prefers).
    @app.get("/logs")
    def logs(profile: str = "", scope: str = ""):
        from metrics.logger import read_session
        from core.config import ATHENA_ROOT
        try:
            if scope == "all":
                # The root aggregate: .athena/logs/{date}_metric.log
                from metrics.logger import LOGS_DIR
                logs_dir = LOGS_DIR
                if not logs_dir.is_dir():
                    return {"log": "", "scope": "all"}
                files = sorted(logs_dir.glob("*_metric.log"), reverse=True)
                text = ""
                for f in files:
                    try:
                        text += f.read_text(encoding="utf-8",
                                            errors="replace")
                    except Exception:
                        continue
                return {"log": text, "scope": "all", "files": len(files)}
            return {"log": read_session(profile=profile), "scope": scope or "profile"}
        except Exception as exc:
            return {"error": str(exc)}
    # -- The CONSOLE (the Operator's spec): the profile-scoped operator
    #    view — reads the SAME consolidated stream as the terminal but
    #    ONLY the CURRENT profile's {date}_metric.log (the Operator
    #    Console vs the Developer Terminal). Raw JSONL entries, newest
    #    first, with the listener's code + reason fields.
    @app.get("/console")
    def console(profile: str = "", limit: int = 200):
        from metrics import events as events_mod
        try:
            entries = events_mod.read_events(profile=profile,
                                             limit=max(1, min(limit, 500)))
            # Newest first (read_events returns newest-first already).
            return {"entries": entries}
        except Exception as exc:
            return {"error": str(exc)}

    # -- The TERMINAL (the Operator's spec): the raw shell. Runs a system
    #    command in the ACTIVE profile's WORKSPACE (each agent works in
    #    its own workspace — settable by nature via workspace.dir) and
    #    returns its output. Blocked: sudo, network, writes outside
    #    .athena.
    @app.post("/terminal")
    async def terminal_run(request: Request):
        import subprocess
        from core.config import ATHENA_ROOT as _TERM_ROOT
        body = await request.json()
        cmd = str((body or {}).get("cmd", "")).strip()
        if not cmd:
            return JSONResponse({"ok": False, "error": "cmd required"},
                                status_code=400)
        low = cmd.lower()
        for bad in ("sudo", "su -", "ssh ", "curl ", "wget ", "nc ", "telnet",
                    "apt ", "dnf ", "yum ", "pip install", "rm -rf ",
                    "rm -fr ", "mkfs", "dd if=", "> /etc", "chmod 777 /",
                    "passwd", "shutdown", "reboot", "poweroff"):
            if bad in low:
                return JSONResponse({"ok": False, "error": f"blocked: {bad.strip()}"},
                                    status_code=403)
        # The cwd is the ACTIVE profile's WORKSPACE by default — a bee
        # works in its own hive. With sandbox:true the terminal opens in
        # the profile's SANDBOX (its safe home base). Falls back to the
        # queen's workspace when no active profile resolves.
        sandbox_mode = bool((body or {}).get("sandbox", False))
        try:
            from intelligence.profiles import current_profile
            prof = current_profile()
            cwd = str(prof.sandbox_dir if sandbox_mode else prof.workspace_dir)
        except Exception:
            cwd = str(_TERM_ROOT)
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True,
                                  text=True, timeout=20, cwd=cwd)
            return {"ok": True, "exit": proc.returncode,
                    "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-2000:]}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout (20s)"}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)},
                                status_code=400)

    # -- Tools ---------------------------------------------------------
    @app.get("/tools")
    def tools():
        from filesystem.tools import schemas
        return {"tools": schemas()}

    # -- WebSocket: live flow events -----------------------------------
    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                data = await websocket.receive_text()
                # The client sends a chat message; the loop runs through
                # the queue; the reply is streamed back with the events.
                msg = json.loads(data)
                loop = get_loop()
                if loop is None:
                    await websocket.send_text(json.dumps(
                        {"event": "error", "detail": "no loop"}))
                    continue
                events = []
                orig_event = getattr(loop, "on_event", None)
                loop.on_event = lambda k, d: events.append({"kind": k, "detail": d})
                try:
                    ack = loop.handle_event({
                        "session_id": msg.get("session_id") or loop.session_id,
                        "content": msg.get("message", ""),
                        "channel": "user",   # the operator's chat — conversational
                    })
                    loop.drain()
                finally:
                    loop.on_event = orig_event
                reply = ""
                for response in loop.responses:
                    if response.get("event_id") == ack.get("event_id"):
                        reply = response.get("reply", "")
                        break
                await websocket.send_text(json.dumps({
                    "event": "done", "reply": reply, "events": events[-30:],
                }))
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    return app

"""The multiplexer's HTTP surface — FastAPI, one addressable endpoint per channel.

Bound to 127.0.0.1 by default: there is no authentication in 0.0.1, so it must not be reachable
from the network (documentation/architecture/10-runtime-model.md).
"""
from __future__ import annotations

from pathlib import Path

import os
import re
import time as _time
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse, Response


class RevalidatingStatic(StaticFiles):
    """Static files that must be revalidated on every load.

    Each response carries a conditional-request validator, so a browser asks before reusing a cached
    script or stylesheet. These are small local files: a conditional request is cheap, a stale panel is not.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if isinstance(response, Response):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response
from pydantic import BaseModel, Field

from config import loader
from core import agentconfig
from core import profile as profile_mod
from providers import base as providers_base
from providers import base as provider_base
from server import channels as channels_mod
from server.channels import ChannelManager
from tools import registry as tools_registry


class PersonaModel(BaseModel):
    # The human-facing description of the agent; written to ASSISTANT.md.
    name: str = Field("", max_length=80)
    role: str = Field("", max_length=600)
    tone: str = Field("", max_length=600)
    boundaries: str = Field("", max_length=600)


class AgentModel(BaseModel):
    # Where this agent's answers come from. `local` names a folder under .athena/models.
    provider: str = Field("openai", max_length=20)
    base_url: str = Field("", max_length=300)
    name: str = Field("", max_length=200)
    window: int = Field(32768, ge=32768)
    max_output_tokens: int = Field(1024, ge=1)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    local: str = Field("", max_length=120)


class ApprovalProbe(BaseModel):
    """A synthetic request, for exercising the popup in development."""

    agent: str = "probe"
    path: str = "/tmp/example/notes.md"
    op: str = "write"
    tool: str = "write_file"
    reason: str = "this path is outside the agent's scope"


class InstallRequest(BaseModel):
    """The answer to an install prompt: `install` performs it, `deny` forgets it."""

    decision: str = "install"


class ApprovalRequest(BaseModel):
    """The answer to a prompt: allow or deny, remembered for once / session / permanent."""

    decision: str = "allow"
    scope: str = "once"


class KeyRequest(BaseModel):
    """A key to store, or a model list to refresh. The value is written to `.secret` and never returned."""

    key_name: str = ""
    value: str = ""
    scope: str = "global"
    agent: str = ""
    format: str = "openai"
    base_url: str = ""


class AgentSetup(BaseModel):
    """One profile, set up in one request — what the wizard posts after its last step."""

    persona: PersonaModel = Field(default_factory=PersonaModel)
    model: AgentModel = Field(default_factory=AgentModel)

    # Collected once and written to .secret; never stored in the profile and never returned.
    api_key: str = Field("", max_length=400)


class NewAgent(BaseModel):
    """A name is enough to register a profile; the wizard fills in the rest afterwards."""

    name: str = Field(..., min_length=1, max_length=32)


class AskRequest(BaseModel):
    """One agent addressing another: the question text."""

    text: str = Field(..., min_length=1, max_length=8000)


class TurnRequest(BaseModel):
    # One user turn; capped so an unbounded body cannot exhaust memory.
    text: str = Field(..., min_length=1, max_length=32000)


def create_app() -> FastAPI:
    cfg = loader.load()
    provider = provider_base.build(cfg)
    tools = tools_registry.default_registry(cfg)
    agents_dir = loader.paths(cfg)["agents"]
    managers = ChannelManager(cfg, provider, tools, agents_dir)

    # -- the credential sidecar --------------------------------------------
    # Keys are held by the proxy process, not this one. `credentials.mode: direct` loads them in-process; the
    # default is the sidecar, so a key never enters the process that builds prompts.
    cred_cfg = (cfg.get("security") or {}).get("credentials") or {}
    cred_state = {"mode": "direct", "reason": "disabled"}
    try:
        import json as _json
        import subprocess as _subprocess
        import sys as _sys

        import providers.base as _pbase

        model_cfg = cfg.get("model") or {}
        wants_sidecar = str(cred_cfg.get("mode") or "sidecar").lower() == "sidecar"
        local_on = bool((model_cfg.get("local") or {}).get("enabled"))
        upstream_host = ""
        base_url = str(model_cfg.get("base_url") or "")
        if base_url:
            upstream_host = base_url.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
        if wants_sidecar and not local_on and upstream_host:
            proxy_proc = _subprocess.Popen(
                [_sys.executable, "-m", "core.credproxy", "--athena", str(agents_dir.parent),
                 "--allow", upstream_host],
                cwd=str(Path(__file__).resolve().parents[1]),
                stdout=_subprocess.PIPE, text=True)
            handshake = _json.loads(proxy_proc.stdout.readline() or "{}")
            if handshake.get("port"):
                _pbase.PROXY.update({"url": f"http://127.0.0.1:{handshake['port']}",
                                     "token": handshake.get("token", "")})
                cred_state = {"mode": "sidecar", "reason": f"proxy on 127.0.0.1:{handshake['port']}",
                              "upstream": upstream_host}
            else:
                cred_state = {"mode": "direct", "reason": "proxy did not start"}
        else:
            cred_state = {"mode": "direct",
                          "reason": "local model" if local_on else "no remote base_url"}
    except Exception as exc:                          # noqa: BLE001 — never block startup on this
        cred_state = {"mode": "direct", "reason": f"sidecar failed: {type(exc).__name__}: {exc}"}

    # -- the two watchdogs -------------------------------------------------
    # Red team reads what the guards inside refused; blue team compares the disk to a baseline. Both write a report
    # that the agents can read; neither acts on its own.
    from core import paths as paths_mod
    from core import security as security_mod

    security_folder = agents_dir.parent / "security"
    ledger = security_mod.Ledger(security_folder)
    paths_mod.set_event_sink(
        lambda kind, path, detail, lane="": ledger.record("red", kind, detail, path, lane))
    watch = None
    security_cfg = cfg.get("security") or {}
    if security_cfg.get("enabled", True):
        from core import netdefense

        net_cfg = security_cfg.get("network") or {}
        allow_list = list(net_cfg.get("allow") or [])
        watch = security_mod.SecurityWatch(
            agents_dir.parent, security_folder,
            interval_s=float(security_cfg.get("interval_s") or 300),
            on_finding=None,
            extras=(lambda: netdefense.outbound_findings(ledger, allow_list)) if net_cfg.get("audit_outbound", True)
            else None)
        watch.ledger = ledger
        watch.start()
    # a profile without sandbox/ and workspace/ cannot be worked in — repair at boot, not on first use
    _repaired = managers.repair_layout()

    from core import version as version_mod

    app = FastAPI(title="Athena Multiplexer", version=version_mod.text())

    # the door: every request is classified, and local-only paths are refused to anyone off the loopback
    from core import netdefense as net_mod

    net_settings = (cfg.get("security") or {}).get("network") or {}
    api_token = str(net_settings.get("api_token") or "") or net_mod.ensure_token(agents_dir.parent)
    app.add_middleware(
        net_mod.InboundGuard,
        ledger=ledger,
        loopback_only=bool(net_settings.get("loopback_only", True)),
        rate_per_minute=int(net_settings.get("rate_per_minute") or 240),
        token=api_token,
        require_token=bool(net_settings.get("require_token", True)),
    )
    app.state.cfg = cfg
    app.state.managers = managers
    app.state.provider = provider
    app.state.tools = tools

    def manager_root(channel: str):
        """The profile directory for a ref — by label, alias or `id:<uuid>`. Creates nothing.

        Resolution only: a name that does not exist raises, and a new lane is made by `managers.create`.
        """
        return identity_mod.root_for(agents_dir, channel)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": version_mod.text(), "channel": version_mod.channel(),
                "provider": provider.name,
                "model": (cfg.get("model") or {}).get("name"),
                "window": (cfg.get("model") or {}).get("window"),
                "warnings": cfg.get("_warnings") or [],
                # profiles whose sandbox/ or workspace/ had to be created at boot
                "layout_repaired": _repaired,
                # whether provider keys are held by this process (direct) or by the sidecar
                "credentials": cred_state}

    @app.get("/config")
    def config() -> dict:
        """The effective configuration, with anything secret removed.

        Read-only: the config file is the interface (documentation/architecture/08-config.md), so the
        page shows what is actually in force — merged defaults plus the operator's overrides — rather
        than inviting a second place to change it.
        """
        def redact(value):
            """Strip secret-shaped keys at EVERY depth.

            Only `model` was cleaned, so the moment an operator pinned `security.network.api_token` in config.yaml
            — the documented way to pin it — this route served the server's own token to any client holding it,
            and to the very UI it is meant to gate.
            """
            if isinstance(value, dict):
                return {k: redact(v) for k, v in value.items()
                        if not (isinstance(k, str) and any(word in k.lower()
                                                           for word in ("api_key", "token", "secret", "password",
                                                                        "private_key", "passphrase")))}
            if isinstance(value, list):
                return [redact(v) for v in value]
            return value

        safe = redact({k: v for k, v in cfg.items() if not k.startswith("_")})
        from models import store as model_store
        return {
            "config": safe,
            "paths": {k: str(v) for k, v in loader.paths(cfg).items()},
            "models_dir": str(model_store.models_dir()),
            "installed_models": model_store.list_models(),
            "warnings": cfg.get("_warnings") or [],
        }

    @app.get("/agents/{channel}/instructions")
    def instructions(channel: str) -> dict:
        """The instructions this agent would be given on its NEXT call, roster included.

        `system_prompt` on /resources shows what the last turn was told; this shows what the next turn
        will be told, which is the only way to check the roster before an agent has spoken.
        """
        channel_obj = managers.channel(channel)
        live = managers.instructions_for(channel_obj)
        return {"channel": channel, "instructions": live, "roster": managers.roster(channel),
                "chars": len(live)}

    @app.get("/agents/{channel}/profile")
    def get_profile(channel: str) -> dict:
        """This agent's own config, with the key reduced to the NAME of its .secret line."""
        root = manager_root(channel)
        cfg = agentconfig.read(root)
        model = dict(cfg.get("model") or {})
        return {"channel": channel, "configured": bool(model.get("name") or model.get("base_url")
                                                       or agentconfig.local_model_name(model)),
                "persona": cfg.get("persona") or {},
                "model": model,
                "api_key_name": cfg.get("api_key") or "",
                "has_key": bool(cfg.get("api_key") and providers_base.api_key(
                    str(model.get("provider") or "openai"), str(cfg.get("api_key") or ""))),
                "inherits_global": not bool(model.get("name") or model.get("base_url")
                                            or agentconfig.local_model_name(model))}

    @app.post("/agents/{channel}/profile")
    def save_profile(channel: str, setup: AgentSetup) -> dict:
        """Create or update one agent: persona, provider, credentials, model — in that order.

        Validation is here rather than in the wizard because the wizard is one client of this endpoint:
        a channel name must be addressable, a remote provider needs a URL and a model, and a local one must
        name a model that is actually downloaded. A key already on file is reused when none is posted, so
        re-saving a persona does not require re-entering the secret.
        """
        if not identity_mod.label_ok(channel):
            raise HTTPException(status_code=400,
                                detail="agent name must be letters, digits, - or _ (1-64 characters)")
        try:
            root = identity_mod.root_for(agents_dir, channel)
        except identity_mod.UnknownAgent:
            # a save for a name that does not exist yet is how the wizard creates one; `create` validates the label
            # and mints the id, and refuses a directory that exists without one
            managers.create(channel)
            root = identity_mod.root_for(agents_dir, channel)

        provider = (setup.model.provider or "openai").lower()
        if provider not in ("openai", "anthropic", "local"):
            raise HTTPException(status_code=400,
                                detail=f"provider {provider!r} — use openai, anthropic or local")

        model = setup.model.model_dump()
        if provider == "local":
            if not model["local"]:
                raise HTTPException(status_code=400, detail="a local model needs its folder name")
            from models import store as model_store
            installed = {m["name"] for m in model_store.list_models()}
            if model["local"] not in installed:
                raise HTTPException(status_code=400,
                                    detail=f"model {model['local']!r} is not in models/ — "
                                           f"available: {', '.join(sorted(installed)) or 'none'}")
            model["name"] = model["name"] or model["local"]
        else:
            if not model["base_url"].strip():
                raise HTTPException(status_code=400, detail="provider needs a base URL")
            if not model["name"].strip():
                raise HTTPException(status_code=400, detail="provider needs a model name")

        root = manager_root(channel)
        existing = agentconfig.read(root)
        key_name = ""
        if provider != "local":
            # The variable is named after the ENDPOINT, not the agent: `DEEPSEEK_API_KEY` is what a human
            # would export, and two agents on the same endpoint share it rather than duplicating a secret.
            derived = providers_base.secret_name(provider, model["base_url"])
            key_name = str(existing.get("api_key") or derived)
            has_any = bool(providers_base.api_key(provider, key_name)
                           or providers_base.api_key(provider, derived))
            if setup.api_key.strip():
                providers_base.write_secret(key_name, setup.api_key)
            elif not has_any:
                raise HTTPException(status_code=400,
                                    detail=f"an API key is required for this provider "
                                           f"(it will be stored as {key_name} in .athena/.secret)")
            else:
                key_name = key_name if providers_base.api_key(provider, key_name) else derived

        profile_mod.ensure(root)
        # a brand-new profile gets a colour chosen against the ones already in use; an existing one keeps
        # the colour it was born with (agentconfig.write only assigns when nothing is stored)
        taken = [agentconfig.read(agents_dir / other).get("accent", "")
                 for other in managers.profiles() if other != channel]
        accent = existing.get("accent") or ""
        agentconfig.write(root, {"persona": setup.persona.model_dump(), "model": model,
                                 "api_key": key_name,
                                 **({"accent": agentconfig.random_accent(
                                     avoid=[c for c in taken if c])} if not accent else {})})
        agentconfig.write_identity(root, setup.persona.model_dump())
        managers.forget_provider(channel)

        return {"channel": channel, "saved": True, "provider": provider,
                "model": model.get("name"), "api_key_name": key_name,
                "wrote": ["config.yaml", "assistant/ASSISTANT.md"] +
                         (["~/.athena/.secret"] if setup.api_key.strip() else [])}

    @app.post("/agents")
    def create_agent(req: NewAgent) -> dict:
        try:
            return managers.create(req.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/agents/{channel}")
    def remove_agent(channel: str) -> dict:
        try:
            return managers.remove(channel)
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"no agent named {channel!r}") from exc

    @app.post("/agents/{channel}/attach")
    async def attach(channel: str, request: Request) -> dict:
        """Accept one file into the channel's workspace and return where it landed.

        Raw body plus an `X-Filename` header rather than multipart: the agent's venv is deliberately two
        libraries, and a file upload does not justify adding a third. The bytes are written under
        `profiles/<channel>/workspace/uploads/<date>/`, the only tree the `read_file` tool will open, so an
        attachment is something the agent can actually use rather than a chip in the interface.
        """
        # The ref is resolved before any path is built, so `..` cannot address a directory outside the lane.
        # because `agents_dir / ".." / "workspace"` is a real path outside every profile
        try:
            channel = identity_mod.resolve(agents_dir, channel)["label"]
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=f"no agent named {channel!r}") from exc
        name = unquote(request.headers.get("x-filename") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="X-Filename header is required")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._") or "file"
        cap = int((cfg.get("tools") or {}).get("max_upload_bytes") or 20 * 1024 * 1024)
        # refuse on the declared length BEFORE buffering: reading first meant a 20 MB cap protected nothing
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > cap:
            raise HTTPException(status_code=413, detail=f"file is {declared} bytes; the cap is {cap}")
        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="empty body")
        if len(body) > cap:
            raise HTTPException(status_code=413, detail=f"file is {len(body)} bytes; the cap is {cap}")

        folder = agents_dir / channel / "workspace" / "uploads" / _time.strftime("%Y-%m-%d", _time.gmtime())
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / safe
        if target.exists():
            stem, suffix = target.stem, target.suffix
            target = folder / f"{stem}-{int(_time.time())}{suffix}"
        target.write_bytes(body)
        return {"channel": channel, "name": safe, "path": str(target), "bytes": len(body)}

    @app.post("/agents/{channel}/sleep")
    def sleep(channel: str) -> dict:
        """Park a channel: no work accepted until it is woken."""
        try:
            return managers.sleep(channel)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/agents/{channel}/wake")
    def wake(channel: str) -> dict:
        """Wake a channel and verify it: files, provider, model. Returns the checks either way."""
        result = managers.wake(channel)
        if not result["awake"]:
            failed = {k: v["detail"] for k, v in result["checks"].items() if not v["ok"]}
            result["detail"] = "wake failed: " + "; ".join(f"{k}: {v}" for k, v in failed.items())
        return result

    @app.post("/agents/{channel}/ask/{target}")
    def ask(channel: str, target: str, req: AskRequest) -> dict:
        """Turn 1 of the three-pass: queue a question on `target` authored by `channel`.

        The answer comes back as a queued turn on `channel` (turn 3), so the caller is never blocked waiting
        on another agent — and a loop of agents cannot deadlock, because every step is a queue entry.
        """
        known = managers.profiles()
        if channel not in known:
            raise HTTPException(status_code=404, detail=f"no agent named {channel!r}")
        if target not in known:
            raise HTTPException(status_code=404, detail=f"no agent named {target!r}")
        if target == channel:
            raise HTTPException(status_code=400, detail="an agent cannot ask itself")
        try:
            queued = managers.enqueue(target, text=req.text, author=channel, hops=0, reply_to=channel)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**queued, "exchanges": [
            {"turn": 1, "who": channel, "does": f"asks @{target}: {req.text[:120]}"},
            {"turn": 2, "who": target, "does": "answers in its own context"},
            {"turn": 3, "who": channel, "does": f"receives @{target}'s answer as a queued turn"},
        ]}

    @app.get("/metrics")
    def metrics_summary(limit: int | None = None, channel: str | None = None):
        # a ref, not a directory name — otherwise `?channel=id:<uuid>` reads an empty profile
        """Turn measurements: totals, per channel and per model. Read-only; the log is the truth.

        `?channel=scout` scopes it to that agent's own `profiles/scout/metrics/turns.jsonl`.
        """
        from core import metrics as metrics_mod
        return metrics_mod.summary(managers.agents_dir, limit=limit, channel=channel or "")

    @app.get("/metrics/turns")
    def metrics_turns(limit: int = 50, channel: str | None = None):
        from core import metrics as metrics_mod
        return {"turns": metrics_mod.read(managers.agents_dir, limit=limit, channel=channel or "")}

    @app.get("/agents/{channel}/metrics")
    def agent_metrics(channel: str, limit: int | None = None):
        """One agent's measurements, from its profile directory."""
        from core import metrics as metrics_mod

        label = identity_mod.resolve(managers.agents_dir, channel)["label"]
        return metrics_mod.summary(managers.agents_dir, limit=limit, channel=label)

    from core import identity as identity_mod

    @app.exception_handler(identity_mod.UnknownAgent)
    def _unknown_agent(request: Request, exc: identity_mod.UnknownAgent):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(identity_mod.Conflict)
    def _identity_conflict(request: Request, exc: identity_mod.Conflict):
        # IdentityMissing / IdentityInvalid / IdentityDuplicate / IdentityExists: 409, and the message names the fix
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/agents")
    def agents() -> dict:
        # `channels` repeats the same rows under the name the page reads.
        # already reads it, because a rename here would break the page that shows agents as "channels".
        rows = managers.discover()
        offered = tools.names() if getattr(managers, "tools_enabled", True) else []
        return {"agents": rows, "channels": rows, "tools": offered}

    @app.post("/agents/{channel}/turn")
    def turn(channel: str, req: TurnRequest) -> dict:
        if not channel.replace("-", "").replace("_", "").isalnum():
            raise HTTPException(status_code=400, detail="channel name must be alphanumeric, - or _")
        wait = float((cfg.get("agents") or {}).get("turn_wait_s") or 120)
        try:
            return managers.submit(channel, req.text, wait_s=wait)
        except channels_mod.ChannelAsleep as exc:
            # a state conflict, not a rate limit: the caller must wake the channel
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    @app.get("/agents/{channel}/messages")
    def messages(channel: str, limit: int = 200) -> dict:
        """The channel's transcript, newest last — what the web thread renders.

        Read-only and bounded: a channel that has run for a week must not hand a browser 50k messages, so
        the caller asks for a window and the session does the slicing.
        """
        if not channel.replace("-", "").replace("_", "").isalnum():
            raise HTTPException(status_code=400, detail="channel name must be alphanumeric, - or _")
        channel_obj = managers.channel(channel)
        limit = max(1, min(int(limit), 500))
        history = channel_obj.session.history()[-limit:]
        out = []
        for msg in history:
            content = msg.get("content") or ""
            role = msg.get("role")
            # Inbound/outbound are stated from the AGENT's side, because the pane is its thread:
            #   outbound = this agent speaking        (left)
            #   inbound  = arriving at this agent      (right): the operator, a peer, a tool result
            direction = "outbound" if role == "assistant" else "inbound"
            author = ""
            marker = "[from @"
            if content.startswith(marker) and "] " in content:
                author, _, rest = content[len(marker):].partition("] ")
                content = rest
            entry = {"role": role, "content": content, "ts": msg.get("ts"),
                     "direction": direction, "author": author}
            if role == "tool":
                # a tool result is inbound to the agent, and naming the tool is the difference between
                # "something came back" and "echo came back"
                entry["tool_name"] = msg.get("name") or ""
            if msg.get("tool_calls"):
                entry["tool_calls"] = [c.get("name") for c in msg["tool_calls"]]
            if msg.get("name"):
                entry["name"] = msg["name"]
            out.append(entry)
        return {"channel": channel, "messages": out, "turns": channel_obj.turns,
                "total": channel_obj.session.size()}

    # -- install requests --------------------------------------------------
    @app.get("/installs")
    def installs_pending() -> dict:
        """Compatibility view: the install-shaped asks from the one permissions queue."""
        kinds = {"install", "tool", "box"}
        return {"pending": [row for row in managers.permissions.pending() if row.get("kind") in kinds]}

    @app.post("/installs/{rid}")
    def installs_answer(rid: str, req: InstallRequest) -> dict:
        """Compatibility route: answered through the same manager, so both surfaces agree about what happened."""
        from core.permissions import PermissionError_

        try:
            return managers.permissions.resolve(rid, req.decision, "once")
        except PermissionError_ as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # -- security ----------------------------------------------------------
    @app.get("/security")
    def security_status() -> dict:
        """What the watchers see right now: counts per team, and whether a baseline exists."""
        findings = watch.sweep() if watch is not None else {
            "findings": security_mod.check(agents_dir.parent, security_folder, ledger),
            "counts": {"red": 0, "blue": 0}}
        rows = findings.get("findings") if isinstance(findings, dict) else findings
        baseline = security_mod.Baseline(agents_dir.parent, security_folder).load()
        return {"enabled": watch is not None,
                "baseline_files": len(baseline.get("files") or {}),
                "counts": {"red": sum(1 for f in rows if f["team"] == "red"),
                           "blue": sum(1 for f in rows if f["team"] == "blue")},
                "findings": rows[:50]}

    @app.get("/security/report")
    def security_report_text() -> Response:
        path = security_folder / security_mod.REPORT_NAME
        if not path.is_file() and watch is not None:
            watch.sweep()
        body = path.read_text(encoding="utf-8") if path.is_file() else "no report yet"
        return Response(content=body, media_type="text/markdown")

    # -- permissions: the one queue ----------------------------------------
    @app.get("/permissions")
    def permissions_pending() -> dict:
        """Everything waiting on a person: path asks, install requests, and anything else that registers."""
        return {"pending": managers.permissions.pending(),
                "answered": managers.permissions.answered(10),
                "policy": managers.permissions.policy()}

    @app.post("/permissions/_revoke")
    def permissions_revoke(agent: str = "", kind: str = "") -> dict:
        return managers.permissions.revoke(agent or None, kind or None)

    @app.post("/permissions/{rid}")
    def permissions_answer(rid: str, req: ApprovalRequest) -> dict:
        from core.permissions import PermissionError_

        try:
            return managers.permissions.resolve(rid, req.decision, req.scope)
        except PermissionError_ as exc:
            # 409 for "not pending / expired / not an action", so the UI stops showing a dead card
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # -- approvals ---------------------------------------------------------
    @app.get("/approvals")
    def approvals_pending() -> dict:            # kept: older pages poll this
        """What is waiting for the user: agent, path, operation, tool and the reason to judge it by."""
        return {"pending": [row for row in managers.permissions.pending() if row.get("kind") == "path"]}

    @app.post("/approvals/_probe")
    def approvals_probe(req: ApprovalProbe) -> dict:
        """Ask the broker the way a tool would, so the popup can be exercised without a live agent.

        Development only: `ATHENA_DEV=1` or `tools.dev_probe: true`. It grants nothing by itself — it creates a
        pending request exactly like a real refusal, which then has to be answered through the normal endpoint.
        """
        enabled = os.environ.get("ATHENA_DEV") == "1" or bool((cfg.get("tools") or {}).get("dev_probe"))
        if not enabled:
            raise HTTPException(status_code=403, detail="the approval probe is disabled (set ATHENA_DEV=1)")
        import threading

        broker = managers.approvals

        def ask():
            broker.request(agent=req.agent, path=req.path, op=req.op, reason=req.reason, tool=req.tool,
                           flags=())

        threading.Thread(target=ask, daemon=True).start()
        for _ in range(50):
            if broker.pending():
                return {"probe": True, "pending": broker.pending()[0]["id"]}
            _time.sleep(0.02)
        return {"probe": True, "pending": None}

    @app.post("/approvals/{rid}")
    def approvals_answer(rid: str, req: ApprovalRequest) -> dict:
        """Allow or deny, for once / this session / permanently."""
        try:
            return managers.approvals.resolve(rid, req.decision, req.scope)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="no such pending approval") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # -- provider registry -------------------------------------------------
    @app.get("/providers")
    def providers_list() -> dict:
        """Providers and the model ids they serve. Key **names** only — never a value."""
        from core import providerregistry as registry
        return {"registry": str(registry.path()), "providers": registry.load()}

    @app.post("/providers/{name}/keys")
    def providers_add_key(name: str, req: KeyRequest) -> dict:
        """Store a key in the right `.secret` and record it against this provider.

        `scope: global` → `~/.athena/.secret` (every agent may use it).
        `scope: local`  → `~/.athena/profiles/<agent>/.secret` (that agent only, and it shadows the global one).
        The value goes into the file with mode 0600 and is never echoed back.
        """
        from core import providerregistry as registry
        scope = (req.scope or "global").lower()
        if scope not in ("global", "local"):
            raise HTTPException(status_code=400, detail="scope must be 'global' or 'local'")
        root = None
        if scope == "local":
            if not req.agent or not identity_mod.ref_ok(req.agent):
                raise HTTPException(status_code=400,
                                    detail="a local key needs the lane it belongs to (a label or id:<uuid>)")
            root = identity_mod.root_for(agents_dir, req.agent)   # 404 comes from the handler above
        name_upper = (req.key_name or "").strip().upper()
        if req.value:
            try:
                providers_base.write_secret(req.key_name, req.value, root=root)
            except Exception as exc:                  # noqa: BLE001 — a name error is a 400
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        elif name_upper not in providers_base.read_secrets(root) and name_upper not in providers_base.read_secrets():
            # no value and no existing line: registering a key that does not exist would make the registry lie
            raise HTTPException(status_code=400,
                                detail=f"{name_upper or 'that key'} is not in "
                                       f"{providers_base.secret_path(root)} — send a value to store it")
        entry = registry.register(name, format=req.format or "openai", base_url=req.base_url or "",
                                  key_name=req.key_name, scope=scope, agent=req.agent or "")
        return {"provider": name, "key_name": req.key_name.upper(), "scope": scope,
                "agent": req.agent or "", "stored_in": str(providers_base.secret_path(root)),
                "keys": entry.get("keys")}

    @app.post("/providers/{name}/models")
    def providers_refresh_models(name: str, req: KeyRequest | None = None) -> dict:
        """Fetch the models this provider actually serves and record them.

        Uses the key that resolves for the provider (global `.secret`, then environment) unless one is supplied
        in the body — so the list is always what the endpoint returned, never a guess.
        """
        from core import providerregistry as registry
        entry = registry.load().get(name.lower()) or {}
        base_url = (req.base_url if req else "") or entry.get("base_url") or ""
        # the key resolves by FORMAT plus the endpoint's conventional name (`DEEPSEEK_API_KEY`), not by the
        # provider's own label — `api_key("deepseek")` finds nothing because the file line is named for the host
        kind = (entry.get("format") or "openai").lower()
        conventional = providers_base.secret_name(name, base_url)
        key = (req.value if req else "") or providers_base.api_key(kind, conventional)
        found = registry.fetch_models(base_url, key)
        if not found:
            raise HTTPException(status_code=502,
                                detail=f"{name} returned no model list (check the base URL and key)")
        registry.set_models(name, found)
        return {"provider": name.lower(), "models": found, "count": len(found)}

    @app.get("/agents/{channel}/history")
    def history(channel: str, days: int = 3, before: str | None = None) -> dict:
        """The whole thread, bucketed by day, newest page first.

        `LATEST.jsonl` is only what the model is *sent*; the rest of the conversation lives in compression
        snapshots. The page shows both: scrolling up asks for `before=<oldest date loaded>` and gets the next
        older days, so the thread can be read back to the beginning without ever loading it all at once.

        Each message carries `live: true` when it is part of the window the agent currently receives, which is
        what lets the interface draw the boundary between "in context" and "archived".
        """
        if not channel.replace("-", "_").replace("_", "").isalnum():
            raise HTTPException(status_code=400, detail="channel name must be alphanumeric, - or _")
        channel_obj = managers.channel(channel)
        rows = channel_obj.session.history_all()
        live = channel_obj.session.live_keys()

        buckets: dict[str, list[dict]] = {}
        for msg in rows:
            date = str(msg.get("ts") or "")[:10] or "unknown"
            if before and date >= before:
                continue
            content = msg.get("content") or ""
            role = msg.get("role")
            author = ""
            marker = "[from @"
            if content.startswith(marker) and "] " in content:
                author, _, content = content[len(marker):].partition("] ")
            buckets.setdefault(date, []).append({
                "role": role, "content": content, "ts": msg.get("ts"),
                "direction": "outbound" if role == "assistant" else "inbound",
                "author": author,
                "live": (msg.get("ts"), role, (msg.get("content") or "")[:200]) in live,
            })

        dates = sorted(buckets)
        page = dates[-max(1, min(days, 31)):] if dates else []
        return {
            "channel": channel,
            "days": [{"date": d, "messages": buckets[d],
                      "live": any(m["live"] for m in buckets[d]),
                      "count": len(buckets[d])} for d in page],
            "oldest_loaded": page[0] if page else None,
            "has_more": bool(dates and page and dates.index(page[0]) > 0),
            "total_days": len(dates),
            "total_messages": len(rows),
        }

    @app.get("/agents/{channel}/resources")
    def resources(channel: str) -> dict:
        return managers.resources(channel)

    @app.post("/agents/{channel}/clear")
    def clear(channel: str) -> dict:
        managers.channel(channel).session.clear(archive=True)
        return {"channel": channel, "cleared": True}

    # -- the website -------------------------------------------------------
    # Mounted LAST so the API routes above win: the site is served at "/", and a mount at the root
    # swallows anything unregistered, which is the behaviour we want for a browser and not for /health.
    here = Path(__file__).resolve().parent.parent

    # Order matters: a mount at "/" swallows every path registered after it, so the narrower mount
    # goes first. (It did not, and /docs/* answered 404 from inside the site directory.)
    docs = here.parent / "documentation"
    if docs.is_dir():
        app.mount("/docs", RevalidatingStatic(directory=str(docs), html=False), name="docs")

    web = here / "web"
    if web.is_dir():
        app.mount("/", RevalidatingStatic(directory=str(web), html=True), name="site")

    return app

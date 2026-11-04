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

import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
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
    first_name: str = Field("", max_length=80)
    last_name: str = Field("", max_length=80)
    nickname: str = Field("", max_length=80)
    role: str = Field("", max_length=600)
    tone: str = Field("", max_length=600)
    boundaries: str = Field("", max_length=600)
    likes: str = Field("", max_length=600)
    dislikes: str = Field("", max_length=600)
    bio: str = Field("", max_length=2000)
    birth_date: str = Field("", max_length=10)
    accent: str = Field("", max_length=40)


class AgentModel(BaseModel):
    # Where this agent's answers come from. `local` names a folder under .athena/models.
    provider: str = Field("openai", max_length=20)
    base_url: str = Field("", max_length=300)
    name: str = Field("", max_length=200)
    window: int = Field(32768, ge=32768)
    # the floor a lane falls back to when the provider publishes nothing; the provider's own figure wins when it does
    context_window_local: int | None = Field(None, ge=32768)
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

    # Collected once and written to a .secret; never stored in the profile and never returned.
    api_key: str = Field("", max_length=400)

    # global keeps the credential in the installation's .secret; local gives this agent its own file beside its profile
    key_scope: str = Field("global", pattern="^(global|local)$")


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

    from core import context_window as window_mod

    @app.get("/diagnostics")
    async def diagnostics(limit: int = 200):
        """Turn outcomes folded into the three levels: what finished slowly, what is stuck, what failed."""
        from core import metrics as metrics_mod
        from core import outcomes as outcomes_mod
        # the install root is two levels above this file: <root>/athena-agent/server/app.py
        from pathlib import Path as _Path
        base = _Path(__file__).resolve().parents[2]
        rows = metrics_mod.read(base, limit=limit) or []
        try:
            slow = float((cfg.get("metrics") or {}).get("slow_turn_s") or 60.0)
        except Exception:
            slow = 60.0
        return outcomes_mod.diagnostics(rows, slow_s=slow)


    @app.get("/console/history")
    async def console_history(page: int = 1, size: int = 128):
        """Whole console history, dated, 128 lines to a page."""
        from server import console as console_mod
        return console_mod.history(page=page, size=size)

    @app.get("/console")
    async def console_tail(since: int = 0, limit: int = 200):
        """The process's own log, from a cursor: the terminal overlay reads this instead of guessing."""
        from server import console as console_mod
        console_mod.install()
        rows = console_mod.recent(since, limit)
        return {"rows": rows, "cursor": rows[-1]["seq"] if rows else since}

    def _user_file(channel: str = ""):
        """The person's own details, kept with the agent they are talking to.

        Each profile gets its own copy, so what one agent knows about you is not automatically what another knows;
        with no channel given the installation-wide file is used.
        """
        if channel:
            try:
                return manager_root(channel) / "user.json"
            except (KeyError, ValueError):
                raise HTTPException(status_code=404, detail=f"no such agent: {channel}") from None
        return agents_dir.parent / "user.json"

    def _user_avatars() -> Path:
        folder = agents_dir.parent / "user" / "avatars"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    @app.get("/user/avatars")
    def list_user_avatars() -> dict:
        folder = _user_avatars()
        numbers = []
        for existing in folder.glob("avatar-0*.png"):
            try:
                numbers.append(int(existing.stem.split("-", 1)[1]))
            except (ValueError, IndexError):
                continue
        numbers.sort()
        selected = numbers[-1] if numbers else None
        marker = folder / "selected"
        try:
            selected = int(marker.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
        return {"numbers": numbers, "selected": selected}

    @app.get("/user/avatar")
    def get_user_avatar(n: int | None = None):
        folder = _user_avatars()
        candidates = []
        for existing in folder.glob("avatar-0*.png"):
            try:
                candidates.append((int(existing.stem.split("-", 1)[1]), existing))
            except (ValueError, IndexError):
                continue
        if not candidates:
            raise HTTPException(status_code=404, detail="no avatar")
        if n is None:
            try:
                n = int((folder / "selected").read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pass
        if n is not None:
            for number, path in candidates:
                if number == n:
                    return FileResponse(str(path), media_type="image/png")
        return FileResponse(str(max(candidates)[1]), media_type="image/png")

    @app.post("/user/avatar")
    def put_user_avatar(payload: dict) -> dict:
        import base64
        import binascii
        data = str(payload.get("data") or "")
        if not data.startswith("data:image/png;base64,"):
            raise HTTPException(status_code=400, detail="expected a PNG data URL")
        blob = data.split(",", 1)[1]
        if len(blob) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="avatar is too large")
        try:
            raw = base64.b64decode(blob, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="avatar is not valid base64") from None
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(status_code=400, detail="not a PNG")
        folder = _user_avatars()
        numbers = []
        for existing in folder.glob("avatar-0*.png"):
            try:
                numbers.append(int(existing.stem.split("-", 1)[1]))
            except (ValueError, IndexError):
                continue
        number = (max(numbers) + 1) if numbers else 1
        target = folder / f"avatar-0{number}.png"
        tmp = folder / (target.name + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(target)
        (folder / "selected").write_text(str(number), encoding="utf-8")
        return {"saved": True, "file": target.name, "count": number}

    @app.get("/user/profile")
    def get_user_profile(channel: str = "") -> dict:
        """What the operator has told this agent about themselves. Absent fields stay absent."""
        import json as _json
        target = _user_file(channel)
        if not target.exists():
            return {"user": {}, "channel": channel, "inherited": False}
        try:
            data = _json.loads(target.read_text(encoding="utf-8")) or {}
            return {"user": data, "channel": channel, "inherited": False}
        except (OSError, ValueError):
            return {"user": {}, "channel": channel, "inherited": False}

    @app.post("/user/profile")
    def put_user_profile(payload: dict, channel: str = "") -> dict:
        """Store the operator's own details. Written whole and atomically: a half-written profile is worse than none."""
        import json as _json
        allowed = ("name", "handle", "email", "timezone", "location", "pronouns", "notes", "accent")
        clean = {}
        for key in allowed:
            value = payload.get(key)
            if value is None:
                continue
            clean[key] = str(value)[:400]
        target = _user_file(channel)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(_json.dumps(clean, indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
        return {"saved": True, "keys": sorted(clean)}

    @app.get("/health")
    def health() -> dict:
        try:
            window_facts = window_mod.resolve(cfg.get("model") or {})
        except ValueError as exc:
            window_facts = {"window": window_mod.FLOOR, "source": "config", "local": window_mod.FLOOR,
                            "provider": None, "error": str(exc)}
        return {"status": "ok", "version": version_mod.text(), "channel": version_mod.channel(),
                "provider": provider.name,
                "model": (cfg.get("model") or {}).get("name"),
                "window": window_facts["window"], "window_source": window_facts["source"],
                "context_window_local": window_facts["local"],
                "context_window_provider": window_facts["provider"],
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
            # A key is secret by NAME, not by containing a word: matching any key that merely contains "token"
            # also removed `model.max_output_tokens`, a setting the settings page displays.
            def is_secret(name: str) -> bool:
                key = name.lower()
                return key in {"api_key", "apikey", "token", "secret", "password", "passwd", "private_key",
                               "passphrase", "api_token", "auth_token", "access_token", "refresh_token"} or \
                       key.endswith(("_token", "_secret", "_password", "_api_key", "_passphrase"))

            if isinstance(value, dict):
                return {k: redact(v) for k, v in value.items() if not (isinstance(k, str) and is_secret(k))}
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

    @app.get("/agents/{channel}/secret-key")
    def reveal_secret(channel: str) -> dict:
        """Return this agent's own stored key, for the operator reading their own profile.

        Only the agent's own .secret entry is read: global credentials are chosen on the settings page, and an
        agent's key is not reachable through another lane. The route answers on loopback behind the same gate as
        every other one, and returns nothing when no key is configured.
        """
        root = manager_root(channel)
        cfg = agentconfig.read(root)
        cfg_model = cfg.get("model") or {}
        provider = str(cfg_model.get("provider") or "openai")
        name = str(cfg.get("api_key") or cfg_model.get("key_name") or ""
                   or providers_base.secret_name(provider, str(cfg_model.get("base_url") or "")))
        key = ""
        local_secret = (root / ".secret").exists()
        if name:
            try:
                key = (providers_base.api_key(provider, name, root=root if local_secret else None)
                       or providers_base.api_key(provider, name) or "")
            except Exception:
                key = ""
        if not key:
            raise HTTPException(status_code=404, detail="no key is stored for this agent")
        try:
            ledger.record("red", "key-reveal", f"secret {name} revealed for {channel}",
                          f"profiles/{channel}/.secret", channel)
        except Exception:
            pass
        return {"name": name, "key": key, "length": len(key)}

    @app.get("/agents/{channel}/avatars")
    def list_avatars(channel: str) -> dict:
        """The numbered avatars this agent has saved, newest last."""
        folder = manager_root(channel) / "avatars"
        numbers = []
        for existing in folder.glob("avatar-0*.png"):
            try:
                numbers.append(int(existing.stem.split("-", 1)[1]))
            except (ValueError, IndexError):
                continue
        numbers.sort()
        selected = None
        marker = folder / "selected"
        try:
            selected = int(marker.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            selected = numbers[-1] if numbers else None
        return {"channel": channel, "numbers": numbers, "selected": selected,
                "files": [f"avatar-0{n}.png" for n in numbers]}

    @app.post("/agents/{channel}/avatar/select")
    def select_avatar(channel: str, payload: dict) -> dict:
        """Remember which of this agent's avatars is in use."""
        folder = manager_root(channel) / "avatars"
        number = int(payload.get("n") or 0)
        if not (folder / f"avatar-0{number}.png").exists():
            raise HTTPException(status_code=404, detail=f"no avatar-0{number}.png")
        (folder / "selected").write_text(str(number), encoding="utf-8")
        return {"channel": channel, "selected": number}

    @app.get("/agents/{channel}/avatar")
    def get_avatar(channel: str, n: int | None = None):
        """This agent's avatar, or 404 when it has none."""
        folder = manager_root(channel) / "avatars"
        candidates = []
        for existing in folder.glob("avatar-0*.png"):
            try:
                candidates.append((int(existing.stem.split("-", 1)[1]), existing))
            except (ValueError, IndexError):
                continue
        if not candidates:
            raise HTTPException(status_code=404, detail="no avatar")
        if n is None:
            marker = folder / "selected"
            try:
                chosen = int(marker.read_text(encoding="utf-8").strip())
                if chosen:
                    n = chosen
            except (OSError, ValueError):
                pass
        if n is not None:
            for number, path in candidates:
                if number == n:
                    return FileResponse(str(path), media_type="image/png")
            raise HTTPException(status_code=404, detail=f"no avatar-0{n}.png")
        target = max(candidates)[1]
        return FileResponse(str(target), media_type="image/png")

    @app.post("/agents/{channel}/avatar")
    def put_avatar(channel: str, payload: dict) -> dict:
        """Store a 1024x1024 avatar inside the agent's own directory.

        The crop happens in the browser, where the pixels already are; the server refuses anything that is not a
        PNG of the expected size, so a malformed or oversized upload cannot become an unbounded file on disk.
        """
        import base64
        import binascii
        data = str(payload.get("data") or "")
        if not data.startswith("data:image/png;base64,"):
            raise HTTPException(status_code=400, detail="expected a PNG data URL")
        blob = data.split(",", 1)[1]
        if len(blob) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="avatar is too large")
        try:
            raw = base64.b64decode(blob, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="avatar is not valid base64") from None
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(status_code=400, detail="not a PNG")
        root_dir = manager_root(channel)
        folder = root_dir / "avatars"
        folder.mkdir(parents=True, exist_ok=True)
        numbers = []
        for existing in folder.glob("avatar-0*.png"):
            try:
                numbers.append(int(existing.stem.split("-", 1)[1]))
            except (ValueError, IndexError):
                continue
        number = (max(numbers) + 1) if numbers else 1
        target = folder / f"avatar-0{number}.png"
        tmp = folder / (target.name + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(target)
        return {"channel": channel, "path": str(target), "file": target.name,
                "bytes": len(raw), "count": number}

    @app.get("/agents/{channel}/profile")
    def get_profile(channel: str) -> dict:
        """This agent's own config, with the key reduced to the NAME of its .secret line."""
        root = manager_root(channel)
        cfg = agentconfig.read(root)
        model = dict(cfg.get("model") or {})
        provider_name = str(model.get("provider") or "openai")
        key_name = str(cfg.get("api_key") or model.get("key_name") or ""
                       or providers_base.secret_name(provider_name, str(model.get("base_url") or "")))
        local_secret = (root / ".secret").exists()
        scope = "local" if local_secret else "global"
        has_key = bool(key_name and (providers_base.api_key(provider_name, key_name, root=root if local_secret else None)
                                     or providers_base.api_key(provider_name, key_name)))
        return {"channel": channel, "configured": bool(model.get("name") or model.get("base_url")
                                                       or agentconfig.local_model_name(model)),
                "persona": cfg.get("persona") or {},
                "model": model,
                "api_key_name": key_name,
                "key_scope": scope,
                "has_key": has_key,
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
                providers_base.write_secret(key_name, setup.api_key,
                                            root=root if setup.key_scope == "local" else None)
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
                entry["tool_call_id"] = msg.get("tool_call_id") or ""
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

    @app.get("/security/events")
    def security_events(since: float | None = None, limit: int = 200,
                        within: int | None = None, min_level: str = "info") -> dict:
        """Ledger rows newer than `since`, oldest first, with the newest timestamp as the cursor.

        Read-only: the ledger is the record, and a reader that could write it would be able to erase the evidence of
        its own tampering. `since` is a unix timestamp; rows at or before it are skipped, so a caller can poll without
        re-reading the file's whole history.
        """
        from server import ledger_view

        try:
            return ledger_view.read_events(security_folder, since=since, limit=limit,
                                           within=within, min_level=min_level)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
        # the registry may know the provider under another label (config says `openai`, provider.json says
        # `deepseek`); fall back to the configured endpoint rather than refusing to ask
        base_url = ((req.base_url if req else "") or entry.get("base_url")
                    or (cfg.get("model") or {}).get("base_url") or "")
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
        # where the provider publishes a context window, record it: that figure, not the config floor, is what the
        # stack should be sized against. A provider that returns ids alone leaves this empty, by design.
        published: dict = {}
        try:
            published = registry.contexts_from_rows(registry.fetch_model_rows(base_url, key))
            if published:
                registry.set_contexts(name, published)
        except Exception:                            # noqa: BLE001 — never fail a model refresh over metadata
            published = {}
        # the published figure becomes the configured one only when it is for the model actually in use and at or
        # above the floor; otherwise the response says why not, and the config is left alone
        from core import context_window as window_mod

        decision = window_mod.adoption(cfg.get("model") or {}, published)
        if decision.get("adopt"):
            try:
                if window_mod.write_provider_window(agents_dir.parent / "config.yaml", decision["window"]):
                    decision["written"] = str(agents_dir.parent / "config.yaml")
            except OSError as exc:                    # noqa: BLE001 — a read-only config is not a failed refresh
                decision.update({"adopt": False, "reason": f"could not write config.yaml: {exc}"})
        return {"provider": name.lower(), "models": found, "count": len(found),
                "context_windows": published, "adopted": decision}

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


try:
    from server.console import install as _install_console

    _install_console()
    from server.console import tap_streams

    tap_streams()
except Exception:
    pass

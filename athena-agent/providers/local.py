"""Local models — a downloaded GGUF served as an OpenAI-compatible endpoint.

The whole point of this module is that it does **not** add a code path to the agent:

    models/<name>/*.gguf  →  start a server  →  http://127.0.0.1:<port>/v1  →  OpenAICompatProvider

Everything after the arrow is the ordinary `openai` format, so a local model behaves exactly like a
custom URL: same adapter, same turn loop, same tools, same budget. A "local provider" that spoke its
own dialect would fork the agent in two, and every future feature would have to be built twice.

Backends (config `model.local.backend`):

  llamacpp   `llama-server -m <gguf> --host 127.0.0.1 --port <port> -c <ctx>`
  ollama     import the GGUF with `ollama create`, then use the daemon's OpenAI endpoint
  command    an operator-supplied command template with {model} {port} {ctx}

If the port already answers, the server is **reused, never respawned** — that is what lets a one-shot
CLI turn run against a server the multiplexer already has warm.
"""
from __future__ import annotations

import atexit
import os
import json
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path

import httpx

from models import store as model_store
from providers.base import BaseProvider, ProviderError
from core.context_window import effective_window

# the tiered runtime lives beside this file and is executed by model.local.python
RUNTIME = Path(__file__).with_name("offload_runtime.py")

class LocalServer:
    """Owns one model server process: render the command, start it, wait for it, stop it."""

    def __init__(self, cfg: dict):
        model = cfg.get("model") or {}
        local = model.get("local") or {}
        self.cfg = cfg
        self.backend = str(local.get("backend") or "transformers").lower()
        self.port = int(local.get("port") or 8080)
        # the local server's own ctx must cover the window in force, not a stale model.window: with a provider
        # figure above the floor it would otherwise truncate at the old number
        self.ctx = int(local.get("ctx") or effective_window({"model": model}))
        self.name = str(local.get("name") or "")
        self.repo = str(local.get("repo") or "")
        self.file = str(local.get("file") or "")
        self.binary = str(local.get("binary") or "llama-server")
        self.python = str(local.get("python") or "python3")
        self.quant = str(local.get("quantization") or "")
        self.gpu_util = float(local.get("gpu_memory_utilization") or 0.90)
        self.command = str(local.get("command") or "")
        self.timeout = float(local.get("startup_timeout_s")
                             or (600 if (local.get("backend") or "").lower() == "vllm" else 180))
        self.log_path = Path(local.get("log") or (Path(os.environ.get("ATHENA_ROOT")
                                                       or (Path.home() / ".athena")) / "logs" / "model-server.log"))
        self.proc: subprocess.Popen | None = None
        self.base_url = ""

    # -- discovery ---------------------------------------------------------

    def model_path(self) -> Path:
        try:
            return model_store.resolve(self.name, self.repo, self.file)
        except RuntimeError as exc:
            raise ProviderError(str(exc)) from exc

    def served_name(self) -> str:
        """The model identifier the server will answer to."""
        if self.backend in ("vllm", "transformers"):
            return model_store.slug(self.repo, self.name) if (self.repo or self.name) else "athena"
        return self.name or (self.model_path().stem if self.repo or self.file else "")

    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    # -- process -----------------------------------------------------------

    def render(self) -> list[str]:
        """The command that starts this backend. Exposed so the CLI can print it instead of guessing."""
        if self.backend == "transformers":
            return [self.python, str(RUNTIME), "--model", str(self.model_path()),
                    "--plan", str(self.write_plan()), "--port", str(self.port),
                    "--host", "127.0.0.1", "--served-name", self.served_name()]
        if self.backend == "transformers":
            # the tiered runtime: Athena decides the placement, torch executes it
            return [self.python, str(RUNTIME), "--model", str(self.model_path()),
                    "--plan", str(self.write_plan()), "--port", str(self.port),
                    "--host", "127.0.0.1", "--served-name", self.served_name()]
        if self.backend == "vllm":
            # The model as its authors shipped it: no quant file, no conversion step. vLLM loads the
            # safetensors directory and serves the same OpenAI API every other backend serves.
            args = [self.python, "-m", "vllm.entrypoints.openai.api_server",
                    "--model", str(self.model_path()),
                    "--served-model-name", self.served_name(),
                    "--host", "127.0.0.1", "--port", str(self.port),
                    "--max-model-len", str(self.ctx),
                    "--gpu-memory-utilization", str(self.gpu_util)]
            if self.quant:
                args += ["--quantization", self.quant]
            return args
        if self.backend == "command":
            if not self.command:
                raise ProviderError("model.local.command is empty (backend: command)")
            model = str(self.model_path())
            return shlex.split(self.command.format(model=model, port=self.port, ctx=self.ctx))
        model = str(self.model_path())
        # --jinja applies the chat template stored in the GGUF. Without it llama-server falls back to a
        # plain prompt continuation, which is how a model ends up answering "hello" with source code.
        return [self.binary, "-m", model, "--jinja",
                "--host", "127.0.0.1", "--port", str(self.port), "-c", str(self.ctx)]

    def write_plan(self) -> Path:
        """Compute the GPU/CPU/disk placement and persist it for the runtime.

        Written here rather than in the runtime because the *policy* belongs to Athena and the
        *execution* belongs to torch: the split stays testable without loading a model.
        """
        from models import tiering
        plan = tiering.plan(self.model_path(), self.ctx,
                            gpu_fill=float((self.cfg.get("model", {}).get("local", {}) or {})
                                           .get("gpu_memory_utilization") or 0.90),
                            ram_fill=float((self.cfg.get("model", {}).get("local", {}) or {})
                                           .get("ram_fill") or 0.75),
                            dtype_bytes=2 if str(self.quant) != "fp32" else 4)
        target = self.log_path.parent / f"plan-{self.served_name()}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        print(tiering.describe(plan), file=__import__("sys").stderr)
        return target

    def _health_info(self) -> dict:
        base = self.endpoint().rsplit("/v1", 1)[0]
        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(base + "/health")
            if resp.status_code >= 400:
                return {}
            return resp.json() or {}
        except (httpx.HTTPError, ValueError):
            return {}

    def serves_another_model(self) -> bool:
        """Is the warm server on this port answering as a DIFFERENT model?

        Reuse is keyed on the port, but a port serves one model. Two agents with different local models
        and the same `model.local.port` would have the second one silently talk to the first one's
        model — a wrong answer with no error, which is the worst failure mode available. Compare the
        served name and refuse instead.
        """
        if self.backend != "transformers":
            return False
        info = self._health_info()
        if not info:
            return False
        serves = str(info.get("served_name") or "")
        mine = self.served_name()
        return bool(serves) and bool(mine) and serves != mine

    def is_stale(self) -> bool:
        """Is the warm runtime running code older than the file on disk?

A running runtime answers with the code it loaded, so a changed file has no effect until the runtime is
restarted. The runtime is therefore replaced on a change rather than reused."""
        info = self._health_info()
        if not info:
            return False                       # not up, or not answering: nothing to compare
        started = float(info.get("runtime_mtime") or 0)
        if not started:
            # a runtime that cannot say when it started predates the ability to — always stale
            return True
        try:
            on_disk = RUNTIME.stat().st_mtime
        except OSError:
            return False
        return on_disk > started + 0.5

    def _pid_on_port(self) -> int:
        """Who is listening on our port, when the server cannot tell us itself."""
        try:
            out = subprocess.run(["ss", "-ltnpH", f"sport = :{self.port}"],
                                 capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return 0
        match = re.search(r"pid=(\d+)", out)
        return int(match.group(1)) if match else 0

    def stop_foreign(self) -> None:
        """    Stop a runtime this process did not spawn, such as one left behind by a previous server process."""
        info = self._health_info()
        pid = int(info.get("pid") or 0) or self._pid_on_port()
        if not pid:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        for _ in range(20):
            if not self.is_up(0.5):
                return
            time.sleep(0.5)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def is_up(self, timeout: float = 2.0) -> bool:
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(self.endpoint() + "/models")
            return resp.status_code < 400
        except httpx.HTTPError:
            return False

    def supports_tools(self) -> bool | None:
        """Does the served model accept a `tools` field? None means "this backend cannot tell".

        It matters because the turn loop always passes the tool schemas: a server that rejects the
        field answers **every** turn with HTTP 400 "does not support tools", which reads like a broken
        model rather than a missing capability. Detect once, report it, and drop the field — an agent
        that answers in prose is useful; one that 400s on every turn is not.
        """
        override = str(self.cfg.get("model", {}).get("local", {}).get("tools") or "auto").lower()
        if override in ("on", "true", "yes"):
            return True
        if override in ("off", "false", "no"):
            return False
        if self.backend in ("vllm", "transformers"):
            return True                     # the repo's own chat template renders the tools
        return None                         # llama.cpp takes the field; the model decides what it does

    def _spawn(self, cmd: list[str]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "ab") as log:
            try:
                # steady-state GPU promotion: the loader halves the card to survive transformers' allocator
                # warmup, which only applies during load. "auto" means "as many layers as fit while keeping a
                # KV reserve" — measured 1.14 -> 1.44 tok/s on the 4B (doc 25).
                env = dict(os.environ)
                env.setdefault("ATHENA_PROMOTE_LAYERS",
                               str(int((self.cfg.get("model") or {}).get("local", {}).get("promote_layers", 32))))
                env.setdefault("ATHENA_PROMOTE_RESERVE_MB",
                               str(int((self.cfg.get("model") or {}).get("local", {}).get("promote_reserve_mb", 1600))))
                self.proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                             start_new_session=True, env=env)
            except FileNotFoundError as exc:
                raise ProviderError(
                    f"{cmd[0]} is not installed — install llama.cpp, or set model.local.backend to "
                    f"ollama/command. Command was: {' '.join(cmd)}") from exc
        atexit.register(self.stop)

    def _wait_up(self) -> None:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if self.is_up():
                return
            if self.proc is not None and self.proc.poll() is not None:
                raise ProviderError(f"model server exited ({self.proc.returncode}): {self._log_tail()}")
            time.sleep(1.0)
        self.stop()
        raise ProviderError(
            f"model server did not answer on {self.endpoint()} within {self.timeout:.0f}s — "
            f"see {self.log_path}")

    def start(self) -> str:
        """Start (or reuse) the server and return its base URL."""
        self.base_url = self.endpoint()

        if self.backend in ("vllm", "transformers"):
            if self.is_up():
                if self.backend == "transformers" and self.serves_another_model():
                    raise ProviderError(
                        f"port {self.port} already serves a different local model "
                        f"({self._health_info().get('served_name')!r}, this agent wants "
                        f"{self.served_name()!r}) — give each local model its own "
                        f"model.local.port, or point this agent at the running one")
                if self.backend == "transformers" and self.is_stale():
                    self.stop_foreign()          # the warm one predates the current runtime file
                else:
                    return self.base_url
            self._spawn(self.render())
            self._wait_up()
            return self.base_url

        if self.is_up():
            return self.base_url                  # someone already has it warm — never respawn
        if not self.repo and not self.file and not self.name:
            raise ProviderError(
                "model.local.enabled is true but no model is named — set model.local.name to a download in models/")

        self._spawn(self.render())
        self._wait_up()
        return self.base_url

    def _log_tail(self, lines: int = 12) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "(no log)"
        return "\n".join(text.strip().splitlines()[-lines:])

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.proc = None


class LocalProvider(BaseProvider):
    """The openai adapter pointed at a server we start ourselves — lazily, on the first call."""

    name = "local"

    def __init__(self, cfg: dict):
        from providers.openai_compat import OpenAICompatProvider
        self.server = LocalServer(cfg)
        self._inner = OpenAICompatProvider(base_url="", model="", allow_empty_base=True)
        self._started = False
        self.tools_ok: bool | None = None

    def describe(self) -> dict:
        return {"provider": "local", "backend": self.server.backend,
                "model": self.server.served_name(), "port": self.server.port,
                "tools": self.tools_ok}

    def _ensure(self) -> None:
        if self._started:
            return
        base = self.server.start()
        self._inner.base_url = base
        self._inner.model = self.server.served_name()
        self.tools_ok = self.server.supports_tools()
        self._started = True

    def call(self, messages: list[dict], *, tools: list[dict] | None = None,
             max_tokens: int = 1024, temperature: float = 0.7) -> dict:
        self._ensure()
        # A model with no tool template rejects the field outright; send it only where it is accepted.
        send_tools = tools if (self.tools_ok is not False) else None
        try:
            return self._inner.call(messages, tools=send_tools, max_tokens=max_tokens,
                                    temperature=temperature)
        except Exception:                             # noqa: BLE001 — decide by liveness, not by error text
            # `_started` was a one-way flag: once the runtime died (crash, OOM, killed, box restart) this
            # provider kept posting to a dead port and every turn failed with "Connection refused" until the
            # whole server was restarted. If the port is not answering, forget the warm state and retry ONCE.
            if self.server.is_up(0.5):
                raise                                 # it is alive — the error is real, not a dead runtime
            self._started = False
            self._ensure()
            return self._inner.call(messages, tools=send_tools, max_tokens=max_tokens,
                                    temperature=temperature)

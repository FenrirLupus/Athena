"""Provider adapter interface and the factory that picks one.

An adapter turns a message list into {content, tool_calls, cost_usd}. Nothing else in the system
knows how a provider works — core calls this and gets a normalised shape back.

Supported wire formats (documentation/architecture/17-providers-and-models.md):

    openai     /chat/completions     OpenAI, DeepSeek, OpenRouter, vLLM, llama.cpp, LM Studio, Ollama
    anthropic  /v1/messages          Anthropic, and any gateway that keeps the Messages API
    mock       offline               scripted replies for tests, no network at all

A **local model is not a third format**. Athena downloads a GGUF into `models/`, starts a server for
it, and points the `openai` adapter at `http://127.0.0.1:<port>/v1`. Local is a *base_url*, which is
why running a downloaded model is exactly the same thing as using a custom URL.
"""
from __future__ import annotations

import os
from pathlib import Path


# Credential sidecar (core/credproxy.py). Empty means "sign requests here"; set at boot when the sidecar runs, in
# which case the adapter sends no Authorization at all and the proxy adds one on the other side.
PROXY: dict = {"url": "", "token": ""}


class ProviderError(RuntimeError):
    """A provider call failed in a way the turn should surface, not retry forever."""


class BaseProvider:
    name = "base"

    def call(self, messages: list[dict], *, tools: list[dict] | None = None,
             max_tokens: int = 1024, temperature: float = 0.7) -> dict:
        """Return {"content": str, "tool_calls": [...], "cost_usd": float, "finish_reason": str}."""
        raise NotImplementedError

    def describe(self) -> dict:
        """What this provider is, for the resource panel and the logs (never includes a key)."""
        return {"provider": self.name, "model": getattr(self, "model", "")}


# -- credentials -----------------------------------------------------------

# Where a wire format goes when it must not inherit another provider's endpoint. Used only when an agent
# declares a provider that differs from the global one and gives no base_url of its own.
DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}

ATHENA_ROOT = Path(os.environ.get("ATHENA_ROOT") or (Path.home() / ".athena"))
SECRET_FILE = ATHENA_ROOT / ".secret"


def secret_path(root=None) -> Path:
    """Where an agent's keys live: its own `.secret`, or the global one.

    Two scopes, one rule — **local shadows global**:
      * `~/.athena/profiles/<agent>/.secret`   that agent's own keys (a separate key, a separate account)
      * `~/.athena/.secret`                    keys shared by every agent
    The profile file is checked first, so an agent with its own key never picks up the shared one by accident.
    """
    return (Path(root) / ".secret") if root else SECRET_FILE


def read_secrets(root=None) -> dict:
    """`{NAME: value}` from one `.secret` file. Names are upper-case env-style; comments ignored."""
    out: dict[str, str] = {}
    path = secret_path(root)
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                out[name.strip().upper()] = value.strip()
    except OSError:
        pass
    return out

# Environment names tried per format, most specific first.
ENV_NAMES = {
    "openai": ("ATHENA_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"),
    "anthropic": ("ATHENA_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
}


# Host fragments -> the conventional env-style secret name. The file is `.secret` but its lines read
# exactly like a dotenv, so the same value works whether Athena reads it, `export`ed into a shell, or
# pasted into a service definition.
HOST_SECRETS = (
    ("deepseek", "DEEPSEEK_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
    ("openai", "OPENAI_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("claude", "ANTHROPIC_API_KEY"),
    ("mistral", "MISTRAL_API_KEY"),
    ("groq", "GROQ_API_KEY"),
    ("together", "TOGETHER_API_KEY"),
    ("fireworks", "FIREWORKS_API_KEY"),
    ("localhost", "ATHENA_API_KEY"),
    ("127.0.0.1", "ATHENA_API_KEY"),
)


def secret_name(provider: str, base_url: str = "") -> str:
    """The ALL-CAPS variable that holds this endpoint's key.

    A key is a property of the endpoint, not of the agent: two agents pointed at DeepSeek share one
    `DEEPSEEK_API_KEY`, which is also what a human would `export` when running curl. Named after the host
    when one is recognised, otherwise `<PROVIDER>_API_KEY`, and `ATHENA_API_KEY` for anything local.
    """
    host = ""
    try:
        from urllib.parse import urlparse
        host = (urlparse(base_url or "").hostname or "").lower()
    except ValueError:
        host = ""

    for fragment, name in HOST_SECRETS:
        if fragment in host:
            return name
    if provider in ("anthropic", "claude"):
        return "ANTHROPIC_API_KEY"
    if provider in ("openai", "deepseek", "openrouter", "vllm", "custom"):
        return "OPENAI_API_KEY"
    upper = "".join(ch if ch.isalnum() else "_" for ch in (provider or "athena")).upper()
    return f"{upper}_API_KEY" if upper else "ATHENA_API_KEY"


def write_secret(name: str, value: str, root=None) -> Path:
    """Store a key in `.secret` under `name`, replacing any existing line, with owner-only permissions.

    The setup wizard collects a key once and this is the only place it lands: never config.yaml (which
    gets pasted into issues and read by the agent), never the profile directory (which gets copied and
    archived), never a log line.
    """
    name = (name or "").strip().upper()
    value = (value or "").strip()
    if not name or not value:
        raise ProviderError("a secret needs both a name and a value")
    if any(ch in name for ch in "=\n\r"):
        raise ProviderError("a secret name cannot contain '=' or a newline")
    if not all(ch.isalnum() or ch == "_" for ch in name):
        raise ProviderError(f"secret name {name!r} must be A-Z, 0-9 and _ (an env-style variable)")

    target = secret_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if target.exists():
        lines = target.read_text(encoding="utf-8").splitlines()
    out, replaced = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == name and "=" in line:
            out.append(f"{name}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{name}={value}")
    # `target`, never the module-level SECRET_FILE: with `root=` the write must land in that profile's file.
    # Writing to SECRET_FILE here truncated the GLOBAL secret file and destroyed every other key in it —
    # Writing to the module-level path here truncates the shared secret file and destroys every other key in it.
    payload = ("\n".join(out) + "\n").encode("utf-8")
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)
    try:                                               # the directory entry too, or the rename itself can be lost
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass
    return target


def api_key(kind: str, name: str = "", root=None) -> str:
    """The key for a format: the agent's own file, then the shared one, then the environment.

    Precedence, in order — **local shadows global**:
      1. `<profile>/.secret` — this agent's own key (a separate account, a separate quota)
      2. `~/.athena/.secret` — keys shared by every agent
      3. the environment — how a launcher or a service supplies a key without writing a file

    Within a file: a line named by `name` (the agent's `model.key_name`) wins over a line named for the
    format (`deepseek`), which wins over a bare first line. Config never holds a secret.
    """
    roots = []
    if root:
        roots.append(Path(root))
    roots.append(SECRET_FILE.parent)

    for base in roots:
        path = base / ".secret"
        named = explicit = fallback = ""
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key_name, _, value = line.partition("=")
                        key_name = key_name.strip().lower()
                        if name and key_name == name.strip().lower():
                            named = value.strip()
                        elif key_name == kind:
                            explicit = value.strip()
                    elif not fallback:
                        fallback = line
        except OSError:
            continue
        if named:
            return named
        if explicit or fallback:
            return explicit or fallback

    for env in ENV_NAMES.get(kind, ("ATHENA_API_KEY",)):
        val = os.environ.get(env)
        if val and val.strip():
            return val.strip()
    return ""


def key_exists(kind: str, name: str = "", root=None) -> bool:
    """Whether a key resolves, without returning it — for the UI's `has_key` and the wake checks."""
    return bool(api_key(kind, name, root=root))


# -- factory ---------------------------------------------------------------

def build(cfg: dict, key_name: str = "", root=None) -> BaseProvider:
    """Instantiate the adapter named in the config; a local model starts its server lazily.

    `key_name` selects the agent's own line in `.secret`, so two agents can point at the same endpoint
    with different keys — or at different endpoints entirely.
    """
    model = cfg.get("model") or {}
    kind = (model.get("provider") or "mock").lower()
    local = model.get("local") or {}

    # local wins over `provider`: enabling it is an explicit statement about where answers come from, and
    # a config that says `provider: mock, local.enabled: true` must not quietly keep answering from the mock
    if local.get("enabled"):
        from providers.local import LocalProvider
        return LocalProvider(cfg)

    if kind == "mock":
        from providers.mock import MockProvider
        return MockProvider(model_name=str(model.get("name") or "mock-1"))

    if kind in ("openai", "deepseek", "lmstudio", "llamacpp", "ollama", "vllm", "custom"):
        from providers.openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(
            base_url=str(model.get("base_url") or ""),
            model=str(model.get("name") or ""),
            key_name=key_name,
            secret_root=root,
        )

    if kind in ("anthropic", "claude"):
        from providers.anthropic import AnthropicProvider
        return AnthropicProvider(
            base_url=str(model.get("base_url") or "https://api.anthropic.com"),
            model=str(model.get("name") or "claude-sonnet-4-5"),
            key_name=key_name,
            secret_root=root,
        )

    raise ProviderError(f"unknown provider: {kind} (expected openai, anthropic or mock)")

"""Secret store — the .secret file (the Operator's spec).

ALL credentials live in ONE protected file (the .secret store) with
restricted permissions (0600 — owner only). The rule:

  • .secret contains CREDENTIALS ONLY — nothing more (like an .env)
  • each API key type for each provider is stored here, one per line:
        LMSTUDIO_API_KEY=
        OPENCODE_GO_API_KEY=
  • secrets are READ locally — never written to the vault, never logged
  • secrets LEAVE the machine ONLY for the necessary provider calls
    (API keys to their providers) — nothing else
  • each profile can have its own .secret (scoped, per-profile
    secret_scope); the default profile's store is the root one

The file is ENV-STYLE flat KEY=VALUE lines (the Operator's spec: per-profile
.env but credentials only). Config (base_url, models) lives in
authentication.json; the api_key there is DROPPED — it lives only here.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from core.config import ATHENA_ROOT

_lock = threading.Lock()
# The protected store: a hidden file with owner-only permissions.
SECRET_FILE = ATHENA_ROOT / ".secret"

# ALL known/popular providers (the Operator's spec): every provider's API_KEY
# variable is registered in .secret with a NULL default, so the user MUST
# provide the applicable keys. The env-style key is PROVIDER_API_KEY.
KNOWN_PROVIDERS = [
    "openai", "openai-api", "anthropic", "google", "gemini", "deepseek",
    "openrouter", "groq", "mistral", "cohere", "xai", "grok", "together",
    "fireworks", "perplexity", "replicate", "ollama", "ollama-cloud", "lmstudio",
    "opencode-go", "opencode-zen", "athena", "custom",
    "azure", "azure-foundry", "bedrock", "vertex", "moonshot", "kimi",
    "kimi-coding", "zhipu", "zai", "minimax", "qwen", "qwen-oauth", "alibaba",
    "baidu", "tencent", "tencent-tokenhub", "vllm", "localai", "novita",
    "scaleway", "cerebras", "samba", "nous", "qstash", "nvidia", "xiaomi",
    "huggingface", "arcee", "gmi", "kilocode", "stepfun", "upstage",
    "deepinfra", "ai-gateway",
]


def _provider_key(name: str) -> str:
    return f"{name.upper().replace('-', '_')}_API_KEY"


def _seed_keys(data: dict, p: Path) -> None:
    """Ensure EVERY known provider's API_KEY key exists with a NULL
    default (the Operator's spec: default values when empty are `null`, so the
    user MUST supply the applicable keys). Existing real values are
    preserved."""
    for prov in KNOWN_PROVIDERS:
        data.setdefault(_provider_key(prov), "null")


def _path(profile: str = "") -> Path:
    if profile and profile != "default":
        return ATHENA_ROOT / "profiles" / profile / ".secret"
    return _resolve_secret_file()


def _resolve_secret_file() -> Path:
    """The .secret path, DERIVED FROM THE CURRENT ATHENA_ROOT at call
    time (the Operator's 08-12 wipe-fix).

    The old SECRET_FILE was computed at import — a test (or the doctor's
    isolated subprocess) that patched ATHENA_ROOT to a temp still had
    SECRET_FILE pointing at the REAL .secret, so a seed() write could
    destroy the operator's real keys. Deriving at call time means any
    ATHENA_ROOT redirect follows correctly.

    EXCEPTION: an explicit SECRET_FILE override (a test pointing it at
    a temp for isolation) is HONORED — the override wins over the
    derived path, so test isolation still works.
    """
    try:
        from core.config import ATHENA_ROOT as _root
        derived = _root / ".secret"
        # An override that differs from the derived path (a test temp)
        # is honored.
        if SECRET_FILE != derived:
            return SECRET_FILE
        return derived
    except Exception:
        return SECRET_FILE


def _ensure_protected(p: Path) -> None:
    """The .secret is 0600 — owner read/write only."""
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            os.chmod(p, 0o600)
    except Exception:
        pass


def _load(p: Path) -> dict:
    """Parse the env-style .secret into a flat dict {KEY: value}."""
    out = {}
    try:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    out[k.strip()] = v.strip()
    except Exception:
        pass
    # The known-provider registry (the Operator's spec): every provider's key
    # exists — null/empty defaults mean the user must supply the ones
    # they use.
    _seed_keys(out, p)
    return out


def _save(p: Path, data: dict) -> bool:
    """Write the flat dict as env-style KEY=VALUE lines."""
    _ensure_protected(p)
    try:
        lines = ["# .secret — credentials ONLY (the Operator's spec).",
                 "# One API key per provider, KEY=VALUE. Never logged, never"]
        lines.append("# stored outside this file; leaves only for provider calls.")
        for k in sorted(data):
            v = data[k]
            lines.append(f"{k}={v}")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(p, 0o600)
        return True
    except Exception as exc:
        _log(4, f"secret store write failed: {exc}", source="secret_store")
        return False


def _log(level: int, msg: str, source: str = "secret_store") -> None:
    """The secret store is operational — failures are logged (never the
    secret values)."""
    try:
        from metrics.logger import log
        log(level, msg, source=source)
    except Exception:
        pass


def get(key: str, profile: str = "", default: str = "") -> str:
    """Read a secret locally (never logs it, never stores it elsewhere).

    Keys are the env-style flat names: 'LMSTUDIO_API_KEY',
    'OPENCODE_GO_API_KEY'. Also accepts 'providers.NAME.api_key' (the
    nested legacy form) resolved to PROVIDER_API_KEY for compatibility.

    The seeded NULL default ('null') is treated as EMPTY — a provider
    without a real key reports '', never the literal 'null'.
    """
    p = _path(profile)
    data = _load(p)
    if key in data:
        v = data.get(key, default)
        if v == "null" or v is None:
            return default
        return str(v)
    # Legacy nested form: providers.opencode-go.api_key → OPENCODE_GO_API_KEY
    if key.startswith("providers.") and key.endswith(".api_key"):
        name = key[len("providers."):-len(".api_key")]
        flat = f"{name.upper().replace('-', '_')}_API_KEY"
        v = data.get(flat, default)
        if v == "null" or v is None:
            return default
        return str(v)
    return default


def get_api_key(provider: str, profile: str = "") -> str:
    """The provider's credential (the Operator's spec): each provider's API
    key type lives in .secret as PROVIDER_API_KEY."""
    flat = f"{provider.upper().replace('-', '_')}_API_KEY"
    return get(flat, profile=profile)


def set(key: str, value: str, profile: str = "") -> bool:
    """Store a secret (env-style flat key)."""
    with _lock:
        p = _path(profile)
        data = _load(p)
        data[key] = value
        return _save(p, data)


def set_api_key(provider: str, value: str, profile: str = "") -> bool:
    """Store a provider's API key as PROVIDER_API_KEY."""
    flat = f"{provider.upper().replace('-', '_')}_API_KEY"
    return set(flat, value, profile=profile)


def has(key: str, profile: str = "") -> bool:
    return bool(get(key, profile=profile))


def keys(profile: str = "") -> list[str]:
    """The PROVIDED keys (real values only — null defaults are hidden,
    they are not credentials yet)."""
    p = _path(profile)
    data = _load(p)
    return [k for k, v in data.items() if v not in ("null", "", None)]




def seed(profile: str = "", create_if_missing: bool = False) -> list[str]:
    """Write the FULL known-provider registry to .secret (null defaults).

    the Operator's spec: every known provider's API_KEY variable is saved in
    the .secret file with an empty default, so the user MUST provide the
    keys they use. Existing values are preserved. Returns the key names.

    WIPE-FIX (08-12): seed() NEVER destroys real values. It only ADDS
    missing keys to an EXISTING .secret. create_if_missing=True (the
    wipe's default branch / fresh setup) writes the null registry ONLY
    when the file genuinely does not exist — the operator's real values
    are never touched either way.
    """
    with _lock:
        p = _path(profile)
        if not p.exists():
            if not create_if_missing:
                return []
            data = {}
            # A freshly-created default .secret carries the FULL null
            # registry (standard/default values — the Operator's rule: real
            # credentials NEVER come from defaults).
            _seed_keys(data, p)
        else:
            data = _load(p)
        _save(p, data)
        return [k for k in data if k.endswith("_API_KEY")]


def is_protected(profile: str = "") -> bool:
    """The .secret file is owner-only (0600)."""
    p = _path(profile)
    if not p.exists():
        return False
    try:
        return (os.stat(p).st_mode & 0o777) == 0o600
    except Exception:
        return False

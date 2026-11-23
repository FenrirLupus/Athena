"""The provider registry — `~/.athena/provider.json`.

Keys and providers are two different things, and conflating them is what makes "two DeepSeek accounts" awkward.
The registry is organised the way the operator thinks about it:

```json
{
  "deepseek": {
    "format": "openai",
    "base_url": "https://api.deepseek.com/v1",
    "keys": [
      {"name": "DEEPSEEK_API_KEY", "scope": "global"},
      {"name": "DEEPSEEK_WORK_KEY", "scope": "local", "agent": "scout"}
    ],
    "models": ["deepseek-chat", "deepseek-reasoner"],
    "models_updated": "2026-11-01T15:04:00Z"
  }
}
```

**Provider name is the primary section; models hang off it.** A key is registered *against* a provider, so two
keys for the same endpoint both live under `deepseek` and the model list is fetched once per provider — whichever
key is in force — and the interface offers only what that provider actually serves.

What lives where, and why:

| file | holds | never holds |
|---|---|---|
| `~/.athena/.secret` | global key values (`NAME=value`, 0600) | provider metadata |
| `~/.athena/profiles/<agent>/.secret` | that agent's own key values | anything shared |
| `~/.athena/provider.json` | provider names, formats, endpoints, key *names*, model ids | key **values** |
| `config.yaml` / `config.yaml` | the selected provider + model | any secret |

So the registry is safe to read, print and commit: it names keys, it never carries them.
"""
from __future__ import annotations

import json
from pathlib import Path
from core import timefmt, trace
from core.jsonio import read_json
from core.root import athena_root
from core import durable

ATHENA_ROOT = athena_root()
REGISTRY = ATHENA_ROOT / "provider.json"


def path() -> Path:
    return REGISTRY


def load() -> dict:
    """The registry, or an empty one. A corrupt file must not stop a turn."""
    data = read_json(REGISTRY, {})
    if not isinstance(data, dict):
        # the fallback is silent by design, so the trace is the only thing that says the file was replaced by {}
        trace.event("provider.registry.bad", level="error", path=str(REGISTRY),
                    got=type(data).__name__, reason="not an object — every provider vanished from view")
        return {}
    return data


def save(data: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    durable.write_state(REGISTRY, json.dumps(data, indent=2, sort_keys=True))


def register(provider: str, *, format: str = "openai", base_url: str = "",
             key_name: str = "", scope: str = "global", agent: str = "",
             models: list[str] | None = None, contexts: dict[str, int] | None = None) -> dict:
    """Record a provider (and optionally one of its keys). Never stores a key value."""
    provider = (provider or "").strip().lower()
    if not provider:
        trace.event("provider.register.refused", level="error", reason="no provider name")
        raise ValueError("a provider needs a name")
    data = load()
    existed = provider in data
    entry = data.setdefault(provider, {"format": format or "openai", "base_url": base_url,
                                       "keys": [], "models": []})
    if base_url:
        entry["base_url"] = base_url
    if format:
        entry["format"] = format
    if key_name:
        name = key_name.strip().upper()
        existing = [k for k in entry.setdefault("keys", [])
                    if not (k.get("name") == name and k.get("scope") == scope and k.get("agent") == agent)]
        existing.append({"name": name, "scope": scope, "agent": agent})
        entry["keys"] = existing
    if contexts is not None:
        entry["contexts"] = {str(k): int(v) for k, v in contexts.items() if v}
    if models is not None:
        entry["models"] = list(models)
        entry["models_updated"] = timefmt.stamp()
    save(data)
    # names and counts only: a key VALUE never enters this function, and nothing here could carry one
    trace.event("provider.register", level="debug", provider=provider, format=entry.get("format"),
                base_url=entry.get("base_url") or "-", new=not existed,
                key_names=[k.get("name") for k in entry.get("keys") or []],
                models=len(entry.get("models") or []))
    return entry


def set_models(provider: str, models: list[str]) -> dict:
    return register(provider, models=models)


def models(provider: str) -> list[str]:
    label = (provider or "").lower()
    entry = load().get(label) or {}
    if not entry:
        # an unknown label is why "refresh models" returns nothing: name the label that was looked up
        trace.event("provider.unknown", level="debug", provider=label or "-", by="models")
    return list(entry.get("models") or [])


def keys(provider: str) -> list[dict]:
    label = (provider or "").lower()
    entry = load().get(label) or {}
    found = list(entry.get("keys") or [])
    if label and not found:
        # a provider with no key is a provider the turn will be refused for — the registry knows it, say it
        trace.event("provider.no_key", level="debug", provider=label,
                    reason="no key registered against this provider")
    return found


def fetch_models(base_url: str, key: str = "", timeout: float = 20.0,
                 provider: str = "") -> list[str]:
    """Ask the endpoint what it serves (`GET /models`) — the only honest source for a model list."""
    import httpx
    from urllib.parse import urlsplit

    if not base_url:
        trace.event("provider.refresh.refused", level="error", provider=provider or "-",
                    reason="no base_url — nothing to ask")
        return []
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{url}/models", headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:                                # noqa: BLE001 — a failed refresh is not an error state
        # the empty list is the refusal the caller sees; without this line "no models" has no cause
        trace.failure("provider.refresh.failed", exc, provider=provider or "-",
                      host=urlsplit(url).netloc, endpoint="/models",
                      key_supplied=bool(key))
        return []
    rows = payload.get("data") if isinstance(payload, dict) else payload
    out = []
    for row in rows or []:
        if isinstance(row, dict):
            ident = row.get("id") or row.get("name")
            if ident:
                out.append(str(ident))
        elif isinstance(row, str):
            out.append(row)
    out = sorted(set(out))
    trace.event("provider.models", level="debug", provider=provider or "-",
                host=urlsplit(url).netloc, models=len(out))
    return out


def set_contexts(provider: str, contexts: dict[str, int]) -> dict:
    """Record the context window each model published, keyed by model id.

    Only providers that publish one have entries here. OpenAI and Anthropic return ids alone, so the configured floor
    answers for them; DeepSeek does publish a figure (`context_window`), which is why the live install adopts one.
    """
    clean = {str(k): int(v) for k, v in (contexts or {}).items() if v}
    return register(provider, contexts=clean)




def fetch_model_rows(base_url: str, key: str = "", timeout: float = 20.0,
                     provider: str = "") -> list[dict]:
    """The raw entries from `GET /models`, unfiltered.

    A provider that publishes a context length puts it here — OpenRouter as `context_length`, vLLM as `max_model_len`.
    OpenAI, Anthropic and DeepSeek return ids and nothing else, so for those this yields rows with no window and the
    configured floor remains the answer.
    """
    import httpx
    from urllib.parse import urlsplit

    if not base_url:
        return []
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(f"{url}/models", headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:                                # noqa: BLE001 — a failed refresh is not an error state
        trace.failure("provider.refresh.failed", exc, provider=provider or "-",
                      host=urlsplit(url).netloc, endpoint="/models", rows=True)
        return []
    rows = payload.get("data") if isinstance(payload, dict) else payload
    kept = [row for row in (rows or []) if isinstance(row, dict)]
    trace.event("provider.models", level="debug", provider=provider or "-",
                host=urlsplit(url).netloc, rows=len(kept))
    return kept


CONTEXT_FIELDS = ("context_length", "max_model_len", "context_window", "max_context_length")


def contexts_from_rows(rows: list[dict]) -> dict[str, int]:
    """Model id → published window, for whichever of the known field names each entry uses."""
    out: dict[str, int] = {}
    unnamed = 0
    for row in rows or []:
        ident = str(row.get("id") or row.get("name") or "").strip()
        if not ident:
            unnamed += 1                       # a row with no id teaches nothing and cannot be keyed
            continue
        for field in CONTEXT_FIELDS:
            value = row.get(field)
            if value in (None, "", 0):
                continue
            try:
                out[ident] = int(value)
            except (TypeError, ValueError):
                continue
            break
    if unnamed:
        trace.event("provider.contexts.skipped", level="debug", rows=len(rows or []), unnamed=unnamed,
                    with_window=len(out), reason="row has no model id")
    return out

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
from core import timefmt
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
    return data if isinstance(data, dict) else {}


def save(data: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    durable.write_state(REGISTRY, json.dumps(data, indent=2, sort_keys=True))


def register(provider: str, *, format: str = "openai", base_url: str = "",
             key_name: str = "", scope: str = "global", agent: str = "",
             models: list[str] | None = None, contexts: dict[str, int] | None = None) -> dict:
    """Record a provider (and optionally one of its keys). Never stores a key value."""
    provider = (provider or "").strip().lower()
    if not provider:
        raise ValueError("a provider needs a name")
    data = load()
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
    return entry


def set_models(provider: str, models: list[str]) -> dict:
    return register(provider, models=models)


def models(provider: str) -> list[str]:
    return list((load().get((provider or "").lower()) or {}).get("models") or [])


def keys(provider: str) -> list[dict]:
    return list((load().get((provider or "").lower()) or {}).get("keys") or [])


def fetch_models(base_url: str, key: str = "", timeout: float = 20.0) -> list[str]:
    """Ask the endpoint what it serves (`GET /models`) — the only honest source for a model list."""
    import httpx

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
    except Exception:                                # noqa: BLE001 — a failed refresh is not an error state
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
    return sorted(set(out))


def set_contexts(provider: str, contexts: dict[str, int]) -> dict:
    """Record the context window each model published, keyed by model id.

    Only providers that publish one have entries here. OpenAI and Anthropic return ids alone, so the configured floor
    answers for them; DeepSeek does publish a figure (`context_window`), which is why the live install adopts one.
    """
    clean = {str(k): int(v) for k, v in (contexts or {}).items() if v}
    return register(provider, contexts=clean)




def fetch_model_rows(base_url: str, key: str = "", timeout: float = 20.0) -> list[dict]:
    """The raw entries from `GET /models`, unfiltered.

    A provider that publishes a context length puts it here — OpenRouter as `context_length`, vLLM as `max_model_len`.
    OpenAI, Anthropic and DeepSeek return ids and nothing else, so for those this yields rows with no window and the
    configured floor remains the answer.
    """
    import httpx

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
    except Exception:                                # noqa: BLE001 — a failed refresh is not an error state
        return []
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return [row for row in (rows or []) if isinstance(row, dict)]


CONTEXT_FIELDS = ("context_length", "max_model_len", "context_window", "max_context_length")


def contexts_from_rows(rows: list[dict]) -> dict[str, int]:
    """Model id → published window, for whichever of the known field names each entry uses."""
    out: dict[str, int] = {}
    for row in rows or []:
        ident = str(row.get("id") or row.get("name") or "").strip()
        if not ident:
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
    return out

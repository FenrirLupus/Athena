"""Authentication store — per-provider CONFIG in authentication.json.

the Operator's spec — the split:
  • .secret            = CREDENTIALS ONLY (env-style PROVIDER_API_KEY
                         lines). The api_key lives HERE, never in
                         authentication.json.
  • authentication.json = CONFIG only: base_url + discovered models +
                         last_connected_at. The api_key variable is
                         DROPPED from this file — it lives only in .secret.

Shape (per provider):

    {
      "version": 1,
      "providers": {
        "lmstudio": {
          "base_url": "http://localhost:1234/v1",
          "models": ["lmstudio-community/qwen3.5-4b"],
          "last_connected_at": null
        },
        ...
      }
    }

Auto-population: when Athena connects to a provider, it probes the
/models endpoint and writes the discovered model list back into the store.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from core.config import AUTH_PATH


# THE PROVIDER CATALOG (the Operator's 08-14 spec): every provider Athena
# supports — the SAME set the provider catalog supports — with its canonical base_url.
# A provider is ACTIVATED when its API key exists in .secret (the
# 3-things rule: name + base_url + api_key). The catalog supplies the
# base_url; the key comes from .secret; the name is the key's provider.
PROVIDER_CATALOG: dict[str, str] = {
    "opencode-go": "https://opencode.ai/zen/go/v1",
    "opencode-zen": "https://opencode.ai/zen/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "openai-api": "https://api.openai.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "xai": "https://api.x.ai/v1",
    "grok": "https://api.x.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "cohere": "https://api.cohere.com/v1",
    "together": "https://api.together.xyz/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "perplexity": "https://api.perplexity.ai",
    "replicate": "https://api.replicate.com/v1",
    "ollama": "http://localhost:11434/v1",
    "ollama-cloud": "https://ollama.com/v1",
    "lmstudio": "http://localhost:1234/v1",
    "azure": "https://your-resource.openai.azure.com/",
    "azure-foundry": "https://your-foundry.resource.azure.com/",
    "bedrock": "https://bedrock-runtime.us-east-1.amazonaws.com",
    "vertex": "https://us-central1-aiplatform.googleapis.com/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "kimi": "https://api.moonshot.cn/v1",
    "kimi-coding": "https://api.moonshot.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "zai": "https://api.z.ai/api/paas/v4",
    "minimax": "https://api.minimaxi.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "qwen-oauth": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "alibaba": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "baidu": "https://qianfan.baidubce.com/v2",
    "tencent": "https://api.hunyuan.cloud.tencent.com/v1",
    "tencent-tokenhub": "https://api.hunyuan.cloud.tencent.com/v1",
    "vllm": "http://localhost:8000/v1",
    "localai": "http://localhost:8080/v1",
    "novita": "https://api.novita.ai/v3/openai",
    "scaleway": "https://api.scaleway.ai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "samba": "https://api.sambanova.ai/v1",
    "nous": "https://api.nousresearch.com/v1",
    "qstash": "https://qstash.upstash.io/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "xiaomi": "https://api.xiaomi.com/v1",
    "huggingface": "https://router.huggingface.co/v1",
    "arcee": "https://api.arcee.ai/v1",
    "gmi": "https://api.gmi.com/v1",
    "kilocode": "https://api.kilocode.ai/v1",
    "stepfun": "https://api.stepfun.com/v1",
    "upstage": "https://api.upstage.ai/v1/solar",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "ai-gateway": "https://api.ai-gateway.com/v1",
    "athena": "",
    "custom": "",
}


# The ONE schema every provider entry uses. Missing information is null.
# authentication.json stores CONFIG only: base_url + models. The
# CREDENTIAL (api_key) lives in .secret — the config file never holds it.
PROVIDER_SCHEMA: dict = {
    "base_url": None,           # string | null — endpoint root
    "models": None,             # list | null — DISCOVERED model ids (auto)
    "last_connected_at": None,  # float | null — last successful connect
}


def normalize_provider(entry: dict | None) -> dict:
    """Return an entry with EVERY schema key present; missing = null."""
    entry = entry or {}
    out = {}
    for key, default in PROVIDER_SCHEMA.items():
        out[key] = entry.get(key, default)
    return out


def _load_raw() -> dict:
    """Read the CONFIG store (authentication.json) only."""
    # The credential registry is maintained: every known provider's key
    # exists in .secret (null defaults) so the user supplies the ones
    # they use (the Operator's spec).
    try:
        from core.secret_store import seed
        seed()
    except Exception:
        pass
    if AUTH_PATH.exists():
        try:
            data = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            from core.logging import log_event
            log_event(4, f"auth store read failed: {exc}", source="providers", action="load")
    return {"version": 1, "providers": {}}


def _save_raw(data: dict) -> None:
    """Write the CONFIG store. Credentials are NEVER written here —
    the api_key lives only in .secret (the Operator's spec)."""
    AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = AUTH_PATH.with_suffix(AUTH_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(AUTH_PATH)


def list_providers() -> dict[str, dict]:
    """Return {name: entry} for every REGISTERED provider, schema-normalized.

    THE TWO-STORE RULE (the Operator's 08-14 spec):
      • authentication.json = PROVIDER INFORMATION (what is SET UP):
        base_url + models + last_connected_at.
      • .secret             = CREDENTIALS ONLY (the api_key).
    A provider is ACTIVE (usable) when BOTH exist: registered in
    authentication.json AND keyed in .secret.
    """
    raw = _load_raw().get("providers", {})
    merged: dict[str, dict] = {}
    # 1. The stored configs (the SET-UP providers — authentication.json).
    for name, entry in raw.items():
        merged[name] = normalize_provider(entry)
    # 2. THE SETUP HELPER (the 08-14 fix): when a NEW api_key appears in
    #    .secret for a provider the catalog knows, auto-REGISTER it in
    #    authentication.json with the catalog base_url — so the operator
    #    drops a key in .secret and the provider is SET UP (the 3 things:
    #    name + base_url + api_key). The registry stays the source of
    #    truth; .secret never holds config.
    from core.secret_store import get_api_key as _key
    changed = False
    for name, base_url in PROVIDER_CATALOG.items():
        if not base_url or not _key(name):
            continue
        if name not in merged:
            merged[name] = normalize_provider({"base_url": base_url})
            changed = True
        elif not merged[name].get("base_url"):
            merged[name]["base_url"] = base_url
            changed = True
    if changed:
        try:
            data = _load_raw()
            data.setdefault("providers", {}).update(
                {name: {k: v for k, v in merged[name].items() if v}
                 for name in merged})
            _save_raw(data)
        except Exception:
            pass  # the registry write is best-effort — never break listing
    return merged


def get_provider(name: str) -> Optional[dict]:
    entry = _load_raw().get("providers", {}).get(name)
    return normalize_provider(entry) if entry is not None else None


def get_api_key(name: str) -> str:
    """The provider's credential — from .secret (never from config)."""
    from core.secret_store import get_api_key as secret_key
    return secret_key(name)


def save_provider(name: str, entry: dict) -> None:
    """Save a provider's CONFIG. Any api_key in the entry is DROPPED —
    the credential lives only in .secret."""
    entry = normalize_provider(entry)
    entry.pop("api_key", None)  # the credential never enters config
    data = _load_raw()
    data.setdefault("providers", {})[name] = entry
    _save_raw(data)


def delete_provider(name: str) -> bool:
    """Remove a provider from the CONFIG store. Returns True when the
    provider existed and was removed. The credential in .secret is NOT
    touched here (the caller clears it via secret_store when desired)."""
    data = _load_raw()
    providers = data.setdefault("providers", {})
    if name not in providers:
        return False
    del providers[name]
    _save_raw(data)
    return True




def probe_models(base_url: str, api_key: str = "", timeout: float = 10.0) -> list[str]:
    """Query a provider's /models endpoint. Returns the model id list.

    Fails gracefully: returns [] when the endpoint is unreachable or the
    provider doesn't expose one (some APIs require auth to list models).
    """
    import urllib.error
    import urllib.request

    url = f"{base_url.rstrip('/')}/models"
    headers = {"User-Agent": "Athena/0.1"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = []
        for item in data.get("data", []):
            mid = item.get("id")
            if mid:
                models.append(mid)
        return sorted(models)
    except Exception as exc:
        from core.logging import log_event
        log_event(4, f"model discovery parse failed: {exc}", source="providers", action="models")
        return []



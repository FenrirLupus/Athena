"""Vision routing — the Operator's 08-12 LM Studio setup.

The loop's vision slot: sends an image (base64 PNG) + a prompt to the
configured VISION provider/model (`.default/config.yaml`:
vision: {provider: lmstudio, model: qwen2.5-vl-3b-instruct}). The
API key comes from `.secret` (LMSTUDIO_API_KEY — the operator set it).

This is what makes the screenshot tool USABLE: screenshot captures →
vision.py describes/answers → the agent navigates visually.
"""

from __future__ import annotations

import base64
import json
import urllib.request
from pathlib import Path


def _vision_config() -> dict:
    """The vision selection from the profile config (provider + model)."""
    try:
        from core.config import load_config, ATHENA_ROOT
        # The default profile's config (the Operator set it there).
        p = ATHENA_ROOT / "profiles" / ".default" / "config.yaml"
        cfg = load_config("")  # platform root mirrors the default profile
        sel = (cfg.get("provider") or {}).get("selection") or {}
        return sel.get("vision") or {}
    except Exception:
        return {}


def _provider_endpoint(provider: str) -> str:
    """The provider's base_url — the Operator's 08-12 rule: the CONFIGURED
    base_url (authentication.json) wins; the catalog default is only a
    fallback. The operator sets the real URL when setting up a provider
    (e.g. lmstudio → http://localhost:1234/v1)."""
    # 1. The configured base_url (authentication.json — set at setup).
    try:
        from core.config import AUTH_PATH
        import json as _json
        from pathlib import Path as _Path
        p = _Path(AUTH_PATH)
        if p.exists():
            data = _json.loads(p.read_text(encoding="utf-8"))
            entry = (data.get("providers") or {}).get(provider) or {}
            url = str(entry.get("base_url") or "").strip()
            if url:
                return url.rstrip("/")
    except Exception:
        pass
    # 2. Fallback: the catalog default.
    try:
        from providers.provider_catalog import PROVIDER_CATALOG
        entry = PROVIDER_CATALOG.get(provider) or {}
        return str(entry.get("base_url", "http://localhost:1234/v1"))
    except Exception:
        return "http://localhost:1234/v1"


def _api_key(provider: str) -> str:
    """The provider's API key from .secret (names only, never echoed)."""
    try:
        from core.secret_store import get_api_key
        return get_api_key(provider) or ""
    except Exception:
        return ""


def _image_data_uri(image: str | Path | bytes) -> str:
    """Encode an image path or bytes into a data URI."""
    if isinstance(image, bytes):
        data = image
    else:
        p = Path(image)
        data = p.read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode()


def describe(image: str | Path | bytes, prompt: str = "",
             timeout: float = 60.0) -> str:
    """Send an image to the configured vision model; return the answer.

    The vision loop: screenshot (base64) → describe → the agent acts.
    """
    config = _vision_config()
    provider = str(config.get("provider") or "").strip()
    model = str(config.get("model") or "").strip()
    if not provider or not model:
        return json.dumps({"ok": False,
                           "detail": "no vision provider/model configured "
                                     "(set provider.selection.vision)"},
                          ensure_ascii=False)
    endpoint = _provider_endpoint(provider)
    key = _api_key(provider)
    uri = _image_data_uri(image)
    content = [{"type": "text", "text": prompt or
                "Describe what you see in this image in detail."}]
    # LM Studio/OpenAI-compatible vision: image_url with the data URI.
    content.append({"type": "image_url",
                    "image_url": {"url": uri}})
    return _chat(provider, model, content, timeout)


def ask_text(prompt: str, timeout: float = 60.0) -> str:
    """Send a TEXT prompt to the vision/reasoning model (the auxiliary-
    model fallback for text-page understanding: the model reads the
    fetched page content and answers). No image involved.

    The browser tool's `vision` action uses this: fetch a page silently,
    then ask the model what it says — the text-analysis fallback.
    """
    config = _vision_config()
    provider = str(config.get("provider") or "").strip()
    model = str(config.get("model") or "").strip()
    if not provider or not model:
        return json.dumps({"ok": False,
                           "detail": "no vision provider/model configured "
                                     "(set provider.selection.vision)"},
                          ensure_ascii=False)
    content = [{"type": "text", "text": prompt or ""}]
    return _chat(provider, model, content, timeout)


def _chat(provider: str, model: str, content: list, timeout: float) -> str:
    """The shared chat path: build the request, call the provider,
    return the answer JSON. Logs failures (the coverage rule)."""
    endpoint = _provider_endpoint(provider)
    key = _api_key(provider)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 512,
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        req = urllib.request.Request(
            endpoint.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        text = ((data.get("choices") or [{}])[0]
                .get("message", {}).get("content", ""))
        return json.dumps({"ok": True, "provider": provider, "model": model,
                           "answer": text[:2000]}, ensure_ascii=False)
    except Exception as exc:
        try:
            from core.logging import log_event
            log_event(4, f"vision call failed: {provider}/{model}: {exc}",
                      source="core", action="vision")
        except Exception:
            pass
        return json.dumps({"ok": False, "provider": provider, "model": model,
                           "detail": f"{type(exc).__name__}: {exc}"[:300]},
                          ensure_ascii=False)


def available() -> bool:
    """Is a vision provider/model configured?"""
    c = _vision_config()
    return bool(c.get("provider") and c.get("model"))

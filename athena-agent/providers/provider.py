"""Provider client — minimal OpenAI-compatible chat completion.

Stdlib only (urllib). Reads the provider chain from config.yaml (names
only) and the per-provider credentials from authentication.json (base_url,
api_key, models).

Retry ladder (the Operator's spec): for each provider in the chain (primary
then fallback), try each MODEL in its list up to ``max_attempts`` times
(default 3) on transient errors (429 rate-limit, 5xx, network blips) with
exponential backoff. When a model is exhausted, move to the NEXT model in
that provider's list. When every model on a provider is exhausted, move to
the next provider. Permanent errors (400/401) fail the whole chain fast.

Chain (config.yaml):
    primary:  lmstudio      (local — runs without auth)
    fallback: opencode-zen  (cloud — needs its api_key in the auth store)
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Optional

from . import auth_store
from core.config import load_config


class ProviderError(Exception):
    """Raised when every provider/model in the chain is exhausted."""


class ModelError(Exception):
    """Raised when ONE model is exhausted; the chain moves to the next."""


def _post_json(url: str, key: str, payload: dict, timeout: float = 60.0) -> dict:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Athena/0.1",
    }
    # Only send the Authorization header when a key is actually set.
    # Local servers (LM Studio) run fine without auth.
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # THE 400-BODY CAPTURE (the 08-14 diagnostic fix): a provider
        # rejection (400/401/429/5xx) carries the EXACT reason in the
        # response body — "The reasoning_content in the thinking mode
        # must be passed back", "model not found", etc. Surface it so
        # the operator sees WHY, not just the bare HTTP code.
        try:
            body = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            body = ""
        reason = f"HTTP {exc.code}"
        if body:
            reason += f": {body}"
        # The provider audit: every rejection reaches the logs WITH the
        # reason (the diagnostic contract — a provider outage must be
        # diagnosable, and the 400 must say which field/rule it hit).
        try:
            from core.logging import log_event
            log_event(3, f"provider HTTP {exc.code} ({url}): {body[:200]}",
                      source="providers", tool="provider", action="http_error")
        except Exception:
            pass
        raise ProviderError(reason) from exc


class Provider:
    """One provider endpoint. Config from authentication.json; the
    CREDENTIAL from .secret (the Operator's spec — api_key lives only there)."""

    def __init__(self, name: str, entry: dict):
        self.name = name
        self.base_url = str(entry.get("base_url", "")).rstrip("/")
        # THE .SECRET (the Operator's spec): credentials live ONLY in the
        # env-style .secret store (PROVIDER_API_KEY), never in config.
        try:
            from providers.auth_store import get_api_key
            self.api_key = get_api_key(name)
        except Exception:
            self.api_key = ""
        # The active model: the SELECTION wins (provider.selection in
        # config.yaml), else the first discovered model. It leads the
        # model order. (authentication.json stores credentials only —
        # there is no stored 'model' choice anymore.)
        #
        # IMPORTANT: read ONLY the explicit selection here — never the
        # fallback path (selection_for's fallback builds a ProviderChain,
        # which would recurse back into this constructor).
        active = ""
        try:
            from providers.selection import load_selection
            sel = load_selection()
            for t in ("reason", "vision", "embedding"):
                s = sel.get(t) or {}
                if s.get("provider") == name and s.get("model"):
                    active = s["model"]
                    break
        except Exception:
            pass
        # The preferred/active model first, then the rest of the discovered
        # list (deduped, order preserved).
        self.models = self._order_models(
            str(active or ""),
            entry.get("models") or [],
        )
        # THE FALLBACK MODEL (the 08-12 fix): if this provider IS the
        # configured fallback_provider, its fallback_model leads the model
        # order — the fallback serves the exact model the operator chose,
        # not the provider's arbitrary first model.
        try:
            from providers.selection import load_selection
            sel = load_selection()
            for t in ("reason", "vision", "embedding"):
                s = sel.get(t) or {}
                if s.get("fallback_provider") == name and s.get("fallback_model"):
                    fm = str(s["fallback_model"])
                    if fm and fm not in self.models:
                        self.models.insert(0, fm)
                    elif fm and self.models and self.models[0] != fm:
                        self.models.remove(fm)
                        self.models.insert(0, fm)
                    break
        except Exception:
            pass

    @staticmethod
    def _order_models(active: str, models: list) -> list:
        ordered: list[str] = []
        if active:
            ordered.append(active)
        for mid in models:
            mid = str(mid)
            if mid and mid not in ordered:
                ordered.append(mid)
        return ordered

    @property
    def _is_local(self) -> bool:
        """True for local endpoints (LM Studio etc.) that run without auth.

        Covers localhost, loopback, and PRIVATE-LAN addresses (10.x,
        192.168.x, 172.16-31.x) — a LAN LM Studio runs without an API key
        just like one on the same machine.
        """
        if "localhost" in self.base_url or "127.0.0.1" in self.base_url:
            return True
        import ipaddress
        host = self.base_url.split("://")[-1].split("/")[0].split(":")[0]
        try:
            return ipaddress.ip_address(host).is_private
        except Exception:
            return False

    @property
    def ready(self) -> bool:
        # A local server is ready even with no key (it runs without auth).
        return bool(self.api_key) or self._is_local

    def chat(self, messages: list[dict], model: str, timeout: float = 60.0) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        # THE DEEPSEEK THINKING FIELD (the 08-14 zen-400 fix, mirroring
        # the opencode provider): deepseek-v* models on the opencode
        # relay expect an explicit thinking control — sending plain
        # messages without it (or with conflicting reasoning fields)
        # returns HTTP 400. Athena does its OWN reasoning display from
        # reasoning_content, so the relay's thinking is explicitly
        # DISABLED — the same shape the provider sends for these models.
        flat = (model or "").strip().lower()
        if flat.startswith("deepseek-v") and not flat.startswith("deepseek-v3"):
            payload["thinking"] = {"type": "disabled"}
        data = _post_json(url, self.api_key, payload, timeout=timeout)
        return data["choices"][0]["message"]["content"]


class ProviderChain:
    """An ordered chain of providers, each with a model list.

    Retry ladder (the Operator's spec — model-first, provider-second):
        1. provider[0].model[0]  — up to max_attempts tries (transient only)
        2. provider[0].model[1]  — up to max_attempts tries
        3. ... up to MAX_MODELS_PER_PROVIDER models on provider[0]
        4. provider[1].model[0]  — up to max_attempts tries
        5. ... up to MAX_PROVIDERS providers total

    With the defaults (3 providers x 3 models x 3 attempts) that is a
    27-try ladder. The primary goal is to find a WORKING MODEL first —
    if a provider's models all fail, the provider is presumed down and
    we switch to the next provider. Raises ProviderError when the whole
    ladder is exhausted.
    """

    MAX_PROVIDERS = 3
    MAX_MODELS_PER_PROVIDER = 3

    def __init__(self, config: Optional[dict] = None, max_attempts: int = 3):
        cfg = config or load_config()
        providers = auth_store.list_providers()

        # Provider order (no chain): the REASON selection leads, then the
        # FALLBACK selection (the 08-12 fix: the config's fallback is the
        # TRUE 2nd rung — a rate-limited primary fails over to it), then
        # the rest of the catalog in its stored order. The selection is
        # the source of truth; the catalog is the menu.
        chain = list(providers.keys())[: self.MAX_PROVIDERS]

        # THE CONFIGURED MODELS (the 08-14 strict-config fix): the
        # ladder must honor the operator's selection EXACTLY — each
        # provider's model list LEADS with the configured model, then
        # the catalog. The auth store's raw order (e.g. zen leading
        # with big-pickle) must NEVER be tried before the configured
        # model — the operator picks the model, not the store.
        self._model_leads: dict[str, list[str]] = {}
        try:
            from providers.selection import load_selection
            sel = load_selection(cfg)
            for role in ("reason", "fallback"):
                s = (sel.get(role) or {})
                pname = s.get("provider")
                mname = s.get("model")
                if pname and mname:
                    self._model_leads.setdefault(pname, []).append(mname)
        except Exception:
            pass
        try:
            # Read ONLY the explicit selection — never selection_for's
            # fallback (which builds a ProviderChain → infinite recursion).
            from providers.selection import load_selection
            reason = (load_selection(cfg).get("reason") or {})
            rp = reason.get("provider")
            if rp and rp in chain:
                chain.remove(rp)
                chain.insert(0, rp)
            # THE FALLBACK PROVIDER AS THE 2ND RUNG (the 08-12 fix): the
            # config's fallback_provider follows the primary in the chain,
            # so a rate-limited/exhausted primary fails over to the
            # configured fallback — never to an arbitrary catalog model.
            fp = reason.get("fallback_provider")
            if fp and fp in chain:
                chain.remove(fp)
                if len(chain) > 0:
                    chain.insert(1, fp)
                else:
                    chain.append(fp)
        except Exception:
            pass

        self.providers = [
            self._build(name, providers) for name in chain
        ]
        self.providers = [p for p in self.providers if p is not None]
        self.max_attempts = max(1, min(max_attempts, 6))

    def _build(self, name, providers) -> Optional[Provider]:
        if not name:
            return None
        entry = providers.get(name)
        if not entry:
            return None
        provider = Provider(name, entry)
        # THE STRICT-CONFIG MODEL LEAD (the 08-14 fix): the configured
        # model for this provider leads its model list — the operator's
        # selection is tried FIRST, before any catalog model. A missing
        # configured model is a 404 (ModelError → next rung), never a
        # silent substitution with an arbitrary store model.
        leads = self._model_leads.get(name) or []
        if leads:
            models = list(provider.models or [])
            for m in reversed(leads):
                if m in models:
                    models.remove(m)
                models.insert(0, m)
            provider.models = models
        return provider

    def ready_provider(self) -> Optional[Provider]:
        """The first provider in the chain that is ready to chat."""
        for provider in self.providers:
            if provider.ready and provider.models:
                return provider
        return None

    def _chat_model(self, provider: Provider, model: str,
                    messages: list[dict], timeout: float) -> str:
        """Try ONE model up to max_attempts times (transient only)."""
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return provider.chat(messages, model=model, timeout=timeout)
            except urllib.error.HTTPError as exc:
                last_error = exc
                # THE PROVIDER AUDIT (the Operator's 08-12 metrics spec):
                # every failed provider attempt reaches the logs — a
                # provider outage must be diagnosable (which model, which
                # HTTP code, which attempt).
                try:
                    from core.logging import log_event
                    log_event(3, f"{provider.name}/{model}: HTTP {exc.code} "
                                 f"(attempt {attempt}/{self.max_attempts})",
                              source="providers", tool="provider",
                              action="chat_retry")
                except Exception:
                    pass
                if exc.code == 404:
                    # Model not found — model-level failure, move to next
                    # model immediately (no point retrying a missing model).
                    raise ModelError(f"{provider.name}/{model}: HTTP 404 (model not found)")
                if exc.code in (400, 401):
                    # Permanent — fail the whole chain fast.
                    raise ProviderError(f"{provider.name}/{model}: HTTP {exc.code}")
                # 429 / 5xx — transient, retry same model.
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                try:
                    from core.logging import log_event
                    log_event(3, f"{provider.name}/{model}: {type(exc).__name__} "
                                 f"(attempt {attempt}/{self.max_attempts})",
                              source="providers", tool="provider",
                              action="chat_retry")
                except Exception:
                    pass
            if attempt < self.max_attempts:
                # THE 429-AWARE BACKOFF (the Operator's 08-12 fix): a
                # rate-limit (429) needs a LONGER cool-down than the
                # generic 1s-8s backoff — the limit window is usually
                # 30-60s, and hammering it just extends the ban. BUT the
                # chain FAST-FAILOVERS on 429 (the 08-12 fix): the
                # primary only waits ONCE, briefly, then the fallback
                # provider serves — the long cool-down is the FALLBACK's
                # problem, not the primary's.
                if (isinstance(last_error, urllib.error.HTTPError)
                        and last_error.code == 429):
                    backoff = min(1.0 * (2.0 ** (attempt + 1)), 5.0)
                else:
                    backoff = min(1.0 * (2.0 ** (attempt - 1)), 8.0)
                time.sleep(backoff)
        # THE GRACEFUL 429 (the Operator's 08-12 fix): when the LAST error
        # is a rate-limit, raise a CLEAR message the turn can surface —
        # not a raw "[provider error: HTTP 429]" the model can't act on.
        if (isinstance(last_error, urllib.error.HTTPError)
                and last_error.code == 429):
            raise ModelError(
                f"{provider.name}/{model}: rate-limited (HTTP 429) after "
                f"{self.max_attempts} attempts — try again in a moment")
        raise ModelError(
            f"{provider.name}/{model}: failed after {self.max_attempts} attempts: {last_error}"
        )

    def chat(self, messages: list[dict], timeout: float = 60.0,
             turn_id: str = "") -> str:
        errors = []
        for provider in self.providers:
            if not provider.ready:
                errors.append(f"{provider.name}: not ready")
                self._log_chain(3, f"{provider.name} not ready — skipping",
                                provider.name)
                continue
            if not provider.models:
                errors.append(f"{provider.name}: no models set")
                self._log_chain(3, f"{provider.name} has no models — skipping",
                                provider.name)
                continue
            for model in provider.models[: self.MAX_MODELS_PER_PROVIDER]:
                try:
                    return self._chat_model(provider, model, messages, timeout=timeout)
                except ModelError as exc:
                    # THE 429 FAST-FAILOVER (the Operator's 08-12 fix): a
                    # RATE-LIMIT (429) means the WHOLE PROVIDER is
                    # throttled — retrying another model on the SAME
                    # provider just hits the same 429. Skip to the NEXT
                    # PROVIDER immediately (the fallback fires now, not
                    # after exhausting every model on the primary).
                    if "rate-limited" in str(exc):
                        errors.append(str(exc))
                        self._log_chain(
                            3, f"provider {provider.name} rate-limited "
                               f"({exc}) — fast-failover to next provider",
                            provider.name, model)
                        break  # next provider
                    # RETRY (the Operator's turn-retry spec): a TRANSIENT
                    # failure gets ONE retry on the same model before the
                    # chain falls forward — most hiccups pass on retry.
                    if turn_id:
                        try:
                            from core.turn_retry import should_retry
                            if should_retry(turn_id, provider.name, model,
                                            str(exc)):
                                self._log_chain(2, f"model {model} transient "
                                                   f"({exc}) — retrying once",
                                                provider.name, model)
                                try:
                                    return self._chat_model(
                                        provider, model, messages,
                                        timeout=timeout)
                                except Exception as exc2:
                                    errors.append(str(exc2))
                                    self._log_chain(
                                        3, f"model {model} failed on retry: "
                                           f"{exc2} — next model",
                                        provider.name, model)
                                    continue
                        except Exception:
                            pass  # retry logic never breaks the chain
                    errors.append(str(exc))
                    self._log_chain(3, f"model {model} failed: {exc} — next model",
                                    provider.name, model)
                    continue  # next model on this provider
                except ProviderError as exc:
                    # Permanent provider failure (auth, endpoint, etc.) —
                    # SKIP to the next provider; the chain's fallback is
                    # exactly what keeps the system talking. Only raise
                    # when the whole chain is exhausted (below).
                    errors.append(str(exc))
                    self._log_chain(3, f"provider {provider.name} failed ({exc}) — "
                                       "next provider",
                                    provider.name)
                    break  # next provider
        # Chain exhausted — this is a real ERROR the nurse must see.
        self._log_chain(4, "provider chain exhausted: " + "; ".join(errors),
                        "chain")
        raise ProviderError("; ".join(errors))

    def _log_chain(self, level: int, message: str, provider: str,
                   model: str = "") -> None:
        """Log a chain event through the metric logger (never raises)."""
        try:
            from metrics.logger import log
            log(level, message, source="providers", tool="chain",
                action="provider_chain", target=model or provider)
        except Exception:
            pass

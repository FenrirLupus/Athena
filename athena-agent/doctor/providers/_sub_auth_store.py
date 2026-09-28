"""Auth store test — the credential/config split (the Operator's spec).

  • authentication.json = CONFIG only (base_url, models) — api_key DROPPED
  • .secret = CREDENTIALS ONLY (env-style PROVIDER_API_KEY lines)

Round-trips are verified against a temp store (never the real one), and
credentials are never printed.
"""
from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> list[dict]:
    from providers import auth_store
    from core import secret_store

    checks = []
    with tempfile.TemporaryDirectory() as td:
        # The two files are SEPARATE (the Operator's spec): .secret = the
        # env-style store, authentication.json = the config store.
        secret_path = Path(td) / ".secret"
        auth_path = Path(td) / "authentication.json"
        original = getattr(auth_store, "AUTH_PATH", None)
        original_secret = secret_store.SECRET_FILE
        try:
            if original is not None:
                auth_store.AUTH_PATH = auth_path
            secret_store.SECRET_FILE = secret_path
            # Save a provider WITH an api_key — the CONFIG store must
            # drop it (the credential lives only in .secret).
            auth_store.save_provider("test-provider",
                                     {"base_url": "http://x", "api_key": "secret-123"})
            got = auth_store.get_provider("test-provider")
            checks.append({
                "name": "config store drops api_key",
                "status": "ok" if got and "api_key" not in got
                and got.get("base_url") == "http://x" else "fail",
                "detail": "authentication.json = config only (api_key dropped)",
            })
            # The credential goes to .secret via the env-style key.
            ok = secret_store.set_api_key("test-provider", "secret-123")
            key_back = secret_store.get_api_key("test-provider")
            checks.append({
                "name": "credential lives in .secret (env-style)",
                "status": "ok" if ok and key_back == "secret-123" else "fail",
                "detail": "TEST_PROVIDER_API_KEY in .secret (never printed)",
            })
            # The KNOWN-PROVIDER registry (the Operator's spec): every popular
            # provider's API_KEY variable exists with a NULL default, so
            # the user MUST provide the applicable keys.
            from core.secret_store import seed, KNOWN_PROVIDERS
            seeded = seed()
            n_registered = len(KNOWN_PROVIDERS)
            all_present = all(
                f"{p.upper().replace('-', '_')}_API_KEY" in seeded
                for p in KNOWN_PROVIDERS)
            checks.append({
                "name": "secret registry: all known providers, null defaults",
                "status": "ok" if all_present and n_registered >= 30 else "fail",
                "detail": f"{n_registered} provider keys registered",
            })
            # save_provider round-trips the config.
            listed = auth_store.list_providers()
            checks.append({
                "name": "list providers (config)",
                "status": "ok" if "test-provider" in listed else "fail",
                "detail": f"keys={list(listed.keys())}",
            })
        finally:
            auth_store.AUTH_PATH = original
            secret_store.SECRET_FILE = original_secret

    # The PROVIDER CATALOG (the Operator's spec): every provider's base_url is
    # known and auto-selected at setup — the user provides only the key.
    from providers.provider_catalog import list_catalog, get_catalog_entry
    cat = list_catalog()
    n_cat = len(cat)
    sample = ["openai", "anthropic", "deepseek", "opencode-go", "nvidia",
              "xiaomi", "lmstudio", "custom"]
    ok_known = all(get_catalog_entry(s) is not None for s in sample)
    ok_local = get_catalog_entry("lmstudio").get("local") is True
    checks.append({
        "name": "provider catalog: base_urls auto-selectable",
        "status": "ok" if n_cat >= 40 and ok_known and ok_local else "fail",
        "detail": f"{n_cat} providers catalogued; local flagged",
    })

    # Athena is HER OWN PROVIDER (the Operator's spec): her MCP is registered
    # as a provider (base_url → /mcp, key → ATHENA_API_KEY), and the
    # /mcp/v1/chat/completions conversion endpoint is mounted.
    athena = get_catalog_entry("athena")
    ok_self = athena is not None and athena["base_url"].endswith("/mcp") \
        and "ATHENA_API_KEY" in athena.get("key_env", [])
    from web.mcp import router as mcp_router
    mcp_paths = [r.path for r in mcp_router.routes]
    ok_conv = "/mcp/v1/chat/completions" in mcp_paths
    checks.append({
        "name": "athena is her own provider (MCP conversion layer)",
        "status": "ok" if ok_self and ok_conv else "fail",
        "detail": f"catalog={ok_self} conversion={ok_conv}",
    })

    # Athena's ADDRESS (the Operator's spec): the standard LOCAL bind —
    # 127.0.0.1:51420 in config, and her self-provider base_url matches.
    from core.config import load_config as _lc
    _srv = _lc().get("server", {})
    ok_port = _srv.get("port") == 51420
    ok_bind = str(_srv.get("host", "")) in ("127.0.0.1", "localhost")
    ok_self = (athena or {}).get("base_url", "") == \
        "http://127.0.0.1:51420/mcp"
    checks.append({
        "name": "athena address: local bind 127.0.0.1:51420",
        "status": "ok" if ok_port and ok_bind and ok_self else "fail",
        "detail": f"port={ok_port} bind={ok_bind} self={ok_self}",
    })
    return checks

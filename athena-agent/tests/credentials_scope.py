"""Precedence: an agent's own model and keys first, the global ones only as fallback.

Four cases, because "agent first" is only true if each one holds:
  1 · the agent declares a model AND has its own key   → both are used
  2 · the agent declares a model, no key of its own    → its model, the GLOBAL key
  3 · the agent declares nothing                       → the global provider
  4 · the agent names a different WIRE FORMAT          → its own endpoint, never the global base_url
"""
import copy
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import loader
from core import agentconfig
from providers import base as provider_base
from server.channels import ChannelManager
from tools import registry as treg

cfg = copy.deepcopy(loader.load())
cfg["agents"]["idle_sleep_minutes"] = 0
cfg["model"] = {**cfg.get("model", {}), "provider": "openai",
                "base_url": "https://global.example/v1", "name": "global-model", "window": 32768}
checks = {}

with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "profiles"
    root.mkdir()
    mgr = ChannelManager(cfg, None, treg.default_registry(cfg), root)

    # case 1 · own model + own key
    own = root / "own"
    own.mkdir(parents=True)
    (own / "config.yaml").write_text(
        "model:\n  provider: openai\n  base_url: https://own.example/v1\n  name: own-model\n"
        "  key_name: OWN_KEY\n", encoding="utf-8")
    provider_base.write_secret("OWN_KEY", "own-value", root=own)
    prov = mgr.provider_for("own", own)
    checks["own_model_used"] = prov.model == "own-model" and "own.example" in prov.base_url
    checks["own_key_used"] = provider_base.api_key("openai", "OWN_KEY", root=own) == "own-value"
    checks["own_key_not_global"] = "OWN_KEY" not in provider_base.read_secrets()

    # case 2 · own model, no key of its own → the global key resolves
    shared = root / "shared"
    shared.mkdir(parents=True)
    (shared / "config.yaml").write_text(
        "model:\n  provider: openai\n  base_url: https://shared.example/v1\n  name: shared-model\n",
        encoding="utf-8")
    provider_base.write_secret("GLOBAL_TEST_KEY", "global-value")          # global scope
    prov2 = mgr.provider_for("shared", shared)
    checks["own_model_no_own_key"] = prov2.model == "shared-model"
    checks["falls_back_to_global_key"] = (
        provider_base.api_key("openai", "GLOBAL_TEST_KEY", root=shared) == "global-value")

    # case 3 · nothing declared → the global provider object
    bare = root / "bare"
    bare.mkdir(parents=True)
    (bare / "config.yaml").write_text("persona:\n  name: Bare\n", encoding="utf-8")
    checks["no_model_means_global_provider"] = mgr.provider_for("bare", bare) is mgr.provider

    # local shadows global for the same name
    provider_base.write_secret("OWN_KEY", "global-same-name")              # global line, same name
    checks["local_shadows_global"] = (
        provider_base.api_key("openai", "OWN_KEY", root=own) == "own-value")

    # case 4 · a different wire format never inherits the global base_url
    anth = {"model": {"provider": "anthropic", "name": "claude-x"}}
    merged = agentconfig.as_cfg(anth, cfg)
    checks["no_url_inheritance"] = merged["model"]["base_url"].startswith("https://api.anthropic.com")
    # and an agent with the SAME format does inherit it (that is the fallback working, not a bug)
    same = agentconfig.as_cfg({"model": {"provider": "openai", "name": "x"}}, cfg)
    checks["same_format_inherits_global_url"] = same["model"]["base_url"] == "https://global.example/v1"

    # cleanup the global test line so a real .secret is not polluted
    import os
    from providers.base import SECRET_FILE
    if SECRET_FILE.exists():
        keep = [l for l in SECRET_FILE.read_text(encoding="utf-8").splitlines()
                if not l.startswith(("GLOBAL_TEST_KEY=", "OWN_KEY="))]
        SECRET_FILE.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        os.chmod(SECRET_FILE, 0o600)

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

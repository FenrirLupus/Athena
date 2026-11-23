"""The wake check and the adapter share one resolver — a private copy of the logic fails this suite.

The bug this exists for: `_check_provider` asked for a key under the wire format (`openai`) while the adapter asked
under the lane's declared name, so a correctly configured lane failed its own wake check. The fix is structural —
one function, four call sites, no second decision point — and this test asserts the structure, not the outcome.
"""
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from config import loader
from providers import base
from providers.mock import MockProvider
from server.channels import ChannelManager
from server.lifecycle import ChannelLifecycle
from tools import registry as treg

CHECKS = {}
cfg = loader.load()
MODEL = {"provider": "openai", "base_url": "https://example.invalid/v1", "name": "m", "key_name": "SENTINEL_KEY"}

original = base.resolve_key
try:
    base.resolve_key = lambda *a, **k: {"name": "SENTINEL_KEY", "value": "x", "source": "lane-secret",
                                       "looked": [], "declared": "SENTINEL_KEY", "registry": ""}
    built = base.build({"model": MODEL}, root=None)
    CHECKS["adapter_uses_the_resolver"] = getattr(built, "key_name", "") == "SENTINEL_KEY"

    with tempfile.TemporaryDirectory() as td:
        profiles = Path(td) / "profiles"
        profiles.mkdir()
        mgr = ChannelManager(dict(cfg, metrics={"enabled": False}), MockProvider(),
                             treg.default_registry(cfg), profiles)
        mgr.create("wakecheck")
        channel = mgr.channel("wakecheck", ensure=True)
        lifecycle = ChannelLifecycle(mgr)
        check = lifecycle._check_provider(channel)
        CHECKS["wake_check_uses_the_resolver"] = check["ok"] is True
finally:
    base.resolve_key = original

# and unpatched, the answer is the value's presence, not a second computation
with tempfile.TemporaryDirectory() as td:
    profiles = Path(td) / "profiles"
    (profiles / "wakecheck").mkdir(parents=True)
    (profiles / "wakecheck" / ".secret").write_text("ODD_NAME=value-here\n", encoding="utf-8")
    model = {"provider": "openai", "base_url": "https://example.invalid/v1", "name": "m", "key_name": "ODD_NAME"}
    resolved = base.resolve_key(model, root=profiles / "wakecheck", lane="wakecheck")
    CHECKS["resolver_finds_a_lane_local_key"] = resolved["value"] == "value-here"

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
sys.exit(0 if all(CHECKS.values()) else 1)

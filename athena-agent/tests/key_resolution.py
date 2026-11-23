"""Key resolution: one function decides which `.secret` line holds an endpoint's key, and where it looked."""
import json
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from providers import base

CHECKS = {}
MODEL = {"provider": "openai", "base_url": "https://api.deepseek.com/v1", "name": "deepseek-flash"}

with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / "athena"
    profiles = athena / "profiles"
    lane_a = profiles / "alpha"
    lane_b = profiles / "beta"
    for lane in (lane_a, lane_b):
        lane.mkdir(parents=True)
    (athena / "provider.json").write_text(json.dumps({
        "deepseek": {"base_url": "https://api.deepseek.com/v1", "format": "openai",
                     "keys": [{"name": "DEEPSEEK_API_KEY", "scope": "global", "agent": ""}]},
    }), encoding="utf-8")
    (athena / ".secret").write_text("DEEPSEEK_API_KEY=shared-value\n", encoding="utf-8")
    (lane_b / ".secret").write_text("DEEPSEEK_API_KEY=lane-value\n", encoding="utf-8")

    # declared name, in the lane's own file first: this is the two-agents case that must never regress
    declared = dict(MODEL, key_name="DEEPSEEK_API_KEY")
    a = base.resolve_key(declared, root=lane_a, lane="alpha")
    b = base.resolve_key(declared, root=lane_b, lane="beta")
    CHECKS["declared_lane_shadows_global"] = (a["value"] == "shared-value" and b["value"] == "lane-value"
                                              and b["source"] in ("lane-secret", "global-secret"))

    # no declared name: the registry names it, by endpoint host
    resolved = base.resolve_key(MODEL, root=lane_a, lane="alpha")
    CHECKS["registry_resolves_by_host"] = (resolved["name"] == "DEEPSEEK_API_KEY"
                                           and resolved["registry"] == "deepseek"
                                           and resolved["value"] == "shared-value")

    # a declared name that resolves nowhere stops, instead of borrowing the wire-format line
    missing = base.resolve_key(dict(MODEL, key_name="DEEPSEEK_WORK_KEY"), root=lane_a, lane="alpha")
    CHECKS["declared_missing_stops"] = missing["source"] == "declared-missing" and missing["value"] == ""

    # nothing anywhere: the answer is 'none', and `looked` says where it tried
    empty = Path(td) / "nowhere" / "athena"
    (empty / "profiles" / "solo").mkdir(parents=True)
    none = base.resolve_key(dict(MODEL, base_url="https://example.invalid/v1"),
                            root=empty / "profiles" / "solo", lane="solo")
    CHECKS["nothing_resolves_to_none"] = none["source"] in ("none", "declared-missing") and not none["value"]
    CHECKS["looked_is_recorded"] = isinstance(none["looked"], list)

    # an unreadable registry is reported, not repaired — and with no other naming, nothing is found. Borrowing
    # another line here would be the old silent fallthrough; the correct answer is "none, and say so".
    (athena / "provider.json").write_text("{not json", encoding="utf-8")
    broken = base.resolve_key(MODEL, root=lane_a, lane="alpha")
    CHECKS["broken_registry_does_not_invent_a_key"] = (broken["value"] == ""
                                                       and broken["source"] in ("none", "wire-format"))

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
sys.exit(0 if all(CHECKS.values()) else 1)

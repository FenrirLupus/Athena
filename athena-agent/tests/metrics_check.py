"""Metrics: every turn leaves a record, including the ones that fail.

The point of recording a failed turn is that it still spent tokens and seconds; a log that only measures
successes flatters the system exactly where it is expensive.
"""
import copy
import sys
import tempfile
import time
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from config import loader
from core import metrics as metrics_mod
from providers.mock import MockProvider
from server.channels import ChannelManager
from tools import registry as treg

cfg = copy.deepcopy(loader.load())
cfg.setdefault("agents", {})["idle_sleep_minutes"] = 0
cfg["metrics"] = {"enabled": True, "usd_per_mtok": 1.0, "sample_resources": False}
checks = {}

with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "profiles"
    root.mkdir()
    mgr = ChannelManager(cfg, MockProvider(), treg.default_registry(cfg), root)
    mgr.create("meter")

    mgr.submit("meter", "hello", wait_s=30)
    # it must land in THIS agent's own profile, not a shared installation log
    per_agent = root / "meter" / "metrics" / "turns.jsonl"
    checks["in_profile"] = per_agent.exists()
    checks["row_in_profile"] = per_agent.exists() and len(
        [l for l in per_agent.read_text(encoding="utf-8").splitlines() if l.strip()]) == 1
    checks["no_shared_log"] = not (root.parent / "metrics" / "turns.jsonl").exists()
    rows = metrics_mod.read(root, channel="meter")
    checks["one_record"] = len(rows) == 1
    row = rows[0] if rows else {}
    checks["has_timing"] = row.get("seconds") is not None and row.get("inference_seconds") is not None
    checks["has_tokens"] = int(row.get("prompt_tokens") or 0) > 0
    # $1 per 1M tokens, computed from what was actually reported
    expected = round(int(row.get("total_tokens") or 0) / 1_000_000 * 1.0, 6)
    checks["cost_matches_rate"] = abs(float(row.get("cost_usd") or 0) - expected) < 1e-9
    checks["rate_recorded"] = row.get("usd_per_mtok") == 1.0
    checks["queue_wait_recorded"] = row.get("queue_wait_s") is not None
    checks["channel_named"] = row.get("channel") == "meter"

    # a failing provider must still produce a row, marked as an error
    class Broken(MockProvider):
        def call(self, messages, **kwargs):
            raise RuntimeError("provider exploded")

    mgr._providers["meter"] = Broken()
    mgr.submit("meter", "and now fail", wait_s=30)
    rows = metrics_mod.read(root, channel="meter")
    checks["failure_recorded"] = len(rows) == 2
    checks["failure_marked"] = str((rows[-1] if rows else {}).get("status") or "").startswith("error")
    checks["failure_has_error_text"] = bool((rows[-1] if rows else {}).get("error"))

    summary = metrics_mod.summary(root)
    checks["summary_totals"] = summary["total"]["turns"] == 2 and summary["total"]["errors"] == 1
    checks["summary_by_channel"] = summary["channels"].get("meter", {}).get("turns") == 2
    # the key must be the recorded name, not the lane's internal id (that printed a uuid in the CLI table)
    checks["summary_channel_key_is_named"] = not any("-" in name and len(name) > 32 for name in summary["channels"])
    scoped = metrics_mod.summary(root, channel="meter")
    checks["scoped_summary"] = scoped["total"]["turns"] == 2 and scoped["scope"] == "meter"

    # disabled means disabled: no file growth
    off = copy.deepcopy(cfg)
    off["metrics"] = {"enabled": False}
    before = len(metrics_mod.read(root, channel="meter"))
    mgr.cfg = off
    mgr._providers["meter"] = MockProvider()
    mgr.submit("meter", "not measured", wait_s=30)
    checks["disabled_writes_nothing"] = len(metrics_mod.read(root, channel="meter")) == before

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
_green = all(checks.values())
print("\nRESULT:", "PASS" if _green else "FAIL")
# exit code must agree with the printed verdict: a suite that says FAIL and exits 0 is a trap for any
# runner that trusts the exit code alone.
sys.exit(0 if _green else 1)

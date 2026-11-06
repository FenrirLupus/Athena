"""Retention: folding the oldest metric rows must not lose a single one.

The invariant is the same one the session vault follows — durable first, then rewrite. A rollup that loses rows
would silently rewrite an agent's cost history, which is worse than a log that grows.
"""
import json
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import metrics

checks = {}

with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "profiles"
    channel = "meter"
    target = root / channel / "metrics" / "turns.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)

    rows = [{"ts": f"2026-11-01T00:{i // 60:02d}:{i % 60:02d}Z", "channel": channel, "status": "completed",
             "prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110,
             "cost_usd": 0.00011, "seconds": 1.0, "inference_seconds": 1.0,
             "tool_rounds": 0, "finish_reasons": ["stop"]} for i in range(200)]
    target.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    before = metrics.summary(root, channel=channel)["total"]
    result = metrics.rollup(root, channel, keep=50)

    checks["rolled_oldest"] = result["rolled"] == 150
    checks["kept_newest"] = result["kept"] == 50 and len(metrics.read(root, channel=channel)) == 50
    checks["archive_written"] = bool(result.get("archived_file")) and Path(result["archived_file"]).exists()
    checks["archive_has_every_original_row"] = (
        len([l for l in Path(result["archived_file"]).read_text(encoding="utf-8").splitlines() if l.strip()]) == 200)
    checks["sha256_recorded"] = bool(json.loads((target.parent / "rollup.json").read_text())["periods"][0])

    after = metrics.summary(root, channel=channel)
    checks["surviving_rows_total"] = after["total"]["turns"] == 50
    checks["archived_totals_present"] = after["archived"]["totals"]["turns"] == 150
    checks["no_row_lost"] = (after["total"]["turns"] + after["archived"]["totals"]["turns"]) == before["turns"]
    checks["tokens_accounted"] = (
        after["total"]["total_tokens"] + after["archived"]["totals"]["total_tokens"] == before["total_tokens"])
    checks["cost_accounted"] = abs((after["total"]["cost_usd"] + after["archived"]["totals"]["cost_usd"])
                                   - before["cost_usd"]) < 1e-9

    # a second rollup folds what is new without disturbing the first period
    more = [dict(rows[0], ts="2026-11-02T00:00:00Z") for _ in range(80)]
    target.write_text(target.read_text(encoding="utf-8") + "".join(json.dumps(r) + "\n" for r in more),
                      encoding="utf-8")
    metrics.rollup(root, channel, keep=50)
    ledger = json.loads((target.parent / "rollup.json").read_text())
    checks["two_periods"] = len(ledger["periods"]) == 2
    # 50 rows survived the first fold, +80 appended = 130, keep 50 → 80 rolled; 150 + 80 = 230
    checks["ledger_totals_accumulate"] = ledger["totals"]["turns"] == 230
    final = metrics.summary(root, channel=channel)
    checks["nothing_lost_across_two_rollups"] = (
        final["total"]["turns"] + final["archived"]["totals"]["turns"]) == 200 + 80

    # and it is a no-op when the log is already small
    checks["noop_when_small"] = metrics.rollup(root, channel, keep=1000)["rolled"] == 0

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

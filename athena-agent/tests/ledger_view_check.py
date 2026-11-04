"""The ledger reader's contract, as the interface consumes it.

Green against the wrong shape is worse than no suite, so this asserts the documented fields rather than whatever the
module happens to return: `cursor`, `truncated`, `rows[]` with `id/at/at_epoch/level/team/kind/path/lane/text`.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import ledger_view

checks: dict[str, bool] = {}

with tempfile.TemporaryDirectory() as td:
    sec = Path(td)
    rows = [
        {"at": 1000.0, "team": "blue", "kind": "inbound-401", "path": "/x", "lane": "", "detail": "a"},
        {"at": 1005.5, "team": "red", "kind": "scope-refusal", "path": "/y", "lane": "default", "detail": "b"},
        {"at": 1002.0, "team": "blue", "kind": "heartbeat", "path": "/z", "lane": "", "detail": "c"},
    ]
    lines = [json.dumps(r) for r in rows]
    lines.insert(2, '{"at": 1001.0, "team": "blu')
    (sec / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    first = ledger_view.read_events(sec)
    checks["contract_keys"] = set(first) >= {"cursor", "truncated", "rows"}
    checks["row_fields"] = set(first["rows"][-1]) >= {"id", "at", "at_epoch", "level", "team", "kind", "path", "lane", "text"}
    checks["oldest_first"] = [r["at_epoch"] for r in first["rows"]] == sorted(r["at_epoch"] for r in first["rows"])
    checks["corrupt_line_counted"] = first["skipped"] == 1
    checks["levels_derived"] = ([r["level"] for r in first["rows"]] == ["bad", "info", "bad"])
    checks["iso_and_epoch_agree"] = first["rows"][0]["at"].endswith("Z") and first["rows"][0]["at_epoch"] == 1000.0
    checks["cursor_is_newest"] = first["cursor"] == 1005.5
    checks["no_truncation_at_default"] = first["truncated"] is False

    small = ledger_view.read_events(sec, limit=1)
    checks["truncated_flag_set"] = small["truncated"] is True and len(small["rows"]) == 1
    checks["since_cursor_repeats_nothing"] = ledger_view.read_events(sec, since=first["cursor"])["rows"] == []
    checks["future_cursor_returns_nothing"] = ledger_view.read_events(sec, since=9999999999)["rows"] == []
    checks["min_level_filters"] = [r["level"] for r in ledger_view.read_events(sec, min_level="warn")["rows"]] == ["bad", "bad"]

    for bad_limit in (0, -1, 501):
        try:
            ledger_view.read_events(sec, limit=bad_limit)
            checks[f"limit_{bad_limit}_refused"] = False
        except ValueError:
            checks[f"limit_{bad_limit}_refused"] = True
    try:
        ledger_view.read_events(sec, min_level="loud")
        checks["bad_level_refused"] = False
    except ValueError:
        checks["bad_level_refused"] = True
    try:
        ledger_view.read_events(sec, since=-1)
        checks["negative_since_refused"] = False
    except ValueError:
        checks["negative_since_refused"] = True

    blank = Path(td) / "none"
    blank.mkdir()
    empty = ledger_view.read_events(blank)
    checks["missing_ledger_is_empty"] = empty["rows"] == [] and empty["skipped"] == 0

    profile = Path(td) / "profile"
    (profile / "metrics").mkdir(parents=True)
    (profile / "metrics" / "turns.jsonl").write_text(
        "\n".join(json.dumps({"ts": "2026-11-04T06:00:0%dZ" % i, "calls": i}) for i in range(3)) + "\n", encoding="utf-8")
    turns = ledger_view.turn_rows(profile)
    checks["turns_read_oldest_first"] = [t["calls"] for t in turns] == [0, 1, 2]
    merged = ledger_view.timeline(first["rows"], turns, limit=10)
    checks["timeline_names_source"] = {r["source"] for r in merged} == {"ledger", "turn"}
    keys = [r["at_epoch"] for r in merged]
    checks["timeline_ordered"] = keys == sorted(keys)

failed = [n for n, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"{len(checks) - len(failed)}/{len(checks)} — RESULT: {'PASS' if not failed else 'FAIL'}")
sys.exit(0 if not failed else 1)

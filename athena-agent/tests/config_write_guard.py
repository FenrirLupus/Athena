"""The write guard: a config that cannot be read back is not written, and a repair keeps the evidence."""
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import agentconfig
from core import configguard
from core import context_window

CHECKS = {}

CHECKS["parse_reports_a_line"] = configguard.parse_report("a:\n  b: [\n")["line"] == 3
CHECKS["parse_reports_duplicates"] = configguard.parse_report("a: 1\na: 2\n")["duplicates"] == ["a"]
_soh = configguard.parse_report("a: 1\x01\n")
CHECKS["control_char_has_no_line"] = (not _soh["ok"]) and _soh["line"] is None

with tempfile.TemporaryDirectory() as td:
    target = Path(td) / "config.yaml"
    target.write_text("model:\n  name: keep-me\n", encoding="utf-8")
    before = target.read_text()

    try:
        configguard.guard("model:\n  b: [\n", target, preimage=before)
    except configguard.WriteRefused as exc:
        refused = "line" in str(exc) and "unchanged" in str(exc)
    else:
        refused = False
    CHECKS["refuses_and_names_the_line"] = refused
    CHECKS["refusal_leaves_the_file_alone"] = target.read_text() == before
    CHECKS["refusal_leaves_no_temp_file"] = not list(Path(td).glob(".*tmp"))

    # a repair over an unreadable pre-image: the broken text is copied aside, then the good one is written
    broken = Path(td) / "broken.yaml"
    broken.write_text("model:\n  b: [\n", encoding="utf-8")
    configguard.guard("model:\n  name: fixed\n", broken, preimage=broken.read_text())
    broken.write_text("model:\n  name: fixed\n", encoding="utf-8")
    preimages = list(Path(td).glob("broken.yaml.broken-*"))
    CHECKS["repair_keeps_the_evidence"] = len(preimages) == 1 and "b: [" in preimages[0].read_text()

    # a lane write goes through the guard and reads back
    lane = Path(td) / "profiles" / "guarded"
    lane.mkdir(parents=True)
    agentconfig.write(lane, {"persona": {"name": "Guarded"}, "model": {"name": "deepseek-flash"}})
    written = (lane / "config.yaml").read_text(encoding="utf-8")
    CHECKS["lane_write_parses"] = configguard.parse_report(written)["ok"]

    broken_lane = Path(td) / "profiles" / "hurt"
    broken_lane.mkdir(parents=True)
    (broken_lane / "config.yaml").write_text("model:\n  b: [\n", encoding="utf-8")
    status = agentconfig.read_status(broken_lane)
    CHECKS["read_status_reports_the_line"] = status["present"] and not status["ok"] and status["line"] == 3
    CHECKS["read_still_returns_empty"] = agentconfig.read(broken_lane) == {}

    # the window writer must not splice a control byte into the file: the key is inserted as text at the
    # provider line's own indentation, and the result parses
    window = Path(td) / "window.yaml"
    window.write_text("model:\n  context_window_provider: 0\n", encoding="utf-8")
    context_window.write_provider_window(window, 1048576, "deepseek-flash")
    text = window.read_text()
    CHECKS["window_write_has_no_control_byte"] = "\x01" not in text
    CHECKS["window_write_parses"] = configguard.parse_report(text)["ok"]
    CHECKS["window_write_inserts_the_model_key"] = "context_window_provider_model" in text

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
sys.exit(0 if all(CHECKS.values()) else 1)

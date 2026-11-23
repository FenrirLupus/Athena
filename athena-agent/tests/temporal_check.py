"""A lane's sense of elapsed time: the phrases, the record, the block, and the refusal to be silent.

The failure this suite exists for is not a crash. It is an agent answering a question asked sixteen days ago as
though it had been asked this minute, because nothing in the prompt said otherwise. So every check here is
written to bite: each one has an input that must fail it, and the numbers asserted are the ones the design
measured (documentation/proposals/temporal/design.md §9).
"""
import json
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from config import loader
from context import stack
from core import session as session_mod
from core import temporal
from core import timefmt

CHECKS = {}

CFG = {"temporal": {"enabled": True, "seam_after_minutes": 15, "continuous_under_seconds": 60}}

NOW = 1_792_000_000.0                      # a fixed instant: every rendered figure is pinned to it
HOURS_1 = NOW - 3600
DAYS_1 = NOW - 86400
WEEKS_1 = NOW - 604800


def _record(gap_s, last_line="metrics.", tool_names=("metrics", "memory"), tool_age=7200):
    return {"last_exchange_ts": timefmt.stamp_from(NOW - gap_s), "gap_s": gap_s,
            "last_line": last_line, "last_tool_names": list(tool_names),
            "last_tool_ts": timefmt.stamp_from(NOW - tool_age)}


def _rows():
    base = NOW - 90000
    return [
        {"role": "user", "content": "[Message] what did we decide about the retry limit?", "ts": timefmt.stamp_from(base)},
        {"role": "assistant", "content": "[Message] Three attempts, then stop.", "ts": timefmt.stamp_from(base + 3)},
        {"role": "tool", "name": "metrics", "content": "cpu 3%", "ts": timefmt.stamp_from(base + 10)},
        {"role": "tool", "name": "memory", "content": "row", "ts": timefmt.stamp_from(base + 11)},
    ]


# -- phrases ---------------------------------------------------------------
CHECKS["elapsed_units"] = (temporal.elapsed(0) == "0 s"
                           and temporal.elapsed(59) == "59 s"
                           and temporal.elapsed(60) == "1 min"
                           and temporal.elapsed(3600) == "1 h"
                           and temporal.elapsed(90000) == "1 day 1 h"
                           and temporal.elapsed(2700) == "45 min"
                           and temporal.elapsed(1_449_205) == "2 weeks 2 days"
                           # floored, not rounded: 59.9 s did not become a minute that never passed
                           and temporal.elapsed(59.9) == "59 s")

CHECKS["band_edges"] = (temporal.band(59, CFG) == "continuous"
                        and temporal.band(60, CFG) == "sitting"
                        and temporal.band(899, CFG) == "sitting"
                        # the quantum is the away edge: 15 minutes of silence is away, not "one sitting"
                        and temporal.band(900, CFG) == "away"
                        and temporal.band(3599, CFG) == "away"
                        and temporal.band(3600, CFG) == "hours"
                        and temporal.band(86399, CFG) == "hours"
                        and temporal.band(86400, CFG) == "days"
                        and temporal.band(604_799, CFG) == "days"
                        and temporal.band(604_800, CFG) == "weeks"
                        # a lane configured at the old hour has no away band: the edge collapses onto 1 h
                        and temporal.band(1800, {"temporal": {"seam_after_minutes": 60}}) == "sitting")

# -- the anchor ------------------------------------------------------------
_a1 = temporal.anchor(_record(1_449_205), NOW, CFG)
_a2 = temporal.anchor(_record(1_449_205), NOW, CFG)
CHECKS["anchor_is_pure"] = _a1 == _a2 and "# Now" in _a1

_h = temporal.anchor(_record(3600), HOURS_1 + 3600, CFG)
_d = temporal.anchor(_record(86400), DAYS_1 + 86400, CFG)
_w = temporal.anchor(_record(604800), WEEKS_1 + 604800, CFG)
_a = temporal.anchor(_record(1800), NOW, CFG)
CHECKS["anchor_bands_differ"] = (len({_h, _d, _w, _a}) == 4
                                 and "Hours have passed" in _h
                                 and "day or more" in _d
                                 and "week or more" in _w
                                 and "Minutes have passed" in _a)

CHECKS["anchor_omits_when_empty"] = (temporal.anchor({}, NOW, CFG) == ""
                                     and temporal.anchor({"last_exchange_ts": "", "gap_s": None}, NOW, CFG) == "")

CHECKS["anchor_carries_own_line_and_tools"] = ('Your last line then: "metrics."' in _a1
                                               and "Last tool reads: metrics, memory (" in _a1)

# -- the seam --------------------------------------------------------------
CHECKS["seam_due_threshold"] = (not temporal.seam_due(899, CFG)
                                and temporal.seam_due(900, CFG)
                                and temporal.seam_due(1, {"temporal": {"seam_after_minutes": 0}}))

_after = timefmt.stamp_from(NOW + 5)
_rows_with_seam = _rows() + [{"role": "user", "content": "[Time] …", "ts": _after, "kind": "seam"}]
CHECKS["seam_once_per_gap"] = (temporal.seam_already_written(_rows_with_seam, timefmt.stamp_from(NOW))
                               and not temporal.seam_already_written(_rows(), timefmt.stamp_from(NOW))
                               # a seam OLDER than the last exchange belongs to an earlier gap: it must not
                               # suppress the row for this one
                               and not temporal.seam_already_written(_rows_with_seam, timefmt.stamp_from(NOW + 60)))

CHECKS["seam_has_no_ago"] = " ago" not in temporal.seam_text(_record(1_449_205), NOW)

# the persisted entry is a marker, not a log: one per gap AND one per day
_today_seam = [{"role": "user", "content": "[Time] …", "ts": timefmt.stamp_from(NOW - 300), "kind": "seam"}]
_yesterday_seam = [{"role": "user", "content": "[Time] …",
                    "ts": timefmt.stamp_from(NOW - 86400), "kind": "seam"}]
CHECKS["seam_once_per_day"] = (temporal.seam_written_today(_today_seam, NOW)
                               and not temporal.seam_written_today(_yesterday_seam, NOW)
                               and not temporal.seam_written_today(_rows(), NOW))
CHECKS["seam_can_be_off"] = (temporal.seam_room_today(CFG)
                             and not temporal.seam_room_today({"temporal": {"seam_max_per_day": 0}}))

# -- reading the transcript ------------------------------------------------
CHECKS["newest_exchange_skips_seams"] = temporal.newest_exchange(_rows_with_seam)["role"] == "assistant"

_batch = temporal.newest_tool_batch(_rows())
CHECKS["tool_batch_names"] = (_batch is not None and _batch[0] == ["memory", "metrics"]
                              and _batch[1] == _rows()[2]["ts"]
                              and temporal.newest_tool_batch(_rows()[:2]) is None)

CHECKS["observe_quotes_only_the_agent"] = (temporal.observe(_rows())["last_line"] == "Three attempts, then stop."
                                           and temporal.observe(_rows()[:1])["last_line"] == "")

# -- the record ------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "alpha"
    root.mkdir()
    written = temporal.note_turn_open(root, "alpha", NOW, CFG, _rows())
    back = temporal.read(root)
    no_tmp = not list(root.glob("*.tmp")) and not list(root.glob(".*.tmp"))
    # the newest exchange is the assistant row at base+3, so the gap is 90,000 − 3 seconds: an exact number,
    # not "some gap", because a roundtrip that returns a plausible interval is not proof it returned this one
    CHECKS["record_roundtrip"] = back.get("gap_s") == 89_997 and written["lane"] == "alpha" and no_tmp
    CHECKS["record_gap_is_whole_seconds"] = isinstance(back.get("gap_s"), int) and back["gap_band"] == "days"

    temporal.note_turn_close(root, back, NOW + 2, "completed")
    closed = temporal.read(root)
    CHECKS["record_close"] = closed.get("last_status") == "completed" and bool(closed.get("last_turn_end_ts"))

    (temporal.path(root)).write_text("{not json", encoding="utf-8")
    CHECKS["record_corrupt_is_absent"] = temporal.read(root) == {}

    other = Path(tmp) / "beta"
    other.mkdir()
    # written raw, not through temporal.write: write() stamps the lane from the directory, so a copied file is
    # the only way to hand a lane someone else's record
    temporal.path(other).write_text(json.dumps({"schema": 1, "lane": "alpha",
                                                "last_exchange_ts": "2026-11-06T04:01:45Z"}),
                                    encoding="utf-8")
    CHECKS["record_wrong_lane_refused"] = temporal.read(other) == {}

    off = Path(tmp) / "gamma"
    off.mkdir()
    inert = temporal.note_turn_open(off, "gamma", NOW, {"temporal": {"enabled": False}}, _rows())
    CHECKS["temporal_off_is_inert"] = inert == {} and not temporal.path(off).exists()

# -- the budget carve ------------------------------------------------------
_alloc = stack.allocate(32768, {"model": {"max_output_tokens": 4096},
                                "compression": {"target_max_pct": 30}})
CHECKS["allocate_carves_not_adds"] = (_alloc["temporal"] == 328
                                      and _alloc["system"] == 1310
                                      and _alloc["allocated"] == sum(_alloc[k] for k in
                                                                     ("temporal", "system", "assistant", "user",
                                                                      "summary", "history", "memory", "output"))
                                      and _alloc["free"] == 0)

# -- the block reaches the system side -------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "delta"
    root.mkdir()
    messages, report = stack.build("instructions here", root, _rows(), "hello", CFG,
                                   anchor=temporal.anchor(_record(1_449_205), NOW, CFG), channel="delta")
    CHECKS["anchor_is_a_labelled_block"] = ("temporal" in report["blocks"]
                                            and "# Now" in messages[0]["content"]
                                            and messages[0]["role"] == "system")
    _plain, report_plain = stack.build("instructions here", root, _rows(), "hello", CFG, channel="delta")
    CHECKS["anchor_absent_is_unchanged_prompt"] = "temporal" not in report_plain["blocks"]

# -- the digest is time-bounded -------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "epsilon"
    root.mkdir()
    s = session_mod.Session("epsilon", root)
    s._write_summary("two turns happened", "session_2026-11-06_04-01-42", 2,
                     span="2026-11-06T04:01:42Z → 2026-11-06T04:01:45Z")
    text = s.summary_path.read_text(encoding="utf-8")
    CHECKS["digest_carries_its_span"] = "→" in text and "2 turns" in text

# -- the loader refuses an anchor that does not fit ------------------------
def _refused(cfg):
    try:
        loader._validate(cfg)
    except Exception:
        return True
    return False


_base = loader.load()
_bad_share = json.loads(json.dumps(_base))
_bad_share["budget"]["temporal_pct"] = 9
_bad_band = json.loads(json.dumps(_base))
_bad_band["temporal"]["continuous_under_seconds"] = 7200
CHECKS["loader_refuses_temporal_pct"] = _refused(_bad_share) and not _refused(_base)
CHECKS["loader_refuses_band_order"] = _refused(_bad_band)

# -- a lane may override the three keys, or the section is documentation ----
from core import agentconfig
_CHECKS_OVERRIDE = agentconfig.as_cfg({"temporal": {"seam_after_minutes": 5}}, loader.load())
CHECKS["lane_override_applies"] = _CHECKS_OVERRIDE["temporal"]["seam_after_minutes"] == 5

# -- end to end: a real turn through the real manager, no network ----------
# The unit checks above prove the pieces. This proves the pieces are wired: the record opens, the anchor
# reaches the system block, the seam row is written exactly once, and a contiguous exchange stays quiet.
import copy as _copy
import time as _time

from core import channelstate
from providers.mock import MockProvider
from server.channels import ChannelManager
from tools import registry as treg


def _seed(profiles: Path, name: str, age_s: float, seam: bool = False) -> None:
    sessions = profiles / name / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    stamp = timefmt.stamp_from(_time.time() - age_s)
    rows = [{"role": "user", "content": "[Message] what did we decide about the retry limit?", "ts": stamp},
            {"role": "assistant", "content": "[Message] Three attempts, then stop.", "ts": stamp}]
    if seam:
        # a boundary row already left today, so this lane's next gap must not add a second one
        rows.append({"role": "user", "content": "[Time] 20 min passed between the previous exchange …",
                     "ts": timefmt.stamp_from(_time.time() - 300), "kind": "seam"})
    (sessions / "LATEST.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _rows_on(profiles: Path, name: str) -> list[dict]:
    path = profiles / name / "sessions" / "LATEST.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


with tempfile.TemporaryDirectory() as td:
    profiles = Path(td) / "profiles"
    profiles.mkdir()
    e2e_cfg = _copy.deepcopy(loader.load())
    e2e_cfg["agents"] = {"idle_sleep_minutes": 0, "max_queue": 4}
    e2e_cfg["metrics"] = {"enabled": False}
    mgr = ChannelManager(e2e_cfg, MockProvider(), treg.default_registry(e2e_cfg), profiles)
    for lane, age, seam in (("weekold", 7 * 86400 + 3600, False), ("fresh", 3, False), ("sameyday", 1200, True)):
        mgr.create(lane)
        (profiles / lane / "config.yaml").write_text("model:\n  provider: mock\n  name: mock-1\n", encoding="utf-8")
        mgr._providers.pop(lane, None)
        channel = mgr.channel(lane, ensure=True)
        channelstate.write(channel.root, channelstate.IDLE)
        channel.state = channelstate.IDLE
        # seeded AFTER create (which refuses a dir with no identity) and then reloaded: `Session` reads the
        # live window at construction, so a seed written behind its back must be re-read to be the history
        _seed(profiles, lane, age, seam=seam)
        channel.session.messages = channel.session._read()

    first = mgr.submit("weekold", "hello again", wait_s=30)
    seams = [r for r in _rows_on(profiles, "weekold") if r.get("kind") == "seam"]
    CHECKS["e2e_anchor_reached_the_prompt"] = (
        "A week or more has passed" in (first.get("system_prompt") or "")
        and "# Now" in (first.get("system_prompt") or ""))
    CHECKS["e2e_seam_written_once"] = len(seams) == 1 and "[Time]" in str(seams[0].get("content"))
    CHECKS["e2e_record_written"] = (temporal.read(profiles / "weekold").get("gap_band") == "weeks"
                                    and temporal.read(profiles / "weekold").get("last_status") == "completed")
    CHECKS["e2e_seam_is_not_a_turn"] = all(r.get("kind") == "seam" for r in seams) and seams[0].get("role") == "user"

    mgr.submit("weekold", "and now", wait_s=30)
    CHECKS["e2e_seam_not_duplicated"] = len([r for r in _rows_on(profiles, "weekold")
                                             if r.get("kind") == "seam"]) == 1

    quiet = mgr.submit("fresh", "hi", wait_s=30)
    CHECKS["e2e_contiguous_is_quiet"] = ("has passed" not in (quiet.get("system_prompt") or "")
                                         and not [r for r in _rows_on(profiles, "fresh") if r.get("kind") == "seam"]
                                         and temporal.read(profiles / "fresh").get("gap_band") == "continuous")

    # a lane that already left its [Time] row today keeps the sense but not a second row
    again = mgr.submit("sameyday", "back again", wait_s=30)
    CHECKS["e2e_one_seam_per_day"] = (
        len([r for r in _rows_on(profiles, "sameyday") if r.get("kind") == "seam"]) == 1
        and "Minutes have passed" in (again.get("system_prompt") or ""))

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
sys.exit(0 if all(CHECKS.values()) else 1)

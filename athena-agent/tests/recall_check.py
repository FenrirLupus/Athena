"""Recall: the vault holds the record, the index finds it, and a repeat costs nothing.

Every check here has an input that must fail it. The failure this suite exists for is not a crash — it is an
agent confidently answering a *different* question from an old row, or silently answering nothing because the
store it searched had holes.
"""
import copy
import json
import sys
import tempfile
import time
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from config import loader
from core import archive
from core import channelstate
from core import recall
from core import session as session_mod
from core import synonyms
from core import timefmt
from core.recall import decide, tokens, literals, quotable, consecutive_free
from data.index import Index
from data.vault import Vault
from providers.mock import MockProvider
from server.channels import ChannelManager
from tools import registry as treg

CHECKS = {}
NOW = 1_790_000_000.0
CFG = {"temporal": {"seam_after_minutes": 15, "continuous_under_seconds": 60},
       "recall": {"enabled": True, "min_score": 0.80, "min_semantic": 0.80, "min_jaccard": 0.5,
                  "min_tokens": 2, "min_history_rows": 2, "max_age_days": 30, "quote_chars": 600,
                  "max_consecutive": 3, "candidates": 40, "synonyms": "config/synonyms.txt"}}
SYN = synonyms.load(Path(__file__).resolve().parents[1] / "config" / "synonyms.txt")
PAIRS = json.loads((Path(__file__).resolve().parent / "fixtures" / "recall_pairs.json").read_text(encoding="utf-8"))


def _row(text, role="user", age_s=0, uid="u1", author="", kind=""):
    return {"role": role, "content": text, "ts": timefmt.stamp_from(NOW - age_s), "uid": uid,
            "author": author, "kind": kind}


# -- the text half ---------------------------------------------------------
CHECKS["tokens_drop_function_words"] = tokens("the file is here") == ["file"]
CHECKS["negations_are_kept"] = "not" in tokens("do not delete the file") and "delete" in tokens("delete the file")
CHECKS["markers_are_stripped"] = tokens("[from @scout] [Message] hello world") == ["hello", "world"]
CHECKS["literals_detected"] = (literals(tokens("run disk_usage on MEMORY.md")) == {"disk_usage", "memory.md"}
                               and literals(tokens("say ok")) == set())
CHECKS["sentence_dot_is_not_a_literal"] = tokens("how much space is free.") == ["much", "space", "free"]

# -- the decision ----------------------------------------------------------
_WAKE = [_row("Use the @ syntax to ask another agent to reply with exactly WAKE1.", uid="w1")]
CHECKS["literal_mismatch_refuses"] = decide(
    "Use the @ syntax to ask another agent to reply with exactly WAKE9.", _WAKE, CFG, NOW, "", 0, 100
)["reason"] == "literal_mismatch"

_OK = [_row("say ok", uid="q1")]
_ANSWER = {"content": "ok", "ts": timefmt.stamp_from(NOW - 120), "uid": "a1"}
_matched = decide("say ok", _OK, CFG, NOW, "", 0, 100, answer_for=lambda c: _ANSWER)
CHECKS["match_quotes"] = (_matched["reason"] == "matched" and _matched["mode"] == "quote"
                          and "Quoted from my own record" in _matched["reply"]
                          and "> ok" in _matched["reply"])

CHECKS["answer_unquotable_when_error"] = decide(
    "say ok", _OK, CFG, NOW, "", 0, 100,
    answer_for=lambda c: {"content": "model call failed: 402", "ts": _ANSWER["ts"], "uid": "x"}
)["reason"] == "answer_unquotable"
CHECKS["answer_unquotable_when_none"] = decide("say ok", _OK, CFG, NOW, "", 0, 100)["reason"] == "answer_unquotable"

CHECKS["too_thin_refuses"] = decide("ok", _OK, CFG, NOW, "", 0, 100, answer_for=lambda c: _ANSWER)["reason"] == "too_thin"
CHECKS["cold_lane_refuses"] = decide("say ok", _OK, CFG, NOW, "", 0, 1, answer_for=lambda c: _ANSWER)["reason"] == "cold"
CHECKS["cap_refuses"] = decide("say ok", _OK, CFG, NOW, "", 3, 100, answer_for=lambda c: _ANSWER)["reason"] == "capped"
CHECKS["disabled_refuses"] = decide("say ok", _OK, {"recall": {"enabled": False}}, NOW, "", 0, 100)["reason"] == "disabled"
CHECKS["author_isolated"] = decide("say ok", [_row("say ok", uid="q2", author="scout")], CFG, NOW, "", 0, 100,
                                   answer_for=lambda c: _ANSWER)["reason"] in ("author_mismatch", "below_bar")
CHECKS["too_old_refuses"] = decide("say ok", _OK, CFG, NOW, "", 0, 100,
                                   answer_for=lambda c: {"content": "ok", "ts": timefmt.stamp_from(NOW - 40 * 86400),
                                                         "uid": "a2"})["reason"] == "too_old"
CHECKS["below_bar_records_the_near_miss"] = decide("say ok please", _OK, CFG, NOW, "", 0, 100,
                                                   answer_for=lambda c: _ANSWER)["score"] > 0.5

_long = decide("say ok", _OK, CFG, NOW, "", 0, 100,
               answer_for=lambda c: {"content": "ok " + "word " * 400, "ts": _ANSWER["ts"], "uid": "a3"})
CHECKS["pointer_mode_when_long"] = (_long["mode"] == "pointer"
                                    and "the rest is in row" in _long["reply"])
_old_answer = decide("say ok", _OK, CFG, NOW, "", 0, 100,
                     answer_for=lambda c: {"content": "ok", "ts": timefmt.stamp_from(NOW - 2 * 86400), "uid": "a4"})
CHECKS["stale_answer_is_labelled"] = "may no longer be current" in _old_answer["reply"]
CHECKS["fresh_answer_is_not_labelled_stale"] = "may no longer be current" not in _matched["reply"]
CHECKS["consecutive_free_counts_the_tail"] = consecutive_free([
    {"role": "user", "content": "a"}, {"role": "assistant", "content": "b", "kind": "free"},
    {"role": "user", "content": "c", "kind": "seam"}, {"role": "assistant", "content": "d", "kind": "free"},
]) == 2

# -- the index -------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    index = Index(Path(td) / "recall.db", "t")
    rows = [_row("say ok", uid="1"), _row("ok", role="assistant", uid="2"),
            _row("reply with exactly: hosted ok", uid="3")]
    first, second = index.add(rows), index.add(rows)
    CHECKS["index_add_is_idempotent"] = (first, second, index.count()) == (3, 0, 3)
    hits = index.search("say ok", 5)
    CHECKS["index_search_ranks_the_question"] = bool(hits) and hits[0]["content"] == "say ok"
    CHECKS["index_ignores_seam_rows"] = index.add([_row("[Time] 20 min passed", uid="4", kind="seam")]) == 0
    CHECKS["index_survives_fts_syntax"] = index.search('NEAR( AND * "unbalanced', 5) == []
    answer = index.answer_for(hits[0])
    CHECKS["index_answer_follows_the_question"] = bool(answer) and answer["content"] == "ok"

# -- the archive -----------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "lane"
    (root / "sessions").mkdir(parents=True)
    seeded = [{"role": "user", "content": "hello", "ts": timefmt.stamp_from(NOW - 60)},
              {"role": "assistant", "content": "hi there", "ts": timefmt.stamp_from(NOW - 59)},
              {"role": "user", "content": "second question", "ts": timefmt.stamp_from(NOW - 30)}]
    (root / "sessions" / "LATEST.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in seeded), encoding="utf-8")
    before = archive.missing(root, "lane")
    first = archive.backfill(root, "lane")
    again = archive.backfill(root, "lane")
    CHECKS["backfill_closes_the_gap"] = before == 3 and first["missing_after"] == 0 and first["written"] == 3
    CHECKS["backfill_is_idempotent"] = again["written"] == 0 and again["missing_after"] == 0

    session = session_mod.Session("lane", root)
    session.messages = session._read()
    session.clear(archive=True)
    CHECKS["clear_vaults_before_it_moves"] = (session.vault.count() >= 3 and len(session.messages) == 0
                                              and bool(list((root / "sessions").glob("session_*.jsonl"))))

# -- end to end: a repeat costs nothing, a new question still pays --------
with tempfile.TemporaryDirectory() as td:
    profiles = Path(td) / "profiles"
    profiles.mkdir()
    cfg = copy.deepcopy(loader.load())
    cfg["agents"] = {"idle_sleep_minutes": 0, "max_queue": 4}
    cfg["metrics"] = {"enabled": True}
    cfg["recall"] = dict(CFG["recall"], min_history_rows=2, max_consecutive=9)
    cfg["temporal"]["seam_after_minutes"] = 15
    mgr = ChannelManager(cfg, MockProvider(), treg.default_registry(cfg), profiles)
    mgr.create("repeat")
    (profiles / "repeat" / "config.yaml").write_text("model:\n  provider: mock\n  name: mock-1\n", encoding="utf-8")
    mgr._providers.pop("repeat", None)
    channel = mgr.channel("repeat", ensure=True)
    channelstate.write(channel.root, channelstate.IDLE)
    channel.state = channelstate.IDLE
    seeded = [{"role": "user", "content": "[Message] say ok", "ts": timefmt.stamp_from(time.time() - 120)},
              {"role": "assistant", "content": "[Message] ok", "ts": timefmt.stamp_from(time.time() - 119)}]
    (profiles / "repeat" / "sessions" / "LATEST.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in seeded), encoding="utf-8")
    channel.session.messages = channel.session._read()
    Index.for_lane(channel.root, "repeat").add(channel.session.history())

    free = mgr.submit("repeat", "say ok", wait_s=30)
    CHECKS["free_turn_zero_calls"] = (bool(free.get("free")) and free.get("calls") == 0
                                      and not free.get("model_calls"))
    rows = channel.session.history()
    CHECKS["free_answer_is_marked_and_cited"] = any(r.get("kind") == "free" and r.get("recall") for r in rows)
    CHECKS["free_reply_cites_a_row"] = "Quoted from my own record" in str(free.get("reply") or "")

    paid = mgr.submit("repeat", "what colour is the sky", wait_s=30)
    CHECKS["non_repeat_still_pays"] = (not paid.get("free") and int(paid.get("calls") or 0) >= 1)

    metrics_path = profiles / "repeat" / "metrics" / "turns.jsonl"
    last_rows = [json.loads(l) for l in metrics_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    CHECKS["metrics_record_free_distinctly"] = any(r.get("free") for r in last_rows)

# -- round 2: the two-check ladder, measured against the labelled pairs -----
CHECKS["fixture_counts"] = (len(PAIRS["verbatim"]) == 5 and len(PAIRS["near"]) == 5
                            and len(PAIRS["paraphrase"]) == 8 and len(PAIRS["negative"]) >= 50)
CHECKS["synonyms_load_shape"] = (SYN.state == "ok" and len(SYN.groups) >= 8
                                 and len(SYN.words) >= 20
                                 and len(SYN.groups) <= synonyms.MAX_GROUPS
                                 and len(SYN.words) <= synonyms.MAX_TOKENS
                                 and all(len(g) <= synonyms.MAX_MEMBERS for g in SYN.groups))


def _pair_verdict(asked, stored):
    candidate = {"role": "user", "content": stored, "ts": timefmt.stamp_from(NOW - 300),
                 "uid": "cand", "author": "", "kind": ""}
    return decide(asked, [candidate], CFG, NOW, "", 0, 100, synonyms=SYN,
                  answer_for=lambda c: {"content": "ok, handled", "ts": timefmt.stamp_from(NOW - 299),
                                        "uid": "ans"})


_table = {}
for _name in ("verbatim", "near", "paraphrase", "negative"):
    _fires = sum(1 for p in PAIRS[_name] if _pair_verdict(p["a"], p["b"])["mode"] in ("quote", "pointer"))
    _table[_name] = (_fires, len(PAIRS[_name]))
print(f"experiment  verbatim {_table['verbatim'][0]}/{_table['verbatim'][1]}"
      f" · near {_table['near'][0]}/{_table['near'][1]}"
      f" · paraphrase {_table['paraphrase'][0]}/{_table['paraphrase'][1]}"
      f" · negative {_table['negative'][0]}/{_table['negative'][1]}")
CHECKS["experiment_table_verbatim"] = _table["verbatim"][0] == 5
CHECKS["experiment_table_near"] = _table["near"][0] == 5
CHECKS["experiment_table_paraphrase"] = _table["paraphrase"][0] >= 7
# One known false positive, named: a two-word question fully inside a three-word one
# ('reply with ok' / 'reply with web ok'). Any *new* one fails this check.
CHECKS["negatives_never_fire"] = _table["negative"][0] <= 1

CHECKS["polarity_guard_refuses"] = _pair_verdict("delete the file", "do not delete the file")["reason"] == "polarity_mismatch"
CHECKS["semantic_branch_fires_when_lexical_fails"] = (
    lambda v: v["reason"] == "matched" and v["branch"] == "semantic")(_pair_verdict("say ok", "reply with ok"))
CHECKS["verdict_records_both_scores"] = (
    lambda v: v["branch"] in ("lexical", "semantic") and v["semantic"] is not None)(_pair_verdict("say ok", "reply with ok"))
CHECKS["semantic_branch_needs_the_jaccard_guard"] = _pair_verdict(
    "space free disk left",
    "space free disk left and the host is a laptop with a second drive in the bay so it varies")["reason"] != "matched"
CHECKS["literal_guard_gates_the_semantic_branch"] = _pair_verdict(
    "say ok exactly", "say ok exactly wake1")["reason"] == "literal_mismatch"
# the two-group bridge: `available` is what lets free (not-occupied) reach left (remaining)
CHECKS["semantic_two_group_bridge"] = (lambda c: c is not None and abs(c.cover - 1.0) < 0.01)(
    SYN.compare(["space", "free"], ["disk", "left"]))
# the denominator is the asked side: an unmatched asked word depresses coverage, it never inflates it
CHECKS["semantic_uses_the_asked_side"] = (lambda c: c is not None and abs(c.cover - 0.67) < 0.01)(
    SYN.compare(["space", "free", "keyboard"], ["disk", "left"]))

with tempfile.TemporaryDirectory() as td:
    one = Path(td) / "one.txt"
    one.write_text("hello\n", encoding="utf-8")
    func = Path(td) / "func.txt"
    func.write_text("the and\n", encoding="utf-8")
    lit = Path(td) / "lit.txt"
    lit.write_text("wake1 wake2\n", encoding="utf-8")
    ant = Path(td) / "ant.txt"
    ant.write_text("install remove\n", encoding="utf-8")
    CHECKS["synonyms_rejects_group_of_one"] = synonyms.load(one).state == "invalid"
    CHECKS["synonyms_rejects_function_word"] = synonyms.load(func).state == "invalid"
    CHECKS["synonyms_rejects_literal"] = synonyms.load(lit).state == "invalid"
    CHECKS["synonyms_rejects_antonyms"] = synonyms.load(ant).state == "invalid"
    CHECKS["synonyms_missing_disables"] = (synonyms.load(Path(td) / "absent.txt").state == "missing"
                                           and synonyms.load(Path(td) / "absent.txt").compare(["a"], ["a"]) is None)

# a query must reach the twin it can only reach through the map
with tempfile.TemporaryDirectory() as td:
    index = Index(Path(td) / "recall.db", "t")
    index.add([{"role": "user", "content": "space free", "ts": timefmt.stamp_from(NOW - 60), "uid": "twin"}])
    plain = index.search("disk left", 10)
    expanded = index.search("disk left", 10, aliases=SYN.expand_words(["disk", "left"]))
    CHECKS["search_expands_the_query"] = not plain and bool(expanded) and expanded[0]["content"] == "space free"

# -- end to end: a paraphrase answered free, through the whole path ---------
with tempfile.TemporaryDirectory() as td:
    profiles = Path(td) / "profiles"
    profiles.mkdir()
    cfg = copy.deepcopy(loader.load())
    cfg["agents"] = {"idle_sleep_minutes": 0, "max_queue": 4}
    cfg["metrics"] = {"enabled": True}
    cfg["recall"] = dict(CFG["recall"], min_history_rows=2, max_consecutive=9)
    mgr = ChannelManager(cfg, MockProvider(), treg.default_registry(cfg), profiles)
    mgr.create("para")
    (profiles / "para" / "config.yaml").write_text("model:\n  provider: mock\n  name: mock-1\n", encoding="utf-8")
    mgr._providers.pop("para", None)
    channel = mgr.channel("para", ensure=True)
    channelstate.write(channel.root, channelstate.IDLE)
    channel.state = channelstate.IDLE
    seeded = [{"role": "user", "content": "[Message] say ok", "ts": timefmt.stamp_from(time.time() - 120)},
              {"role": "assistant", "content": "[Message] ok", "ts": timefmt.stamp_from(time.time() - 119)}]
    (profiles / "para" / "sessions" / "LATEST.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in seeded), encoding="utf-8")
    channel.session.messages = channel.session._read()
    Index.for_lane(channel.root, "para").add(channel.session.history())

    paraphrase = mgr.submit("para", "reply with ok", wait_s=30)
    CHECKS["semantic_free_turn_zero_calls"] = (
        bool(paraphrase.get("free")) and not paraphrase.get("model_calls")
        and (paraphrase.get("free") or {}).get("branch") == "semantic")

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
sys.exit(0 if all(CHECKS.values()) else 1)

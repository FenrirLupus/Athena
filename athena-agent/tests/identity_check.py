"""core/identity.py — the contract, checked against real files.

The design this implements was killed once for the thing these checks exist to prevent: an id that can be recomputed
from a name. So the tests are mostly about the cases where an id must *refuse* to exist — a missing file, a corrupt
file, a copied directory, a label that names nothing.

Run: ../.venv/bin/python3 tests/identity_check.py
"""
import json
import re
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import identity

checks: dict[str, bool] = {}


def note(name: str, passed) -> None:
    checks[name] = bool(passed)


def lane(profiles: Path, name: str, *, mint=True, minted_by="create"):
    root = profiles / name
    root.mkdir(parents=True, exist_ok=True)
    return (root, identity.mint(root, minted_by=minted_by)) if mint else (root, None)


with tempfile.TemporaryDirectory() as td:
    profiles = Path(td) / "profiles"
    profiles.mkdir(parents=True)

    # -- mint -------------------------------------------------------------
    scout, rec = lane(profiles, "scout")
    note("mint_writes_a_file", (scout / "identity.json").is_file())
    note("mint_id_is_a_uuid4", str(uuid.UUID(rec["id"])) == rec["id"] and uuid.UUID(rec["id"]).version == 4)
    note("mint_records_created_iso_z", bool(re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", rec["created"])))
    note("mint_records_minted_by", rec["minted_by"] == "create")
    note("mint_starts_with_no_aliases", rec["aliases"] == [])
    note("mint_leaves_no_tmp_file", not (scout / "identity.tmp").exists())
    note("mint_on_disk_matches_return", json.loads((scout / "identity.json").read_text())["id"] == rec["id"])

    try:
        identity.mint(scout)
        note("mint_refuses_to_overwrite", False)
    except identity.IdentityExists as exc:
        note("mint_refuses_to_overwrite", "refusing to overwrite" in str(exc))

    try:
        identity.mint(profiles / "bad", minted_by="made-up")
        note("mint_rejects_unknown_minted_by", False)
    except ValueError:
        note("mint_rejects_unknown_minted_by", True)

    # -- read / require / id_for -----------------------------------------
    note("read_absent_is_none", identity.read(profiles / "nothing-here") is None)

    corrupt, _ = lane(profiles, "corrupt", mint=False)
    (corrupt / "identity.json").write_text("{not json", encoding="utf-8")
    try:
        identity.read(corrupt)
        note("read_rejects_bad_json", False)
    except identity.IdentityInvalid as exc:
        note("read_rejects_bad_json", "not valid JSON" in str(exc))

    noid, _ = lane(profiles, "noid", mint=False)
    (noid / "identity.json").write_text(json.dumps({"created": "x"}), encoding="utf-8")
    try:
        identity.read(noid)
        note("read_rejects_missing_id", False)
    except identity.IdentityInvalid:
        note("read_rejects_missing_id", True)

    notuuid, _ = lane(profiles, "notuuid", mint=False)
    (notuuid / "identity.json").write_text(json.dumps({"id": "scout"}), encoding="utf-8")
    try:
        identity.read(notuuid)
        note("read_rejects_non_uuid_id", False)
    except identity.IdentityInvalid:
        note("read_rejects_non_uuid_id", True)

    try:
        identity.require(profiles / "ghost")
        note("require_names_the_fix", False)
    except identity.IdentityMissing as exc:
        text = str(exc)
        note("require_names_the_fix", "--adopt ghost" in text and "cannot be recovered" in text)

    note("id_for_returns_the_minted_id", identity.id_for(scout) == rec["id"])
    try:
        identity.id_for(profiles / "ghost")
        note("id_for_has_no_fallback", False)
    except identity.IdentityMissing:
        note("id_for_has_no_fallback", True)

    # -- resolve ---------------------------------------------------------
    athena, r2 = lane(profiles, "athena")
    record = json.loads((athena / "identity.json").read_text())
    record["aliases"] = ["athena-old", "old-name"]
    (athena / "identity.json").write_text(json.dumps(record, indent=1), encoding="utf-8")

    by_label = identity.resolve(profiles, "athena")
    note("resolve_by_label", by_label["label"] == "athena" and by_label["root"] == athena)
    note("resolve_by_id_prefix", identity.resolve(profiles, f"id:{r2['id']}")["label"] == "athena")
    note("resolve_by_alias", identity.resolve(profiles, "athena-old")["label"] == "athena")
    note("resolve_returns_aliases", set(by_label["aliases"]) == {"athena-old", "old-name"})

    try:
        identity.resolve(profiles, "nobody")
        note("resolve_unknown_raises", False)
    except identity.UnknownAgent as exc:
        note("resolve_unknown_raises", "known lanes" in str(exc))
        note("unknown_agent_is_a_lookup_error", isinstance(exc, LookupError))

    try:
        identity.resolve(profiles, f"id:{uuid.uuid4()}")
        note("resolve_unknown_id_raises", False)
    except identity.UnknownAgent:
        note("resolve_unknown_id_raises", True)

    # a copied directory shares an id: an error, and the label still resolves so it can be repaired
    copy, _ = lane(profiles, "athena-copy", mint=False)
    (copy / "identity.json").write_text((athena / "identity.json").read_text(), encoding="utf-8")
    try:
        identity.resolve(profiles, f"id:{r2['id']}")
        note("duplicate_id_is_an_error", False)
    except identity.IdentityDuplicate as exc:
        note("duplicate_id_is_an_error", "shared by" in str(exc))
    note("duplicate_still_resolves_by_label",
         identity.resolve(profiles, "athena-copy")["root"] == copy)

    # two lanes claiming one alias
    other, _ = lane(profiles, "other")
    payload = json.loads((other / "identity.json").read_text())
    payload["aliases"] = ["athena-old"]
    (other / "identity.json").write_text(json.dumps(payload), encoding="utf-8")
    try:
        identity.resolve(profiles, "athena-old")
        note("duplicate_alias_is_an_error", False)
    except identity.IdentityDuplicate:
        note("duplicate_alias_is_an_error", True)

    # -- root_for never creates ------------------------------------------
    try:
        identity.root_for(profiles, "made-up-lane")
    except identity.UnknownAgent:
        pass
    note("root_for_creates_nothing", not (profiles / "made-up-lane").exists())

    # -- labels / ids ----------------------------------------------------
    note("labels_include_the_alias", identity.labels(profiles, "athena") == frozenset(
        {"athena", "athena-old", "old-name"}))
    note("ids_of_a_label", identity.ids(profiles, "athena") == frozenset({r2["id"]}))
    note("ids_of_an_id", identity.ids(profiles, f"id:{r2['id']}") == frozenset({r2["id"]}))
    note("ids_of_an_unknown_string", identity.ids(profiles, "nobody") == frozenset({"nobody"}))
    note("ids_strip_the_prefix_when_unknown",
         identity.ids(profiles, f"id:{'0' * 8}-dead") == frozenset({"00000000-dead"}))

    # -- naming ----------------------------------------------------------
    note("label_ok_accepts_a_plain_name", identity.label_ok("scout-2"))
    note("label_ok_rejects_colon", not identity.label_ok("id:123"))
    note("label_ok_rejects_slash", not identity.label_ok("../etc"))
    note("label_ok_rejects_empty", not identity.label_ok(""))
    note("label_ok_rejects_over_64", not identity.label_ok("a" * 65))
    note("ref_ok_accepts_a_uuid_ref", identity.ref_ok(f"id:{r2['id']}"))
    note("ref_ok_rejects_a_bad_uuid_ref", not identity.ref_ok("id:not-a-uuid"))

    note("new_label_problem_allows_a_free_name", identity.new_label_problem(profiles, "fresh") == "")
    note("new_label_problem_rejects_a_live_label",
         "already names a lane" in identity.new_label_problem(profiles, "athena"))
    note("new_label_problem_rejects_an_alias",
         "alias" in identity.new_label_problem(profiles, "old-name"))
    note("new_label_problem_rejects_a_bad_label",
         "is not a label" in identity.new_label_problem(profiles, "has space"))
    note("new_label_problem_rejects_default",
         "queen lane" in identity.new_label_problem(profiles, "default"))
    (profiles / "stale").mkdir()
    note("new_label_problem_rejects_a_directory_without_an_id",
         "adopt" in identity.new_label_problem(profiles, "stale"))

    # -- load ------------------------------------------------------------
    folded = identity.load(profiles)
    note("load_is_nested", set(folded) == {"agents", "duplicates"})
    note("load_keys_by_id", r2["id"] in folded["agents"])
    note("load_excludes_unadopted_dirs", "stale" not in [a["label"] for a in folded["agents"].values()])
    note("load_reports_duplicates", any(i == r2["id"] for i, _ in folded["duplicates"]))
    note("load_does_not_raise_on_a_corrupt_neighbour", "corrupt" not in
         [a["label"] for a in folded["agents"].values()])

    # -- remint (--break-duplicate) --------------------------------------
    split = identity.remint(copy, split_from=r2["id"])
    note("remint_mints_a_new_id", split["id"] != r2["id"])
    note("remint_records_the_split", split.get("split_from") == r2["id"])
    note("remint_records_minted_by", split["minted_by"] == "break-duplicate")
    note("remint_keeps_aliases", split["aliases"] == [])
    note("remint_resolves_apart",
         identity.resolve(profiles, f"id:{split['id']}")["root"] == copy
         and identity.resolve(profiles, f"id:{r2['id']}")["root"] == athena)
    try:
        identity.remint(profiles / "ghost")
        note("remint_requires_an_existing_file", False)
    except identity.IdentityMissing:
        note("remint_requires_an_existing_file", True)


# -- integration: the stores that now carry the id ----------------------
# The elephant's review made the point that a suite which only ever sees bare labels tests the *fallback*, not the
# attribution. These checks build a real profiles tree with identities, so the id path is what is exercised.
import contextlib
import io
import shutil
import sqlite3

from core import permissions as perms_mod
from core import paths as paths_mod
from core.security import Ledger
from core.subagents import Trace

with tempfile.TemporaryDirectory() as td:
    athena = Path(td)
    profiles = athena / "profiles"

    def make_lane(name: str):
        root = profiles / name
        root.mkdir(parents=True, exist_ok=True)
        return root, identity.mint(root)

    scout_root, scout_rec = make_lane("scout")
    ghost_root = profiles / "ghost"              # deliberately un-adopted
    ghost_root.mkdir(parents=True)

    ledger = Ledger(athena / "security")
    boss = perms_mod.PermissionManager(athena, timeout_s=1.0, ledger=ledger, profiles=profiles)
    boss.register("path", blocking=True)

    def grant(agent, path, scope):
        ask = boss.ask("path", agent, summary=f"read {path}", detail=[], actions=("allow", "deny"),
                       targets=[path], reason="check", wait=False)
        return boss.resolve(ask["id"], "allow", scope)

    target = str(athena / "Documents" / "notes.md")
    grant("scout", target, "permanent")
    stored = boss.policy()["permanent"]
    note("grants_carry_the_lane_id", stored and stored[0]["agent_id"] == scout_rec["id"])
    note("covers_by_label_through_the_id", boss.covers("scout", target, "read") is True)
    note("covers_by_id", boss.covers(f"id:{scout_rec['id']}", target, "read") is True)

    # a re-created label is a stranger: the row was written for the old id
    shutil.rmtree(scout_root)
    make_lane("scout")
    note("grant_does_not_survive_delete_and_recreate", boss.covers("scout", target, "read") is False)

    # session grants are stored under the id, so a revoke by label has to clear that bucket
    session_target = str(athena / "Documents" / "session.md")
    grant("scout", session_target, "session")
    note("session_grant_applies", boss.covers("scout", session_target, "read") is True)
    boss.revoke("scout")
    note("revoke_by_label_clears_the_session_grant", boss.covers("scout", session_target, "read") is False)

    # an un-adopted lane: the grant is written (by label) and the operator is told why
    ghost_target = str(athena / "Documents" / "ghost.md")
    try:
        grant("ghost", ghost_target, "permanent")
        note("unattributed_grant_does_not_raise", True)
    except Exception:                              # noqa: BLE001
        note("unattributed_grant_does_not_raise", False)
    note("unattributed_grant_is_logged",
         "permission-grant-unattributed" in (athena / "security" / "events.jsonl").read_text(encoding="utf-8"))

    # identity.json is readable and never writable, with a reason that says why
    scope = paths_mod.Scope(profiles / "scout", athena, [])
    try:
        scope.check(str(profiles / "scout" / "identity.json"), paths_mod.WRITE, tool="probe")
        note("identity_json_write_refused", False)
    except ValueError as exc:
        note("identity_json_write_refused", "not an identity" in str(exc))
    note("identity_json_read_allowed",
         scope.check(str(profiles / "scout" / "identity.json"), paths_mod.READ, tool="probe").name == "identity.json")

    # an un-adopted directory raises the message that names the fix, not "no such lane"
    try:
        identity.resolve(profiles, "ghost")
        note("unadopted_lane_raises_missing", False)
    except identity.IdentityMissing as exc:
        note("unadopted_lane_raises_missing", "--adopt ghost" in str(exc))

    # --adopt replaces an unparsable file; every other mint refuses
    broken = profiles / "broken"
    broken.mkdir()
    (broken / "identity.json").write_text("{oops", encoding="utf-8")
    adopted = identity.mint(broken, minted_by="adopt")
    note("adopt_replaces_an_unparsable_file", uuid.UUID(adopted["id"]).version == 4)
    (broken / "identity.json").write_text("{oops", encoding="utf-8")
    try:
        identity.mint(broken, minted_by="create")
        note("create_does_not_replace_a_corrupt_id", False)
    except identity.IdentityInvalid:
        note("create_does_not_replace_a_corrupt_id", True)

    # residuals the elephant named: a pre-adoption row, and a bucket whose identity disappears
    before = len(boss.policy()["permanent"])
    boss.revoke("ghost")
    after = boss.policy()["permanent"]
    note("revoke_reaches_a_pre_adoption_row",
         len(after) < before + 1 and not any(g.get("agent") == "ghost" for g in after))

    session2 = str(athena / "Documents" / "gone.md")
    grant("scout", session2, "session")
    note("second_session_grant_applies", boss.covers("scout", session2, "read") is True)
    (profiles / "scout" / "identity.json").unlink()          # the lane's identity disappears
    boss.revoke("scout")
    note("revoke_reaches_a_bucket_whose_identity_is_gone", boss.covers("scout", session2, "read") is False)

    # -- the trace database grows the column on an old file, and a failure is loud
    run_dir = profiles / "scout" / "subagents" / "probe"
    run_dir.mkdir(parents=True)
    old = run_dir / "state.db"
    with sqlite3.connect(old) as db:
        db.execute("""CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at REAL, ended_at REAL,
                      task TEXT, model TEXT, lane TEXT, status TEXT, turns INTEGER, tool_calls INTEGER,
                      prompt_tokens INTEGER, completion_tokens INTEGER, cost_usd REAL, note TEXT)""")
        db.commit()
    trace = Trace(run_dir)
    trace.prepare()
    with sqlite3.connect(old) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
    note("subagent_db_gains_the_column", "agent_id" in columns)
    trace.record(agent_id=scout_rec["id"], started_at=1.0, ended_at=2.0, task="probe", lane="scout", status="done")
    with sqlite3.connect(old) as db:
        stored_id = db.execute("SELECT agent_id FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    note("subagent_row_carries_the_id", stored_id == scout_rec["id"])

    with sqlite3.connect(old) as db:
        db.execute("DROP TABLE runs")
        db.commit()
    noise = io.StringIO()
    with contextlib.redirect_stderr(noise):
        trace.record(agent_id="x", started_at=1.0, task="doomed")
    note("subagent_failure_is_loud", "could not record the run" in noise.getvalue())



# -- per-lane plugin enablement, and the config round trip it needs ------------
import yaml as _yaml

from core import agentconfig as agentconfig_mod
from core import plugins as plugins_mod

ceiling = {"plugins": {"enabled": ["clock", "egm"]}}
note("lane_inherits_the_ceiling", plugins_mod.lane_enabled(ceiling, None)[0] == ["clock", "egm"])
note("lane_narrows_the_ceiling",
     plugins_mod.lane_enabled(ceiling, {"plugins": {"enabled": ["clock"]}})[0] == ["clock"])
narrowed = plugins_mod.lane_enabled(ceiling, {"plugins": {"enabled": ["ghost"]}})
note("lane_cannot_widen_the_ceiling", narrowed[0] == [] and narrowed[1] == ["ghost"])
note("empty_lane_list_means_none",
     plugins_mod.lane_enabled(ceiling, {"plugins": {"enabled": []}})[0] == [])
malformed = plugins_mod.lane_enabled(ceiling, {"plugins": "clock"})
note("malformed_block_inherits_and_says_so", malformed[0] == ["clock", "egm"] and "mapping" in malformed[2])

with tempfile.TemporaryDirectory() as td3:
    lane_root = Path(td3) / "narrowed"
    lane_root.mkdir()
    agentconfig_mod.write(lane_root, {"plugins": {"enabled": ["clock"]}})
    first = _yaml.safe_load((lane_root / "config.yaml").read_text(encoding="utf-8"))
    note("write_emits_the_plugins_section", (first.get("plugins") or {}).get("enabled") == ["clock"])
    agentconfig_mod.write(lane_root, {})            # a persona re-save must not delete the narrowing
    again = _yaml.safe_load((lane_root / "config.yaml").read_text(encoding="utf-8"))
    note("narrowing_survives_a_resave", (again.get("plugins") or {}).get("enabled") == ["clock"])
    note("inheriting_lane_stays_blank", (again.get("plugins") or {}).get("enabled") is not None)


# -- aliases have a writer now, and an orphan can be deleted ---------------
with tempfile.TemporaryDirectory() as td4:
    agents = Path(td4) / "profiles"
    agents.mkdir()
    for name in ("atlas", "scout", "default"):
        (agents / name).mkdir()
        identity.mint(agents / name)

    record = identity.add_alias(agents, "scout", "atlas-old")
    note("alias_is_written", record["aliases"] == ["atlas-old"])
    note("alias_resolves_to_the_same_lane", identity.resolve(agents, "atlas-old")["label"] == "scout")
    note("alias_appears_in_labels", "atlas-old" in identity.labels(agents, "scout"))
    note("alias_is_idempotent", identity.add_alias(agents, "scout", "atlas-old")["aliases"] == ["atlas-old"])
    try:
        identity.add_alias(agents, "scout", "atlas")
        note("alias_cannot_take_another_lane_label", False)
    except identity.Conflict:
        note("alias_cannot_take_another_lane_label", True)
    try:
        identity.add_alias(agents, "scout", "id:abc")
        note("alias_cannot_look_like_a_ref", False)
    except identity.Conflict:
        note("alias_cannot_look_like_a_ref", True)
    note("alias_can_be_dropped", identity.drop_alias(agents, "scout", "atlas-old")["aliases"] == [])
    try:
        identity.resolve(agents, "atlas-old")
        note("dropped_alias_no_longer_resolves", False)
    except identity.UnknownAgent:
        note("dropped_alias_no_longer_resolves", True)

    from server.channels import ChannelManager
    from tools import registry as _treg

    class _Provider:
        def describe(self):
            return {}

    orphan = agents / "stale"
    orphan.mkdir()
    (orphan / "config.yaml").write_text("persona:\n  name: leftover\n", encoding="utf-8")
    boss = ChannelManager({"tools": {"enabled": False}}, _Provider(),
                          _treg.default_registry({"tools": {"enabled": False}}), agents)
    note("orphan_directory_is_deletable", boss.remove("stale")["removed"] is True)
    note("orphan_directory_is_gone", not orphan.exists())
    survivor = identity.require(agents / "atlas")
    note("remove_accepts_an_id_ref", boss.remove(f"id:{survivor['id']}")["removed"] is True)

    # the two ambiguity holes the elephant found inside the alias writer
    (agents / "copy").mkdir()
    (agents / "copy" / "identity.json").write_text((agents / "scout" / "identity.json").read_text())
    try:
        identity.add_alias(agents, "scout", "copy")
        note("alias_cannot_take_a_copied_lanes_label", False)
    except identity.Conflict:
        note("alias_cannot_take_a_copied_lanes_label", True)
    (agents / "unadopted").mkdir()
    try:
        identity.add_alias(agents, "scout", "unadopted")
        note("alias_cannot_shadow_an_unadopted_directory", False)
    except identity.Conflict:
        note("alias_cannot_shadow_an_unadopted_directory", True)

    # and a symlink is not a lane to delete through
    import os as _os

    link = agents / "link"
    _os.symlink(agents / "scout", link)
    try:
        boss.remove("link")
        note("remove_refuses_a_symlink", False)
    except identity.Conflict:
        note("remove_refuses_a_symlink", (agents / "scout").exists())

    try:
        boss.remove("nosuchlane")
        note("remove_unknown_ref_refuses", False)
    except identity.UnknownAgent:
        note("remove_unknown_ref_refuses", True)

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
ok = sum(checks.values())
print(f"\n{ok}/{len(checks)} — RESULT:", "PASS" if ok == len(checks) else "FAIL")

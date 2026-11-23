"""Curator safety: three failure modes the flow suite never reached.

`curator_flow_check.py` proves the happy path, the gate failure, containment by `..`, tampering and staleness.
It does not prove what happens when the *filesystem* fights back: a target that is a symlink, a target directory
that cannot be written, or the gate as it actually runs rather than as a stub.

Every case here builds a throwaway tree in a temporary directory and drives the real scripts against it. Nothing
touches the live tree. That is not politeness: these cases deliberately make a directory 0555 and install gates that
fail, and running that against the real tree would be the exact class of accident this suite is supposed to prevent.
Case (c) in particular must run on a copy — the gate it invokes is the real gate, and the real gate runs this very
suite, so in the live tree that would recurse and take minutes; in a copy it is fast and self-contained.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

TREE_SRC = Path(__file__).resolve().parents[1]
SCRIPTS = TREE_SRC / "skills" / "system" / "curator" / "scripts"
REAL_GATE = TREE_SRC / "tests" / "run_gate.sh"
REAL_VENV = TREE_SRC.parent / ".venv"
CHECKS = {}


class _Missing:
    """A proposal that was never written. It answers like a path that is not there, so the checks that follow
    fail by name — `folder.name` and `folder / "APPLIED.md"` still work — instead of raising on a None."""
    name = "__missing__"

    def __truediv__(self, other):
        return Path(self.name) / other

OBSERVED = {}

sys.path.insert(0, str(SCRIPTS))
import curator_diff  # noqa: E402  — the same diff builder apply uses to verify a proposal

PROPOSE = "skills/system/curator/scripts/propose.py"
APPLY = "skills/system/curator/scripts/apply.py"
STUB_PASS = '#!/usr/bin/env bash\necho "GATE: PASS"\nexit 0\n'
STUB_FAIL = '#!/usr/bin/env bash\necho "GATE: FAIL"\nexit 1\n'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args, cwd):
    return subprocess.run([sys.executable] + args, cwd=str(cwd), capture_output=True, text=True)


def make_tree(root: Path, gate: str = STUB_PASS) -> Path:
    (root / "tests").mkdir(parents=True)
    (root.parent / "docs").mkdir(parents=True, exist_ok=True)
    (root / "skills" / "system" / "curator").mkdir(parents=True)
    shutil.copytree(SCRIPTS, root / "skills" / "system" / "curator" / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    # the curator scripts instrument through core.trace, so a tree faithful enough to run them carries it
    (root / "core").mkdir()
    shutil.copy2(TREE_SRC / "core" / "trace.py", root / "core" / "trace.py")
    gate_file = root / "tests" / "run_gate.sh"
    gate_file.write_text(gate)
    os.chmod(gate_file, 0o755)
    (root.parent / "docs" / "notes.md").write_text("line one\nline two\nline three\n")
    os.chmod(root.parent / "docs" / "notes.md", 0o755)
    return root


def fresh_backup(base: Path):
    """apply refuses without a backup newer than the proposal; a stub one is enough here."""
    (base / "backups").mkdir(exist_ok=True)
    backup = base / "backups" / "athena-agent-backup-999.zip"
    backup.write_bytes(b"stub")
    future = time.time() + 5
    os.utime(backup, (future, future))
    return backup


def propose(root: Path, title: str, rel: str, new_text: str):
    new = root / "new.txt"
    new.write_text(new_text)
    out = run([PROPOSE, "--title", title, "--file", rel, "--new", str(new),
               "--rationale", "suite", "--evidence", "suite"], root)
    if out.returncode != 0:
        # a proposal that was not written is a _Missing, never a None: the caller's very next line is
        # folder.name, and a None there is a traceback that names no check and hides every result after it
        return _Missing(), out
    return Path(out.stdout.strip().splitlines()[-1]), out


def wrote(folder, out, label: str):
    """Record that this proposal exists, and say why when it does not. One key per site: a shared key let the
    last site overwrite the earlier ones, and one site checked the wrong variable entirely."""
    CHECKS[f"proposal_was_written:{label}"] = not isinstance(folder, _Missing)
    if isinstance(folder, _Missing):
        print(f"  note: propose refused ({label}):\n{(out.stdout + out.stderr).strip()[-600:]}")
    return folder


def craft(root: Path, name: str, rel: str, old_text: str, new_text: str, sha_before: str,
          base_label: str = "tree") -> Path:
    """Write a proposal by hand, the way a stale or hostile one would arrive, with every artefact consistent.

    Consistency matters: if the refusal could have come from a missing artefact or a hash mismatch, the case proves
    nothing about the failure mode it names.
    """
    folder = root.parent / "documentation" / "optimization" / "proposals" / name
    folder.mkdir(parents=True)
    (folder / "diff.patch").write_text(curator_diff.unified(old_text, new_text, rel), encoding="utf-8")
    (folder / "content.old").write_text(old_text, encoding="utf-8")
    (folder / "content.new").write_text(new_text, encoding="utf-8")
    (folder / "verify.json").write_text(json.dumps({
        "file": rel, "base": base_label,
        "sha256_before": sha_before,
        "sha256_after": hashlib.sha256(new_text.encode()).hexdigest(),
        "gate": "bash tests/run_gate.sh",
        "created_at": time.strftime("%Y%m%dT%H%M%S", time.gmtime()), "format": 2,
    }, indent=2), encoding="utf-8")
    return folder


def apply(root: Path, name: str, *extra):
    return run([APPLY, name, "--yes", *extra], root)


def leftovers(directory: Path):
    """Any file the failed write left behind: the mkstemp names start with .<target>. and end .curator."""
    return [p.name for p in directory.iterdir() if p.name.endswith(".curator")]


# ---------------------------------------------------------------------------------------------------------------
# (a) the target is a symlink pointing outside the tree
#
# `resolve()` is what both scripts use for containment, so the symlink is followed before the check. That is the
# right order — the check must judge the file that would actually be written — but it means the *link* is never
# examined: an implementation could pass the containment test and still write through the link. So the case checks
# both that the write is refused and that the link target's bytes and the link itself are untouched.
# ---------------------------------------------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside_td:
    base = Path(td)
    root = make_tree(base / "tree")
    fresh_backup(base)
    outside = Path(outside_td) / "secret.txt"          # a second temp dir: genuinely outside the tree and home
    outside.write_text("original secret\n")
    before = sha(outside)
    link = root.parent / "docs" / "link.md"
    link.symlink_to(outside)

    _, refused_propose = propose(root, "through the link", "docs/link.md", "pwned\n")
    probe = refused_propose.stdout + refused_propose.stderr
    CHECKS["symlink_propose_refused"] = refused_propose.returncode != 0 and "outside the athena home" in probe
    CHECKS["symlink_propose_wrote_no_proposal"] = not (base / "documentation").exists()
    CHECKS["symlink_propose_left_target_alone"] = sha(outside) == before and outside.read_text() == "original secret\n"

    # a proposal that already exists (written before the link appeared, or by hand) must be refused at apply
    folder = craft(root, "symlink-escape", "docs/link.md", "original secret\n", "pwned\n", before)
    escaped = apply(root, folder.name)
    text = escaped.stdout + escaped.stderr
    # a link that leaves the home is refused either as "outside the athena home" or, when it resolves back
    # into the program tree, as "inside the program tree" — both are the containment rule doing its job
    CHECKS["symlink_apply_refused"] = (escaped.returncode != 0
                                       and ("outside the athena home" in text or ("program tree" in text)))
    CHECKS["symlink_target_untouched"] = sha(outside) == before and outside.read_text() == "original secret\n"
    CHECKS["symlink_still_a_link"] = link.is_symlink() and link.resolve() == outside.resolve()
    CHECKS["symlink_apply_not_recorded"] = not (folder / "APPLIED.md").exists()

# ---------------------------------------------------------------------------------------------------------------
# (b) the target's directory cannot be written
#
# The change is written to a temp file in the target's own directory (so os.replace is atomic), which means a
# directory that refuses creation must fail *before* the target is touched. Three claims: it refuses loudly, the
# original bytes survive, and nothing is left behind — a stray .curator file beside the target would be the partial
# write this test exists to rule out.
# ---------------------------------------------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root = make_tree(base / "tree")
    fresh_backup(base)
    target = root.parent / "docs" / "notes.md"
    folder, out = propose(root, "unwritable", "docs/notes.md", "line one\nTWO\nline three\n")
    wrote(folder, out, "unwritable")
    before = sha(target)
    docs = target.parent

    os.chmod(docs, 0o555)
    try:
        blocked = apply(root, folder.name)
    finally:
        os.chmod(docs, 0o755)
    msg = blocked.stdout + blocked.stderr
    CHECKS["unwritable_refused_loudly"] = blocked.returncode != 0 and "could not write the change" in msg
    CHECKS["unwritable_bytes_intact"] = sha(target) == before
    CHECKS["unwritable_mode_intact"] = oct(os.stat(target).st_mode)[-3:] == "755"
    CHECKS["unwritable_no_temp_file"] = leftovers(docs) == []
    CHECKS["unwritable_journal_cleared"] = not (folder / "INFLIGHT.json").exists()
    CHECKS["unwritable_no_traceback"] = "Traceback" not in msg

# the same directory, but the target itself is also read-only. The refusal still has to be a refusal: the restore
# inside apply's except block writes the target, so a read-only target is the case where the error path can fail.
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root = make_tree(base / "tree")
    fresh_backup(base)
    target = root.parent / "docs" / "notes.md"
    folder, out = propose(root, "unwritable readonly", "docs/notes.md", "line one\nTWO\nline three\n")
    wrote(folder, out, "unwritable_readonly")
    before = sha(target)
    docs = target.parent

    os.chmod(target, 0o444)
    os.chmod(docs, 0o555)
    try:
        blocked2 = apply(root, folder.name)
    finally:
        os.chmod(docs, 0o755)
        os.chmod(target, 0o755)
    msg2 = blocked2.stdout + blocked2.stderr
    CHECKS["unwritable_readonly_bytes_intact"] = sha(target) == before
    CHECKS["unwritable_readonly_no_temp_file"] = leftovers(docs) == []
    # the handler's own restore can fail; if it does, apply owes the caller a refusal, not a traceback and a
    # journal that blocks every later apply ("a previous apply never finished")
    OBSERVED["unwritable_readonly"] = {"msg": msg2[-300:], "exit": blocked2.returncode}
    CHECKS["unwritable_readonly_clean_refusal"] = "could not write the change" in msg2 and "Traceback" not in msg2
    CHECKS["unwritable_readonly_journal_cleared"] = not (folder / "INFLIGHT.json").exists()

# ---------------------------------------------------------------------------------------------------------------
# (c) the real gate, not a stub
#
# Every other case — here and in the flow suite — installs a stand-in gate. That proves apply *calls* a gate and
# reacts to its exit code; it does not prove apply works with the gate that actually ships. So this case copies the
# real tests/run_gate.sh into a throwaway tree and runs apply against a throwaway tree, with the copy's suite list
# trimmed from ~50 suites to one tiny suite. The gate's own verdict logic — the exit code, the RESULT line grep,
# the "GATE: PASS"/"GATE: FAIL" strings apply parses — is the real code; only the work it runs is shrunk.
#
# A copy is used for two reasons: the real suite takes minutes and runs this very file, so pointing it at the live
# tree would recurse; and a gate that rewrites files must never be aimed at the files that matter.
# ---------------------------------------------------------------------------------------------------------------

TINY_SUITE = """import hashlib, pathlib
here = pathlib.Path(__file__).resolve()
{prelude}(here.parents[2] / "gate_saw.txt").write_text(hashlib.sha256((here.parents[2] / "docs" / "notes.md").read_bytes()).hexdigest())
print("RESULT: {verdict}")
{exit_line}
"""


def write_tiny_suite(root: Path, passing: bool, prelude: str = ""):
    """A deliberately tiny suite: its only job is to record what the file looked like when the gate ran."""
    (root / "tests" / "curator_gate_probe.py").write_text(TINY_SUITE.format(
        prelude=prelude,
        verdict="1/1 — PASS" if passing else "0/1 — FAIL",
        exit_line="raise SystemExit(0)" if passing else "raise SystemExit(1)"))


with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    real = REAL_GATE.read_text(encoding="utf-8")
    trimmed, n = re.subn(r"for t in[\s\S]*?; do", "for t in curator_gate_probe; do", real, count=1)
    root = make_tree(base / "tree", gate=trimmed)
    fresh_backup(base)
    CHECKS["real_gate_is_real"] = (n == 1 and trimmed != real and "GATE: PASS" in trimmed
                                   and "GATE: FAIL" in trimmed and "curator_gate_probe" in trimmed)
    # the real gate runs "../.venv/bin/python3"; keep that path contract rather than editing the gate further
    venv = base / ".venv"
    if REAL_VENV.exists():
        venv.symlink_to(REAL_VENV, target_is_directory=True)
    else:
        (venv / "bin").mkdir(parents=True)
        shim = venv / "bin" / "python3"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        os.chmod(shim, 0o755)
    target = root.parent / "docs" / "notes.md"
    original = sha(target)
    new_text = "line one\nline REAL\nline three\n"
    new_sha = hashlib.sha256(new_text.encode()).hexdigest()

    # --- the real gate fails: apply must revert the write it already made ---
    folder, out = propose(root, "real gate fails", "docs/notes.md", new_text)
    wrote(folder, out, "real_gate_fail")
    write_tiny_suite(root, passing=False)
    failed = apply(root, folder.name)
    saw = base / "gate_saw.txt"
    OBSERVED["real_gate_fail"] = {"exit": failed.returncode,
                                  "err": (failed.stderr or "")[-300:],
                                  "journal": (folder / "INFLIGHT.json").exists(),
                                  "applied": (folder / "APPLIED.md").is_file()}
    CHECKS["real_gate_fail_exit"] = failed.returncode == 1
    CHECKS["real_gate_fail_reverts_bytes"] = sha(target) == original and target.read_text() == "line one\nline two\nline three\n"
    CHECKS["real_gate_fail_reverts_mode"] = oct(os.stat(target).st_mode)[-3:] == "755"
    CHECKS["real_gate_fail_was_live"] = saw.is_file() and saw.read_text().strip() == new_sha
    CHECKS["real_gate_fail_recorded"] = (folder / "APPLIED.md").is_file() and "Apply failed" in (folder / "APPLIED.md").read_text()
    CHECKS["real_gate_fail_journal_cleared"] = not (folder / "INFLIGHT.json").exists()

    # --- the real gate passes: the change stays and is recorded as applied ---
    saw.write_text("stale")                                    # so a missed gate run cannot look like a run
    folder2, out2 = propose(root, "real gate passes", "docs/notes.md", new_text)
    wrote(folder2, out2, "real_gate_pass")
    write_tiny_suite(root, passing=True)
    passed = apply(root, folder2.name)
    CHECKS["real_gate_pass_exit"] = passed.returncode == 0 and "applied and gated" in (passed.stdout + passed.stderr)
    CHECKS["real_gate_pass_keeps_change"] = sha(target) == new_sha
    CHECKS["real_gate_pass_records_it"] = (folder2 / "APPLIED.md").is_file() and "Applied —" in (folder2 / "APPLIED.md").read_text()
    CHECKS["real_gate_pass_journal_cleared"] = not (folder2 / "INFLIGHT.json").exists()
    CHECKS["real_gate_pass_ran"] = saw.read_text().strip() == new_sha

    # --- the real gate fails while the target is read-only: the revert is the part that must not fail ---
    # a gate runs every suite, and a suite that audits or changes file modes is normal, so "the gate left the file
    # read-only and then failed" is reachable. The revert writes the target, so this is where restore can throw.
    target3_sha = sha(target)
    new_text3 = "line one\nline GATE\nline three\n"
    folder3, out3 = propose(root, "real gate fails after chmod", "docs/notes.md", new_text3)
    wrote(folder3, out3, "real_gate_fail_after_chmod")
    write_tiny_suite(root, passing=False,
                     prelude='(here.parents[2] / "docs" / "notes.md").chmod(0o444)\n')
    locked = apply(root, folder3.name)
    locked_msg = locked.stdout + locked.stderr
    try:
        CHECKS["gate_fail_reverts_when_target_readonly"] = sha(target) == target3_sha
        CHECKS["gate_fail_readonly_mode_restored"] = oct(os.stat(target).st_mode)[-3:] == "755"
        CHECKS["gate_fail_readonly_recorded"] = (folder3 / "APPLIED.md").is_file()
        CHECKS["gate_fail_readonly_journal_cleared"] = not (folder3 / "INFLIGHT.json").exists()
        CHECKS["gate_fail_readonly_no_traceback"] = "Traceback" not in locked_msg
    finally:
        os.chmod(target, 0o755)

# --- the guards around the record itself ---------------------------------
# a folder that goes read-only at the wrong moment must not turn an outcome into a traceback, and must not
# silently drop the record: both helpers are called directly because timing that state from outside is a race.
with tempfile.TemporaryDirectory() as td:
    import importlib.util
    spec = importlib.util.spec_from_file_location("apply_under_test",
                                                  str(Path(APPLY).resolve() if 'APPLY' in dir() else
                                                      Path(__file__).resolve().parents[1] / "skills/system/curator/scripts/apply.py"))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    folder = Path(td) / "p"
    folder.mkdir()
    journal = folder / "INFLIGHT.json"
    journal.write_text("{}", encoding="utf-8")
    os.chmod(folder, 0o555)
    try:
        try:
            cleared = mod.clear_journal(journal)
        except Exception:                      # a guard that raises is a failure, and it must fail by name
            cleared = "raised"
        try:
            wrote = mod.record(folder, "APPLIED.md", "x")
        except Exception:
            wrote = "raised"
    finally:
        os.chmod(folder, 0o755)
    CHECKS["journal_clear_failure_is_reported"] = cleared is False
    CHECKS["record_failure_does_not_raise"] = wrote is False

    # restore() must answer for the file, not for itself: a True that no write backs is exactly the mutant this
    # suite has to name, and it is cheap to falsify directly — a symlink is a target restore must refuse, so a
    # True here means the return value is a constant rather than a verdict
    keep = folder / "keep.md"
    keep.write_text("keep\n", encoding="utf-8")
    link = folder / "link.md"
    link.symlink_to(keep)
    try:
        refused = mod.restore(str(link), b"orig\n", 0o644)
    except Exception:
        refused = "raised"
    CHECKS["restore_refuses_a_link_and_claims_nothing"] = (refused is False
                                                           and keep.read_bytes() == b"keep\n"
                                                           and link.is_symlink())

    # ...and it must not change a mode by path. A chmod(name) is what follows a symlink swapped in between the
    # check and the open: it widens the referent's mode (0600 -> 0755) and leaves the link in place, so the file
    # that was inspected and the file that was written are different objects. Widen by fchmod, or not at all.
    ro = folder / "readonly.md"
    ro.write_text("keep\n", encoding="utf-8")
    os.chmod(ro, 0o444)
    calls = []
    real_chmod = os.chmod

    def spy(path, m, *a, **k):
        calls.append(str(path))
        return real_chmod(path, m, *a, **k)

    mod.os.chmod = spy
    try:
        restored = mod.restore(str(ro), b"orig\n", 0o444)
    finally:
        mod.os.chmod = real_chmod
    CHECKS["restore_widens_by_fd_not_by_path"] = (restored is True and calls == []
                                                  and ro.read_bytes() == b"orig\n"
                                                  and oct(os.stat(ro).st_mode)[-3:] == "444")


for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
if not all(CHECKS.values()):
    print("\nOBSERVED:", json.dumps(OBSERVED, indent=2)[:1200])
if not all(CHECKS.values()):
    print("\nDEFECTS — apply.py's error paths are not themselves guarded:")
    print("  - unwritable dir + read-only target: the restore inside the except block throws, so the caller gets a")
    print("    PermissionError traceback instead of 'refused: could not write the change', and INFLIGHT.json is")
    print("    left behind (apply.py:221) — every later apply is then refused as 'never finished'.")
    print("  - gate fails after the target became read-only: the revert throws, so the file keeps the unverified")
    print("    new bytes, no APPLIED.md is written and the journal stays (apply.py:230-234). This is the one that")
    print("    matters: the gate said no and the change is live anyway.")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
raise SystemExit(0 if all(CHECKS.values()) else 1)

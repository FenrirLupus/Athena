"""Curator flow: propose, dry-run, apply, and the revert that must never fail silently.

Everything runs in a throwaway tree with a stub gate, because the flow is destructive by design: a suite that
touched the real tree to prove a revert works would be the worst kind of test. The claims here are the ones the audit
found either broken or unproven — containment, content verification, mode preservation, byte-exact revert.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

TREE_SRC = Path(__file__).resolve().parents[1]
SCRIPTS = TREE_SRC / "skills" / "system" / "curator" / "scripts"
CHECKS = {}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args, cwd):
    return subprocess.run([sys.executable] + args, cwd=str(cwd), capture_output=True, text=True)


def make_tree(root: Path, gate: str) -> Path:
    (root / "tests").mkdir(parents=True)
    (root.parent / "docs").mkdir(parents=True, exist_ok=True)
    (root / "skills" / "system" / "curator").mkdir(parents=True)
    shutil.copytree(SCRIPTS, root / "skills" / "system" / "curator" / "scripts")
    # the curator scripts instrument through core.trace, so a tree faithful enough to run them carries it
    (root / "core").mkdir()
    shutil.copy2(TREE_SRC / "core" / "trace.py", root / "core" / "trace.py")
    gate_file = root / "tests" / "run_gate.sh"
    # the gate also checks a precondition: the in-flight journal must exist while a change is unverified.
    # Without that, a mutated apply that never writes the journal would look identical to a correct one.
    gate_file.write_text(
        '#!/usr/bin/env bash\n'
        f'if [ "{gate}" = "PASS" ]; then\n'
        '  find "$PWD/.." -name INFLIGHT.json 2>/dev/null | head -1 | grep -q INFLIGHT || { echo "no journal"; exit 3; }\n'
        'fi\n'
        f'echo "GATE: {gate}"\nexit {0 if gate == "PASS" else 1}\n')
    os.chmod(gate_file, 0o755)
    (root.parent / "docs" / "notes.md").write_text("line one\nline two\nline three\n")
    os.chmod(root.parent / "docs" / "notes.md", 0o755)
    return root


def propose(root: Path, title: str, new_text: str):
    new = root / "new.txt"
    new.write_text(new_text)
    out = run(["skills/system/curator/scripts/propose.py", "--title", title, "--file", "docs/notes.md",
               "--new", str(new), "--rationale", "test", "--evidence", "suite"], root)
    folder = Path(out.stdout.strip().splitlines()[-1])
    # the freshness check compares against a backup newer than the proposal
    (root.parent / "backups").mkdir(exist_ok=True)
    backup = root.parent / "backups" / "athena-agent-backup-999.zip"
    backup.write_bytes(b"stub")
    future = time.time() + 5
    os.utime(backup, (future, future))
    return folder, out


with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root = make_tree(base / "tree", "PASS")
    target = root.parent / "docs" / "notes.md"
    before = sha(target)

    # --- propose writes an artefact and changes nothing ---------------------
    folder, out = propose(root, "tighten wording", "line one\nline TWO\nline three\n")
    CHECKS["proposal_written"] = (folder / "PROPOSAL.md").is_file() and (folder / "verify.json").is_file()
    CHECKS["proposal_carries_content"] = (folder / "content.new").is_file() and (folder / "content.old").is_file()
    CHECKS["propose_changes_nothing"] = sha(target) == before

    # --- dry run changes nothing -------------------------------------------
    dry = run(["skills/system/curator/scripts/apply.py", folder.name, "--dry-run"], root)
    CHECKS["dry_run_refuses_nothing"] = dry.returncode == 0 and sha(target) == before

    # --- apply with a passing gate -----------------------------------------
    applied = run(["skills/system/curator/scripts/apply.py", folder.name, "--yes"], root)
    CHECKS["apply_writes"] = applied.returncode == 0 and "line TWO" in target.read_text()
    CHECKS["apply_records_it"] = (folder / "APPLIED.md").is_file()
    CHECKS["mode_preserved"] = oct(os.stat(target).st_mode)[-3:] == "755"

    # --- a failing gate reverts byte-for-byte -------------------------------
    patched = sha(target)
    folder2, _ = propose(root, "break it", "line one\nWRONG\nline three\n")
    (root / "tests" / "run_gate.sh").write_text('#!/usr/bin/env bash\necho "GATE: FAIL"\nexit 1\n')
    failed = run(["skills/system/curator/scripts/apply.py", folder2.name, "--yes"], root)
    CHECKS["failed_gate_reverts_bytes"] = failed.returncode == 1 and sha(target) == patched
    CHECKS["failed_gate_reverts_mode"] = oct(os.stat(target).st_mode)[-3:] == "755"
    record = folder2 / "APPLIED.md"
    CHECKS["failed_gate_is_recorded"] = record.is_file() and "Apply failed" in record.read_text()
    CHECKS["journal_cleared"] = not (folder2 / "INFLIGHT.json").exists()

    # --- containment: a proposal may not name a path outside the tree ------
    (root / "tests" / "run_gate.sh").write_text('#!/usr/bin/env bash\necho "GATE: PASS"\nexit 0\n')
    outside = base / "OUTSIDE.txt"
    outside.write_text("original\n")
    evil = base / "documentation" / "optimization" / "proposals" / "evil"
    evil.mkdir(parents=True, exist_ok=True)
    outside.write_text("original\n")
    # base "home" pins the resolution root, so this exercises the containment refusal itself rather than
    # the ambiguity guard — the two are different failures and each needs its own case
    (evil / "verify.json").write_text(json.dumps({"file": "../../../OUTSIDE.txt", "base": "home",
                                                  "sha256_before": sha(outside), "sha256_after": sha(outside),
                                                  "created_at": "20200101T000000"}))
    (evil / "content.new").write_text("pwned\n")
    (evil / "diff.patch").write_text("")
    escaped = run(["skills/system/curator/scripts/apply.py", "evil", "--yes"], root)
    CHECKS["containment_refused"] = (escaped.returncode != 0
                                     and ("outside the athena home" in (escaped.stdout + escaped.stderr)
                                     or "program tree" in (escaped.stdout + escaped.stderr))
                                     and outside.read_text() == "original\n")

    # --- a stale journal blocks a later apply ------------------------------
    folder5, _ = propose(root, "journal", "line one\nJ\nline three\n")
    (folder5 / "INFLIGHT.json").write_text('{"file": "docs/notes.md"}')
    blocked = run(["skills/system/curator/scripts/apply.py", folder5.name, "--yes"], root)
    CHECKS["stale_journal_refused"] = blocked.returncode != 0 and "never finished" in (blocked.stdout + blocked.stderr)
    (folder5 / "INFLIGHT.json").unlink()

    # --- guard A: the diff alone is edited, content untouched --------------
    folder3, _ = propose(root, "diff tamper", "line one\nA\nline three\n")
    (folder3 / "diff.patch").write_text("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-nothing\n+else\n")
    diff_tampered = run(["skills/system/curator/scripts/apply.py", folder3.name, "--yes"], root)
    CHECKS["diff_tamper_refused"] = (diff_tampered.returncode != 0
                                     and "diff.patch does not match" in (diff_tampered.stdout + diff_tampered.stderr))

    # --- guard B: content edited AND the diff regenerated to match --------
    folder3b, _ = propose(root, "content tamper", "line one\nB\nline three\n")
    (folder3b / "content.new").write_text("line one\nEVIL\nline three\n")
    import difflib
    old = (folder3b / "content.old").read_text()
    new = (folder3b / "content.new").read_text()
    (folder3b / "diff.patch").write_text("".join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), fromfile="a/docs/notes.md", tofile="b/docs/notes.md")))
    content_tampered = run(["skills/system/curator/scripts/apply.py", folder3b.name, "--yes"], root)
    CHECKS["content_tamper_refused"] = (content_tampered.returncode != 0
                                        and "sha256_after" in (content_tampered.stdout + content_tampered.stderr))

    # --- a stale hash is refused ------------------------------------------
    folder4, _ = propose(root, "stale", "line one\nSTALE\nline three\n")
    target.write_text("someone edited this after the proposal\n")
    stale = run(["skills/system/curator/scripts/apply.py", folder4.name, "--yes"], root)
    CHECKS["stale_hash_refused"] = stale.returncode != 0 and "changed since the proposal" in (stale.stdout + stale.stderr)

with tempfile.TemporaryDirectory() as td:
    # the freshness guard is invisible in the happy path — a fresh backup makes it a no-op — so it is tested
    # by publishing a proposal and then leaving only an old backup behind.
    base = Path(td)
    root = make_tree(base / "tree", "PASS")
    folder, _ = propose(root, "stale backup", "line one\nB\nline three\n")
    old = base / "backups" / "athena-agent-backup-999.zip"
    past = time.time() - 3600
    os.utime(old, (past, past))
    out = run(["skills/system/curator/scripts/apply.py", folder.name, "--yes"], root)
    CHECKS["stale_backup_refused"] = out.returncode != 0 and "predates this proposal" in (out.stdout + out.stderr)

with tempfile.TemporaryDirectory() as td:
    # item 3: an honest proposal must not be refused for how the file stores its last byte or its line endings
    base = Path(td)
    root = make_tree(base / "tree", "PASS")
    target = root.parent / "docs" / "nofinal.txt"
    target.write_text("first\nsecond")                 # no trailing newline
    new = root / "new-nofinal.txt"
    new.write_text("first\nSECOND")
    out = run(["skills/system/curator/scripts/propose.py", "--title", "no final newline", "--file", "docs/nofinal.txt",
               "--new", str(new), "--rationale", "r", "--evidence", "e"], root)
    folder = Path(out.stdout.strip().splitlines()[-1])
    (base / "backups").mkdir(exist_ok=True)
    bk = base / "backups" / "athena-agent-backup-998.zip"; bk.write_bytes(b"x")
    os.utime(bk, (time.time() + 5, time.time() + 5))
    applied = run(["skills/system/curator/scripts/apply.py", folder.name, "--yes"], root)
    CHECKS["no_trailing_newline_applies"] = applied.returncode == 0 and target.read_text() == "first\nSECOND"

with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root = make_tree(base / "tree", "PASS")
    target = root.parent / "docs" / "crlf.txt"
    target.write_bytes(b"one\r\ntwo\r\n")
    new = root / "new-crlf.txt"
    new.write_bytes(b"one\r\nTWO\r\n")
    out = run(["skills/system/curator/scripts/propose.py", "--title", "crlf", "--file", "docs/crlf.txt",
               "--new", str(new), "--rationale", "r", "--evidence", "e"], root)
    folder = Path(out.stdout.strip().splitlines()[-1])
    (base / "backups").mkdir(exist_ok=True)
    bk = base / "backups" / "athena-agent-backup-997.zip"; bk.write_bytes(b"x")
    os.utime(bk, (time.time() + 5, time.time() + 5))
    applied = run(["skills/system/curator/scripts/apply.py", folder.name, "--yes"], root)
    CHECKS["crlf_applies"] = applied.returncode == 0 and "TWO" in target.read_text()

with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root = make_tree(base / "tree", "PASS")
    folder, _ = propose(root, "blank diff", "line one\nZ\nline three\n")
    (folder / "diff.patch").write_text("   \n")          # present but useless
    out = run(["skills/system/curator/scripts/apply.py", folder.name, "--yes"], root)
    CHECKS["blank_diff_refused"] = out.returncode != 0 and "no usable diff.patch" in (out.stdout + out.stderr)

    folder2, _ = propose(root, "no old content", "line one\nY\nline three\n")
    (folder2 / "content.old").unlink()
    out2 = run(["skills/system/curator/scripts/apply.py", folder2.name, "--yes"], root)
    CHECKS["missing_old_content_refused"] = out2.returncode != 0 and "no usable content.old" in (out2.stdout + out2.stderr)

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
raise SystemExit(0 if all(CHECKS.values()) else 1)

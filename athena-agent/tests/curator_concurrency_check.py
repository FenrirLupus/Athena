"""Curator concurrency: one apply per proposal, and the lock must not outlive the run that took it.

The flow suite proves what a single apply does. This one proves what two of them do, because the sequence in
apply.py (write the target, then run the gate, then maybe revert) is only safe while nobody else is inside it.
A second run that entered between the write and the gate would hash-check a file the first is holding
mid-change, and either revert over it or gate content it did not verify.

Everything runs in a throwaway tree with a stub gate, like curator_flow_check.py: proving this against the real
tree would mean racing two real applies over a real file. The failing-gate case is run twice in a row on
purpose — it is the cheapest way to show the lock does not survive a failed run.
"""
import fcntl
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

TREE_SRC = Path(__file__).resolve().parents[1]
SCRIPTS = TREE_SRC / "skills" / "system" / "curator" / "scripts"
APPLY = "skills/system/curator/scripts/apply.py"
LOCK = ".apply.lock"
CHECKS = {}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args, cwd, timeout=180):
    return subprocess.run([sys.executable] + args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def both(result):
    return result.stdout + result.stderr


def gate_text(kind):
    # the two passing gates also assert the precondition of the window being proven: while a change is
    # unverified the in-flight journal must be on disk. A mutated apply that never wrote it would pass a
    # gate that did not check, and the concurrency test would be racing nothing.
    journal = ('find "$PWD/.." -name INFLIGHT.json 2>/dev/null | head -1 | grep -q INFLIGHT '
               '|| { echo "no journal"; exit 3; }\n')
    if kind == "PASS":
        return '#!/usr/bin/env bash\n' + journal + 'echo "GATE: PASS"\nexit 0\n'
    if kind == "LOSE":
        # the gate is the longest window in the run: unlink the target lock while it is held. A run that only
        # checks its ownership before the write still believes the file is its own when it decides to revert.
        return ('#!/usr/bin/env bash\n' + journal +
                'find "$PWD/.." -path "*/.curator-locks/*" -name .apply.lock -delete\n'
                'echo "GATE: FAIL"\nexit 1\n')
    if kind == "SLOW":
        # long enough that the second apply is guaranteed to arrive inside the write-then-gate window
        return '#!/usr/bin/env bash\n' + journal + 'sleep 3\necho "GATE: PASS"\nexit 0\n'
    return '#!/usr/bin/env bash\necho "GATE: FAIL"\nexit 1\n'


def make_tree(root: Path, kind: str) -> Path:
    (root / "tests").mkdir(parents=True)
    (root.parent / "docs").mkdir(parents=True, exist_ok=True)
    (root / "skills" / "system" / "curator").mkdir(parents=True)
    shutil.copytree(SCRIPTS, root / "skills" / "system" / "curator" / "scripts")
    # the curator scripts instrument through core.trace, so a tree faithful enough to run them carries it
    (root / "core").mkdir()
    shutil.copy2(TREE_SRC / "core" / "trace.py", root / "core" / "trace.py")
    gate = root / "tests" / "run_gate.sh"
    gate.write_text(gate_text(kind))
    os.chmod(gate, 0o755)
    (root.parent / "docs" / "notes.md").write_text("line one\nline two\nline three\n")
    os.chmod(root.parent / "docs" / "notes.md", 0o755)
    return root


def propose(root: Path, title: str, new_text: str):
    new = root / "new.txt"
    new.write_text(new_text)
    out = run(["skills/system/curator/scripts/propose.py", "--title", title, "--file", "docs/notes.md",
               "--new", str(new), "--rationale", "test", "--evidence", "suite"], root)
    if out.returncode != 0:
        raise SystemExit(f"propose failed: {both(out)}")
    folder = Path(out.stdout.strip().splitlines()[-1])
    # the freshness check compares against a backup newer than the proposal
    (root.parent / "backups").mkdir(exist_ok=True)
    backup = root.parent / "backups" / "athena-agent-backup-999.zip"
    backup.write_bytes(b"stub")
    future = time.time() + 5
    os.utime(backup, (future, future))
    return folder


def hold_lock(folder: Path):
    """Stand in for a first apply that is mid-flight: the same exclusive lock, taken the same way."""
    handle = open(folder / LOCK, "a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid {os.getpid()} on {socket.gethostname()} since test harness\n")
    handle.flush()
    return handle


# --- (a) a held lock refuses the second apply and writes nothing ------------
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root = make_tree(base / "tree", "PASS")
    target = root.parent / "docs" / "notes.md"
    before = sha(target)
    folder = propose(root, "held lock", "line one\nHELD\nline three\n")

    held = hold_lock(folder)
    try:
        second = run([APPLY, folder.name, "--yes"], root)
        CHECKS["held_lock_refuses"] = (second.returncode != 0
                                       and "another apply is already running" in both(second))
        CHECKS["held_lock_names_the_holder"] = f"pid {os.getpid()}" in both(second)
        CHECKS["held_lock_writes_nothing"] = sha(target) == before
        CHECKS["held_lock_starts_no_journal"] = not (folder / "INFLIGHT.json").exists()
        # a dry run would verify a file another process is mid-way through changing
        dry = run([APPLY, folder.name, "--dry-run"], root)
        CHECKS["held_lock_refuses_dry_run"] = dry.returncode != 0 and "another apply" in both(dry)
    finally:
        held.close()

    # --- (b) once the first is done, a later apply is not blocked by the file it left behind ---
    later = run([APPLY, folder.name, "--yes"], root)
    CHECKS["apply_after_release_succeeds"] = later.returncode == 0 and target.read_text() == "line one\nHELD\nline three\n"

# --- the real race: a second apply while the first sits in the gate --------
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root = make_tree(base / "tree", "SLOW")
    target = root.parent / "docs" / "notes.md"
    before = sha(target)
    folder = propose(root, "concurrent apply", "line one\nRACE\nline three\n")

    first = subprocess.Popen([sys.executable, APPLY, folder.name, "--yes"], cwd=str(root),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    journal = folder / "INFLIGHT.json"          # still needed below to assert it was cleared afterwards
    # the journal is written before the file, so waiting on it samples the wrong state; wait for the file
    # itself to differ, which is the dangerous window this test exists to catch.
    deadline = time.time() + 20
    mid = before
    while time.time() < deadline and mid == before:
        time.sleep(0.02)
        mid = sha(target)
    CHECKS["first_reached_the_window"] = mid != before
    CHECKS["window_is_written_but_ungated"] = mid != before

    second = run([APPLY, folder.name, "--yes"], root)
    CHECKS["concurrent_second_refused"] = (second.returncode != 0
                                           and "another apply is already running" in both(second))
    CHECKS["concurrent_second_wrote_nothing"] = sha(target) == mid

    out1, err1 = first.communicate(timeout=180)
    CHECKS["concurrent_first_still_succeeds"] = (first.returncode == 0
                                                 and target.read_text() == "line one\nRACE\nline three\n")
    CHECKS["journal_cleared_after_first"] = not journal.exists()

# --- (c) a failed gate must not leave the lock behind ----------------------
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root = make_tree(base / "tree", "FAIL")
    target = root.parent / "docs" / "notes.md"
    before = sha(target)
    folder = propose(root, "failing gate", "line one\nBAD\nline three\n")

    one = run([APPLY, folder.name, "--yes"], root)
    two = run([APPLY, folder.name, "--yes"], root)
    CHECKS["failed_gate_reverts"] = one.returncode == 1 and sha(target) == before
    CHECKS["failed_gate_releases_lock"] = (two.returncode == 1
                                           and "another apply" not in both(two)
                                           and "reverted" in both(two))
    CHECKS["file_intact_after_two_failures"] = sha(target) == before
    CHECKS["lock_file_left_inert"] = (folder / LOCK).read_text(encoding="utf-8") == ""

    # a refusal raised inside the lock (a SystemExit, not a return) must release it the same way
    tampered = propose(root, "refusal path", "line one\nX\nline three\n")
    (tampered / "diff.patch").write_text("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-nothing\n+else\n")
    r1 = run([APPLY, tampered.name, "--yes"], root)
    r2 = run([APPLY, tampered.name, "--yes"], root)
    CHECKS["refusal_path_releases_lock"] = (r1.returncode != 0 and r2.returncode != 0
                                            and "diff.patch does not match" in both(r2)
                                            and "another apply" not in both(r2))

# --- a killed apply: flock dies with the process, so nothing needs cleaning ---
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root = make_tree(base / "tree", "SLOW")
    target = root.parent / "docs" / "notes.md"
    folder = propose(root, "killed apply", "line one\nK\nline three\n")

    first = subprocess.Popen([sys.executable, APPLY, folder.name, "--yes"], cwd=str(root),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    journal = folder / "INFLIGHT.json"
    deadline = time.time() + 20
    while time.time() < deadline and not journal.is_file():
        time.sleep(0.02)
    first.kill()                            # SIGKILL: no finally, no atexit, no chance to tidy up
    first.communicate(timeout=60)

    after = run([APPLY, folder.name, "--yes"], root)
    # the journal still blocks it — the file holds unverified content, which is the right refusal — but the
    # reason must be the journal, not a lock the dead process never released
    CHECKS["killed_apply_leaves_no_lock"] = (after.returncode != 0
                                             and "never finished" in both(after)
                                             and "another apply" not in both(after))

# --- two different proposals aimed at the SAME target --------------------
# a per-proposal lock passes the case above and still loses an update; this is the one that proves the lock is
# keyed on the target.
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root = make_tree(base / "tree", "PASS")
    gate = root / "tests" / "run_gate.sh"
    gate.write_text('#!/usr/bin/env bash\nsleep 2\necho "GATE: PASS"\nexit 0\n')
    os.chmod(gate, 0o755)
    backup_dir = base / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / "athena-agent-backup-999.zip"
    backup.write_bytes(b"stub")
    future = time.time() + 30
    os.utime(backup, (future, future))
    target = root.parent / "docs" / "notes.md"
    folder_a = propose(root, "same target A", "line one\nAAA\nline three\n")
    folder_b = propose(root, "same target B", "line one\nBBB\nline three\n")

    running = subprocess.Popen([sys.executable, APPLY, folder_a.name, "--yes"], cwd=str(root),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 20
    while time.time() < deadline and "AAA" not in target.read_text():
        time.sleep(0.02)                      # the first apply is now inside its gate, file already written
    rival = run([APPLY, folder_b.name, "--yes"], root)
    running.communicate(timeout=180)
    CHECKS["same_target_second_refused"] = (rival.returncode != 0
                                            and "another apply is already running" in both(rival))
    CHECKS["same_target_no_lost_update"] = "AAA" in target.read_text()

# --- the lock file is deleted while the gate runs -------------------------
# flock holds an inode, not a name. Delete the name and a rival's apply creates a fresh file, locks that, finds no
# journal in its own proposal folder and proceeds — while this run still holds the old inode and believes it is
# alone. So the identity is checked at every decision, above all before the revert: the first run must refuse and
# leave the file exactly as the gate left it, rather than reverting over the write that came after it.
with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root = make_tree(base / "tree", "LOSE")
    target = root.parent / "docs" / "notes.md"
    text = "line one\nLOST\nline three\n"
    folder = propose(root, "lock lost inside the gate", text)
    ungated = hashlib.sha256(text.encode()).hexdigest()   # what this run wrote: unverified, must stay put

    lost = run([APPLY, folder.name, "--yes"], root)
    msg = both(lost)
    CHECKS["lost_lock_refuses_by_name"] = (lost.returncode != 0 and "does not own the file" in msg
                                           and "Traceback" not in msg)
    CHECKS["lost_lock_does_not_revert"] = sha(target) == ungated and "LOST" in target.read_text()
    CHECKS["lost_lock_keeps_journal"] = (folder / "INFLIGHT.json").exists()

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
raise SystemExit(0 if all(CHECKS.values()) else 1)

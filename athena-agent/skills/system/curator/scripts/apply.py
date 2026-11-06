"""Apply a curator proposal — the only step that changes anything, and the one that must fail safely.

Order, and none of it is optional:

    1  the file's hash must still match the hash recorded when the proposal was written
    2  the patch must apply cleanly in dry-run
    3  a backup must be newer than the proposal, or the apply refuses
    4  the patch is applied, then the gate is run
    5  if the gate does not pass, the patch is reversed and the file is left as it was

A proposal is a hypothesis. This is where it is tested.

Step 0, before any of it: take the proposal's lock. The order above is only safe while nobody else is inside
it — a second run entering between the write and the gate would verify against a file the first is holding
mid-change, and the gate's revert would then land on top of the second run's write.
"""
from __future__ import annotations

import argparse
import calendar
import errno

import curator_diff
import fcntl
import hashlib
import json
import os
import stat
import socket
import subprocess
import sys
import time
from pathlib import Path

for _parent in Path(__file__).resolve().parents:            # the tree that holds core/
    if (_parent / "core" / "trace.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
from core import trace

LOCK_NAME = ".apply.lock"
LANE = "curator"


def _say(text: str = "") -> None:
    """What the operator must read before consenting: the target, the diff, the outcome. Not a diagnostic."""
    print(text, flush=True)


def _refuse(reason: str, **fields) -> None:
    trace.event("curator.refused", channel=LANE, reason=reason, **fields)


def tree_root(start: Path) -> Path:
    for parent in start.parents:
        if (parent / "tests" / "run_gate.sh").is_file():
            return parent
    raise SystemExit("cannot find the tree: no tests/run_gate.sh above here")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def newest_backup(athena: Path) -> Path | None:
    found = sorted(athena.glob("backups/athena-agent-backup-*.zip"), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


_HELD: list = []


def acquire(folder: Path):
    """Take this proposal's exclusive lock, or refuse out loud, naming the process that holds it.

    An flock is released by the kernel when the holder exits or dies, so a crashed or killed apply cannot
    leave the next one locked out: the file is a handle, not a flag, and a stale file is harmless.
    Nothing here waits. A second apply means two people (or a person and a script) have lost track of each
    other; blocking would hide that, and the human would be looking at a terminal that appears to hang.
    """
    path = folder / LOCK_NAME
    try:
        handle = open(path, "a+", encoding="utf-8")
    except OSError as exc:
        # without the lock the write-then-gate window is unguarded, so this is a refusal, not a warning
        raise SystemExit(f"refused: cannot take the apply lock in {folder} ({exc})")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno not in (errno.EACCES, errno.EAGAIN):
            raise
        info = ""
        for _ in range(20):   # the holder writes its name immediately after locking; allow that gap
            try:
                info = path.read_text(encoding="utf-8").strip()
            except OSError:
                info = ""
            if info:
                break
            time.sleep(0.05)
        trace.event("curator.lock.held", channel=LANE, lock=str(path), folder=folder.name,
                    holder=info.splitlines()[0][:120] if info else "unnamed")
        raise SystemExit(
            f"refused: another apply is already running on {folder.name}\n"
            f"  holder: {info or 'unknown — the running apply has not named itself yet'}\n"
            f"  lock  : {path}\n"
            f"  wait for that process to finish and re-run. The lock is released when it exits, "
            f"including when it fails the gate or hits an error; there is nothing to clean up by hand.")
    handle.seek(0)
    # the gate runs as a child; if it inherited this fd the lock would outlive a killed apply
    os.set_inheritable(handle.fileno(), False)
    handle.truncate()
    handle.write(f"pid {os.getpid()} on {socket.gethostname()} "
                 f"since {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
    handle.flush()
    os.fsync(handle.fileno())
    trace.event("curator.lock", channel=LANE, lock=str(path), folder=folder.name, pid=os.getpid())
    return handle, path


def still_ours(locked, label: str) -> None:
    """Refuse if the lock file was deleted or replaced while we held it.

    flock guards an inode, not a name: if the file is unlinked, the next process creates a new one, locks that,
    and both runs believe they are alone. Comparing device and inode closes that.
    """
    handle, path = locked
    try:
        mine = os.fstat(handle.fileno())
        theirs = os.stat(path)
    except OSError:
        raise SystemExit(f"refused: the {label} lock file disappeared while held — another process may be running")
    if (mine.st_dev, mine.st_ino) != (theirs.st_dev, theirs.st_ino):
        raise SystemExit(f"refused: the {label} lock was replaced while held — another process may be running")


def still_ours_all(locked_proposal, locked_target, stage: str) -> None:
    """Both identities, re-checked at every point where this run makes a decision about the file.

    The gate is the longest window in the run, and flock holds an inode, not a name: a rival that unlinks the
    lock file while the gate runs gets a fresh one, locks it, finds no journal in its own proposal folder, and
    has no reason to stop. Verifying only before os.replace means the first run still believes it owns the file
    when it decides to revert — and reverts over the rival's write. Checking here, before that decision, is what
    turns "two applies" into one apply and one refusal instead of one silent data loss.
    """
    try:
        still_ours(locked_proposal, "proposal")
        still_ours(locked_target, "target")
    except SystemExit as exc:
        raise SystemExit(
            f"{exc}\n"
            f"  at: {stage} — this run does not own the file any more and has not touched it since. "
            f"The journal is left in place so the next apply refuses rather than guesses.")


def release(locked) -> None:
    """Drop the lock on every path. Truncate first, so a later refusal cannot quote a finished run."""
    handle = locked[0] if isinstance(locked, tuple) else locked
    try:
        handle.seek(0)
        handle.truncate()
        handle.flush()
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def restore(target, original: bytes, mode: int) -> bool:
    """Put the original bytes and mode back, and prove it. Returns False rather than raising.

    Three things this must survive: a read-only file, a target that stopped being a regular file, and a write
    that silently truncates. The order matters more than the checks. An lstat followed by a chmod by name is a
    hole: a symlink swapped in between them makes the chmod follow it and widen the *referent's* mode
    (0600 -> 0755) while the link stays exactly where it was, so the object that was checked and the object that
    was written are different. Open first, with O_NOFOLLOW, and change modes only through fchmod on that fd.
    The post-condition is the hash read back from an fd, not from the path that may have moved on.
    """
    fd = None
    try:
        try:
            # O_NONBLOCK so a target that became a FIFO errors (ENXIO) instead of hanging the revert forever
            fd = os.open(target, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)   # no O_TRUNC: a refused open must not empty it
        except OSError as exc:
            if exc.errno != errno.EACCES:
                return False                                    # ELOOP (link), EISDIR, ENOENT: refuse, never follow
            # a read-only target: widen *that inode* through a handle read permission already allows. fchmod on
            # the read fd cannot reach a link's referent, because the fd is the file, not the name.
            ro = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(ro).st_mode):
                    return False
                os.fchmod(ro, mode | stat.S_IWUSR)
            finally:
                os.close(ro)
            fd = os.open(target, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        os.ftruncate(fd, 0)
        view = memoryview(original)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                return False
            view = view[written:]
        os.fsync(fd)
        os.fchmod(fd, mode)                                     # the mode goes back on the same inode
    except OSError:
        return False
    finally:
        if fd is not None:
            os.close(fd)
    try:
        check = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            on_disk = b"".join(iter(lambda: os.read(check, 65536), b""))
        finally:
            os.close(check)
    except OSError:
        return False
    return hashlib.sha256(on_disk).hexdigest() == hashlib.sha256(original).hexdigest()


def clear_journal(journal: Path) -> bool:
    """Removing the journal must not be able to raise: a folder that became read-only would otherwise turn a
    successful revert into a traceback with the journal still in place."""
    try:
        journal.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def target_lock(root: Path, target: Path):
    """One lock per TARGET, not per proposal: two proposals aimed at the same file were both applying."""
    key = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:16]
    folder = root.parent / ".curator-locks" / key     # acquire() owns the file name inside it
    folder.mkdir(parents=True, exist_ok=True)
    return acquire(folder)


def record(folder: Path, name: str, text: str) -> bool:
    """Write the operation's record. A folder that went read-only must not turn an outcome into a traceback."""
    try:
        (folder / name).write_text(text, encoding="utf-8")
        return True
    except OSError as exc:
        _say(f"WARNING: could not write {name} ({exc}) — the outcome stands, the record does not")
        return False


def settled(journal: Path, outcome: str) -> bool:
    """Clear the in-flight marker and say so if it cannot be cleared: a stranded marker blocks every later run."""
    if clear_journal(journal):
        return True
    _say(f"WARNING: the in-flight marker could not be removed ({journal}); later applies will refuse until it is")
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Apply a curator proposal behind a hash check and the gate.")
    ap.add_argument("proposal", help="folder name under documentation/optimization/proposals/")
    ap.add_argument("--dry-run", action="store_true", help="verify and show the diff, change nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = ap.parse_args(argv)

    root = tree_root(Path(__file__).resolve())
    folder = root.parent / "documentation" / "optimization" / "proposals" / args.proposal
    if not folder.is_dir():
        raise SystemExit(f"no such proposal: {folder}")

    # before anything is read: the journal check, the hash check and the write-then-gate sequence below are
    # only meaningful if this process is the only one inside them
    _HELD.append(acquire(folder))
    try:
        return apply_locked(folder, root, args)
    except SystemExit as exc:
        # every refusal in apply_locked is a SystemExit whose first line says why; record that one line, once
        reason = str(exc).splitlines()[0] if str(exc) else "SystemExit"
        _refuse(reason[:200], proposal=folder.name)
        raise
    finally:
        for handle in reversed(_HELD):
            release(handle)


def apply_locked(folder: Path, root: Path, args) -> int:
    meta = json.loads((folder / "verify.json").read_text(encoding="utf-8"))
    journal = folder / "INFLIGHT.json"
    if journal.is_file():
        raise SystemExit(
            f"refused: a previous apply never finished ({journal}). The file may hold unverified content.\n"
            f"  restore it from content.old, or from the backup named in {journal.name}, then delete that file.")
    # containment: a proposal may only touch a file inside this tree. Without this, a proposal naming
    # ../../../etc/anything would be written and reported as applied.
    # the curator governs skills, tools, plugins and profiles — those live in the athena home, not the tree
    raw = Path(meta["file"])
    bases = {"home": root.parent}                 # the program tree is not a curator target, by design
    recorded = meta.get("base")
    if recorded == "tree":
        raise SystemExit("refused: this proposal targets the program tree (athena-agent/) — "
                         "the curator governs the home: skills, tools, plugins and profiles")
    if recorded in bases:
        target = (bases[recorded] / raw).resolve() if not raw.is_absolute() else raw.resolve()
    else:
        # an old proposal without a recorded root: only acceptable when the path is unambiguous
        options = [p for p in ((root / raw).resolve(), (root.parent / raw).resolve()) if p.exists()]
        if len(options) != 1:
            raise SystemExit(f"refused: {meta['file']} is ambiguous and the proposal does not record which root")
        target = options[0]
    if str(target).startswith(str(root.resolve()) + os.sep):
        raise SystemExit(f"refused: {meta['file']} is inside the program tree (athena-agent/) — "
                         f"the curator governs the home: skills, tools, plugins and profiles")
    if not str(target).startswith(str(root.parent.resolve()) + os.sep):
        raise SystemExit(f"refused: {meta['file']} resolves outside the athena home")
    patch = folder / "diff.patch"

    _say(f"proposal : {folder.name}")
    _say(f"target   : {meta['file']}")
    trace.event("curator.apply", channel=LANE, proposal=folder.name, target=meta["file"],
                dry_run=args.dry_run, sha256_before=str(meta["sha256_before"])[:12],
                bytes_old=target.stat().st_size if target.is_file() else 0)

    if not target.is_file():
        raise SystemExit("refused: the target file no longer exists")
    target_locked = target_lock(root, target)
    _HELD.append(target_locked)
    if not stat.S_ISREG(os.lstat(target).st_mode):
        raise SystemExit(f"refused: {meta['file']} is not a regular file — a link or directory is not a proposal target")
    if sha(target) != meta["sha256_before"]:
        raise SystemExit("refused: the file changed since the proposal was written — re-propose against current content")

    new_text = (folder / "content.new").read_text(encoding="utf-8") if (folder / "content.new").is_file() else ""
    if not new_text:
        raise SystemExit("refused: the proposal carries no content.new — apply would have nothing to write")
    if int(meta.get("format") or 1) < 2:
        raise SystemExit("refused: this proposal predates the single-diff format — re-propose it against current content")
    # A missing artefact must not read as consent. All three are required: the content being replaced, the
    # content being written (checked above) and the diff that a human is supposed to have read.
    for needed in ("content.old", "content.new", "diff.patch"):
        if not (folder / needed).is_file() or not (folder / needed).read_text(encoding="utf-8").strip():
            raise SystemExit(f"refused: the proposal has no usable {needed} — apply will not guess what was intended")
    old_text = (folder / "content.old").read_text(encoding="utf-8")
    stored_diff = (folder / "diff.patch").read_text(encoding="utf-8")
    rel = meta["file"]
    expected = curator_diff.unified(old_text, new_text, rel)
    if curator_diff.normalise(expected).strip() != curator_diff.normalise(stored_diff).strip():
        raise SystemExit("refused: diff.patch does not match content.new — the proposal was edited after it was written")
    if hashlib.sha256(new_text.encode("utf-8")).hexdigest() != meta.get("sha256_after"):
        raise SystemExit("refused: content.new does not match sha256_after — the proposal was edited after it was written")

    backup = newest_backup(root.parent)
    created = calendar.timegm(time.strptime(meta["created_at"], "%Y%m%dT%H%M%S"))  # the stamp is UTC
    if backup is None:
        raise SystemExit("refused: no athena-agent-backup-*.zip found — take a backup first")
    if backup.stat().st_mtime < created:
        raise SystemExit(f"refused: the newest backup predates this proposal ({backup.name}) — take a new one")

    _say(f"backup   : {backup.name}")
    trace.event("curator.verify", channel=LANE, proposal=folder.name, target=meta["file"],
                hash_matches=True, diff_matches=True, bytes_new=len(new_text.encode("utf-8")),
                backup=backup.name, backup_age_s=round(time.time() - backup.stat().st_mtime))
    _say("diff:")
    _say((patch.read_text(encoding="utf-8"))[:4000])

    if args.dry_run:
        _say("\ndry run: hash verified, diff shown, nothing applied")
        return 0
    if not args.yes:
        _say("\nre-run with --yes to apply")
        return 2

    import tempfile
    original = target.read_bytes()
    original_mode = stat.S_IMODE(target.stat().st_mode)          # a rewrite must not lose 0755
    journal.write_text(json.dumps({"file": meta["file"], "backup": str(backup),
                                   "sha256_original": hashlib.sha256(original).hexdigest(),
                                   "mode_original": oct(original_mode)}, indent=2), encoding="utf-8")
    try:
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix="." + target.name + ".", suffix=".curator")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(new_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, original_mode)                             # mode travels with the content
        still_ours_all(_HELD[0], target_locked, "before the write")
        os.replace(tmp, target)
    except Exception as exc:
        if restore(target, original, original_mode):
            clear_journal(journal)
            raise SystemExit(f"refused: could not write the change ({exc}); the file is as it was")
        record(folder, "RESTORE_FAILED.md",
               f"# RESTORE FAILED — {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n"
               f"The write failed ({exc}) and the original could not be put back.\n"
               f"Original sha256: {hashlib.sha256(original).hexdigest()}\n")
        trace.event("curator.restore.failed", level="error", channel=LANE, proposal=folder.name,
                    target=meta["file"], phase="write", bytes=len(original),
                    sha256=hashlib.sha256(original).hexdigest()[:12], error=str(exc)[:200])
        _say(f"RESTORE FAILED: the write failed ({exc}) and the file could not be restored — "
             f"see {folder.name}/RESTORE_FAILED.md; the journal is left in place deliberately")
        return 3


    _say("\napplied — running the gate")
    with trace.span("curator.gate", channel=LANE, proposal=folder.name, target=meta["file"]) as sp:
        gate = run(["bash", "tests/run_gate.sh"], root)
        passed = gate.returncode == 0 and "GATE: PASS" in gate.stdout
        tail = "\n".join(gate.stdout.strip().splitlines()[-6:])
        sp.ok(passed=passed, exit_code=gate.returncode, tail=tail[-300:].replace("\n", " | "))
    _say(tail)
    # the gate just ran for minutes with this process owning the name and nothing else done
    still_ours_all(_HELD[0], target_locked, "after the gate returned")

    if not passed:
        # the revert is the decision that destroys work: verify immediately before it, not merely once
        still_ours_all(_HELD[0], target_locked, "before the revert")
        if not restore(target, original, original_mode):
            # the gate said no and the change is live: this must never look like an ordinary revert
            trace.event("curator.restore.failed", level="error", channel=LANE, proposal=folder.name,
                        target=meta["file"], gate="failed", bytes=len(original),
                        sha256=hashlib.sha256(original).hexdigest()[:12])
            record(folder, "RESTORE_FAILED.md",
                   f"# RESTORE FAILED — {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n"
                   f"The gate failed and the original could not be written back. The unverified change is still on "
                   f"disk.\nOriginal sha256: {hashlib.sha256(original).hexdigest()}\n\n```\n{tail}\n```\n")
            _say("\nRESTORE FAILED: the gate refused this change and it is still on disk")
            return 3
        settled(journal, "reverted")
        record(folder, "APPLIED.md",
               f"# Apply failed — {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n"
               f"Gate did not pass; the patch was reversed and the file restored.\n\n```\n{tail}\n```\n")
        trace.event("curator.revert", channel=LANE, proposal=folder.name, target=meta["file"],
                    bytes=len(original), sha256=hashlib.sha256(original).hexdigest()[:12],
                    gate="failed", restored=True)
        _say("\nreverted: the gate did not pass")
        return 1

    # the keep path claims the file is now the verified content: that claim is only ours to make while ours
    still_ours_all(_HELD[0], target_locked, "before recording the result")
    settled(journal, "applied")
    record(folder, "APPLIED.md",
           f"# Applied — {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n"
           f"- file: `{meta['file']}`\n- backup: `{backup.name}`\n- gate: PASS\n")
    trace.event("curator.applied", channel=LANE, proposal=folder.name, target=meta["file"],
                bytes=len(new_text.encode("utf-8")), sha256_after=str(meta["sha256_after"])[:12], gate="pass")
    _say("\napplied and gated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The live session — `sessions/LATEST.jsonl`, plus compression into a dated snapshot.

Layout (documentation/architecture/15-session-storage.md):

    sessions/LATEST.jsonl                    the live window — appended as turns arrive
    sessions/session_<date>_<time>.jsonl     a snapshot of the turns that left the window
    sessions/storage/vault.db                copy of every evicted turn, written BEFORE the truncate
    sessions/compression/SUMMARY.md          the digest, derived from the snapshots
    sessions/compression/summary_<date>_<time>.md   the previous digest, archived on each write

Order of operations on compression, never reordered:

    1 snapshot the evicted turns to a dated file   2 fsync
    3 copy them into the vault                     4 write SUMMARY.md
    5 truncate LATEST.jsonl to the retained tail   ← the only destructive step, last
"""
from __future__ import annotations

import calendar
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from data.vault import Vault


def _free_name(directory: Path, stamp: str, suffix: str = ".jsonl", prefix: str = "session_") -> str:
    """`session_<YYYY-MM-DD>_<HH-MM-SS>.jsonl`, never overwriting and never carrying a suffix.

When the chosen second is already taken the second is advanced until a free name exists, so every snapshot keeps
the `session_<date>_<time>` shape and its name states when it was written."""
    # calendar.timegm, not time.mktime: the stamp is UTC, and mktime reads it as local time — which shifted a
    # collision bump by the timezone offset (14:35 became 19:35 on a UTC-5 box)
    base = calendar.timegm(time.strptime(stamp, "%Y-%m-%d_%H-%M-%S"))
    for step in range(0, 120):
        candidate = time.strftime("%Y-%m-%d_%H-%M-%S", time.gmtime(base + step))
        name = f"{prefix}{candidate}{suffix}"
        if not (directory / name).exists():
            return name
    return f"{prefix}{time.strftime('%Y-%m-%d_%H-%M-%S', time.gmtime(base + 121))}{suffix}"


def _atomic_write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(lines))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Session:
    """One agent's continuous thread: JSONL live file, dated snapshots, vault copy."""

    def __init__(self, channel: str, root: Path, keep_after_compress: float = 0.5):
        self.channel = channel
        self.root = Path(root)
        self.dir = self.root / "sessions"
        self.path = self.dir / "LATEST.jsonl"
        self.summary_path = self.root / "sessions" / "compression" / "SUMMARY.md"
        self.vault = Vault(self.root / "sessions" / "storage" / "vault.db", channel)
        self.keep_after_compress = float(keep_after_compress)
        self._lock = threading.Lock()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.messages: list[dict] = self._read()

    # -- disk --------------------------------------------------------------
    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue                     # a corrupt tail line costs that line, not the file
        return out

    def _append(self, msg: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # -- api ---------------------------------------------------------------
    def add(self, role: str, content: str, **extra) -> dict:
        msg = {"role": role, "content": content,
               "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        msg.update({k: v for k, v in extra.items() if v is not None})
        with self._lock:
            self.messages.append(msg)
            self._append(msg)
        return msg

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> dict:
        return self.add("tool", content, tool_call_id=tool_call_id, name=name)

    def add_assistant_tool_calls(self, content: str, tool_calls: list[dict]) -> dict:
        return self.add("assistant", content, tool_calls=tool_calls)

    def history_all(self) -> list[dict]:
        """Every message this channel has exchanged, oldest first, across snapshots *and* the live window.

        `LATEST.jsonl` is what the model is sent; the snapshots are what compression evicted. The web thread
        wants both, in order — so this merges them and drops duplicates, because a compression copies rows into
        a snapshot *and* into the vault, and `clear` writes a full copy as well.
        """
        rows: list[dict] = []
        seen: set[tuple] = set()
        files = sorted(self.dir.glob("session_*.jsonl")) + ([self.path] if self.path.exists() else [])
        for path in files:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        key = (msg.get("ts"), msg.get("role"), (msg.get("content") or "")[:200])
                        if key in seen:
                            continue
                        seen.add(key)
                        rows.append(msg)
            except OSError:
                continue
        rows.sort(key=lambda m: str(m.get("ts") or ""))
        return rows

    def live_keys(self) -> set[tuple]:
        """Which messages are in the window the model actually receives right now."""
        with self._lock:
            live = list(self.messages)
        return {(m.get("ts"), m.get("role"), (m.get("content") or "")[:200]) for m in live}

    def history(self, limit: int | None = None) -> list[dict]:
        with self._lock:
            msgs = list(self.messages)
        return msgs[-limit:] if limit else msgs

    def size(self) -> int:
        with self._lock:
            return len(self.messages)

    # -- compression -------------------------------------------------------
    def compress(self, evict_count: int, digest_fn=None) -> dict:
        """Move the oldest `evict_count` turns out of the live window, losing nothing."""
        with self._lock:
            total = len(self.messages)
            evict_count = max(0, min(int(evict_count), total))
            if evict_count == 0:
                return {"evicted": 0, "retained": total, "snapshot": None,
                        "vaulted": 0, "summary_written": False, "summary_archived": None}
            evicted = self.messages[:evict_count]
            retained = self.messages[evict_count:]

            # Unique snapshot name: two compressions can land in the same second, and a colliding
                        # name would overwrite the snapshot taken in the same second, leaving the turns only in the vault.
            
            stamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.gmtime())
            snap = self.dir / _free_name(self.dir, stamp)

            # 1 + 2: snapshot durably
            _atomic_write(snap, [json.dumps(m, ensure_ascii=False) + "\n" for m in evicted])
            # 3: copy into the vault before anything is destroyed
            vaulted = self.vault.insert_messages(evicted, snap.stem)
            # 4: derive the digest
            digest = ""
            if digest_fn is not None:
                try:
                    # the model digest wants the snapshot name for provenance; the mechanical one takes evicted
                    try:
                        digest = digest_fn(evicted, snap.stem) or ""
                    except TypeError:
                        digest = digest_fn(evicted) or ""
                except Exception:            # noqa: BLE001 — a bad digest must not abort a compression
                    digest = ""
            archived_summary = None
            if digest:
                archived_summary = self._write_summary(digest, snap.stem, len(evicted))
            # 5: only now truncate the live window
            _atomic_write(self.path, [json.dumps(m, ensure_ascii=False) + "\n" for m in retained])
            self.messages = retained

        return {"evicted": len(evicted), "retained": len(retained), "snapshot": str(snap),
                "vaulted": vaulted, "summary_written": bool(digest),
                "summary_archived": archived_summary}

    def _archive_summary(self) -> str | None:
        """Copy the current SUMMARY.md aside as summary_<date>_<time>.md before it is rewritten.

        The previous digest is preserved exactly as it was — the same pre-image rule the code tree
        uses for updates. A digest that gets replaced without a copy is a digest nobody can audit.
        """
        if not self.summary_path.exists():
            return None
        try:
            current = self.summary_path.read_text(encoding="utf-8")
        except OSError:
            return None
        # nothing but the header: there is no digest to keep yet
        body_lines = [ln for ln in current.splitlines()
                      if ln.strip() and not ln.startswith("# Summary")]
        if not body_lines:
            return None
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.gmtime())
        target = self.summary_path.parent / _free_name(self.summary_path.parent, stamp, ".md", "summary_")
        target.write_text(current, encoding="utf-8")
        return str(target)

    def _write_summary(self, digest: str, snapshot: str, count: int) -> str | None:
        """Archive the previous digest, then write SUMMARY.md fresh.

The previous digest is kept as `summary_<YYYY-MM-DD>_<HH-MM-SS>.md`, so every rewrite leaves its pre-image on
disk and exactly one digest is current."""
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)
        header = "# Summary — what happened before the window\n"
        archived = self._archive_summary()
        block = f"## {snapshot} · {count} turns\n\n{digest.strip()}\n"
        self.summary_path.write_text(header + "\n" + block, encoding="utf-8")
        return archived

    def summary_text(self, limit_chars: int = 8000) -> str:
        try:
            text = self.summary_path.read_text(encoding="utf-8")
        except OSError:
            return ""
        return text[-limit_chars:] if len(text) > limit_chars else text

    # -- maintenance -------------------------------------------------------
    def clear(self, archive: bool = True) -> None:
        with self._lock:
            if archive and self.path.exists():
                # exactly the same shape as a compression snapshot — no suffix of any kind. The name is read
                # by the agent and the operator, so it says when and nothing else.
                stamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.gmtime())
                os.replace(self.path, self.dir / _free_name(self.dir, stamp))
            elif self.path.exists():
                self.path.unlink()
            self.messages = []

    def snapshots(self) -> list[Path]:
        return sorted(self.dir.glob("session_*.jsonl"))

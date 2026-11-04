"""Two watchdogs over one baseline: inside and outside.

    RED TEAM    tampering that comes from *within* the architecture — an agent calling a tool, a plugin running code,
                a prompt-injected turn. The threat has credentials; what it lacks is permission.
    BLUE TEAM   tampering that comes from *outside* — something editing a file on disk, a stray plugin folder, a
                changed permission bit, another process. The threat has permission; what it lacks is the architecture.

Both read the same evidence and split it by **attribution**: a changed file is only "internal" if the runtime ledger
shows an agent or plugin reaching for that path. Otherwise it is external, because nothing inside asked for it.

    baseline   sha256 of every critical file, taken when the system is known good
    ledger     append-only events from the guard and the plugin loader (refusals, violations, secret attempts)
    findings   what differs from the baseline, classified, with guidance for the agents who must act on it

The guidance matters as much as the detection: a finding an agent cannot act on is noise, and noise gets ignored.

    "do not try to write it again — report it to the user and continue elsewhere"

Both run in the background (`SecurityWatch` thread, started at boot) and never act on their own. They *report*;
the agents and the user decide.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

BASELINE_NAME = "baseline.json"
LEDGER_NAME = "events.jsonl"
REPORT_NAME = "REPORT.md"
SEVERITY = {"critical": 3, "high": 2, "medium": 1, "low": 0}

# What counts as "the system". Anything an agent may legitimately change is excluded — otherwise every turn is a
# finding, and a watchdog that cries wolf is worse than none.
CRITICAL_ROOTS = (
    ("athena-agent", (".py", ".yaml", ".yml", ".toml", ".js", ".html", ".css", ".md", ".sh")),
    ("tools", (".md", ".py", ".yaml", ".yml")),
    ("plugins", (".md", ".py", ".yaml", ".yml")),
)
CRITICAL_FILES = ("config.yaml",)
# A plugin's own venv is not part of what was installed — it is generated, thousands of files, and every `pip` run
# would rewrite it. Hashing it made the blue team report an authorised dependency update as tampering.
SKIP_PARTS = {"__pycache__", "graphify", "backups", "logs", "metrics", ".git", "node_modules",
              ".venv", "venv", ".mypy_cache", "site-packages"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_critical(athena_root: Path):
    """Every file that the architecture is made of — code, descriptors, plugins, the config."""
    for name, suffixes in CRITICAL_ROOTS:
        base = athena_root / name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            if SKIP_PARTS & set(path.parts):
                continue
            yield path
    for name in CRITICAL_FILES:
        path = athena_root / name
        if path.is_file():
            yield path


class Ledger:
    """Append-only record of what the guards inside already refused. This is the evidence for attribution."""

    # One event per (team, kind, path) per window. A page that polls every two seconds without a valid token produced
    # 2,044 rows in an hour and buried everything else — a ledger that can be flooded by a broken client is not
    # evidence, it is a log file.
    WINDOW_S = 60.0
    MAX_ROWS = 20000

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / LEDGER_NAME
        self._lock = threading.Lock()
        self._last: dict[tuple, float] = {}
        self._suppressed: dict[tuple, int] = {}

    def record(self, team: str, kind: str, detail: str, path: str = "", lane: str = "",
               agent_id: str = "") -> bool:
        """Append one event, at most once per key per window. Returns True when the row was written."""
        now = time.time()
        key = (team, kind, str(path) or str(detail)[:40])
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self.WINDOW_S:
                self._suppressed[key] = self._suppressed.get(key, 0) + 1
                folded = self._suppressed[key]
                # say something every hundred: silence during a storm is indistinguishable from a quiet system
                if folded % 100:
                    return False
                self._suppressed[key] = 0
                suppressed = folded
            else:
                self._last[key] = now
                suppressed = self._suppressed.pop(key, 0)

        row = {"at": now, "team": team, "kind": kind, "path": str(path) or "",
               "lane": str(lane) or "", "detail": str(detail)[:500]}
        if agent_id:
            row["agent_id"] = str(agent_id)
        if suppressed:
            row["suppressed"] = suppressed          # how many identical events were folded into this one
        try:
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
        except OSError:
            return False
        self._rotate_if_large()
        return True

    def _rotate_if_large(self) -> None:
        """Keep the ledger bounded: over MAX_ROWS, the newest half survives and the rest goes to a dated file."""
        try:
            if self.path.stat().st_size < 2 * 1024 * 1024:
                return
            with self._lock:
                lines = self.path.read_text(encoding="utf-8").splitlines()
                if len(lines) <= self.MAX_ROWS:
                    return
                keep = lines[-self.MAX_ROWS // 2:]
                archive = self.folder / f"events_{time.strftime('%Y-%m-%d_%H-%M-%S')}.jsonl"
                archive.write_text("\n".join(lines[:len(lines) - len(keep)]) + "\n", encoding="utf-8")
                self.path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            pass

    def recent(self, seconds: float = 3600.0) -> list[dict]:
        if not self.path.is_file():
            return []
        cutoff = time.time() - seconds
        rows = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue                      # a torn last line is not a finding
                if float(row.get("at") or 0) >= cutoff:
                    rows.append(row)
        except OSError:
            return []
        return rows


class Baseline:
    """Hashes of the critical set, and the check that compares the disk to them."""

    def __init__(self, athena_root: Path, folder: Path):
        self.athena_root = Path(athena_root)
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / BASELINE_NAME

    def build(self) -> dict:
        rows = {}
        for path in _iter_critical(self.athena_root):
            try:
                stat = path.stat()
            except OSError:
                continue
            rows[str(path.relative_to(self.athena_root))] = {
                "sha256": _sha256(path), "bytes": stat.st_size, "mode": oct(stat.st_mode)[-3:],
                "mtime": int(stat.st_mtime)}
        data = {"built_at": time.time(), "root": str(self.athena_root), "files": rows}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o400)
        except OSError:
            pass
        return {"files": len(rows), "path": str(self.path)}

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}


def classify(rel_path: str, ledger: Ledger, window_s: float = 3600.0) -> tuple[str, str]:
    """Internal or external? Attribution is evidence, not a guess dressed up as proof."""
    target = rel_path.rstrip("/")
    for row in ledger.recent(window_s):
        recorded = str(row.get("path") or "").rstrip("/")
        if recorded and (recorded.endswith(target) or target.endswith(recorded) or recorded == target):
            return "red", f"the runtime ledger shows an internal attempt on this path ({row.get('kind')})"
    return "blue", "no internal request for this path was recorded — the change came from outside"


def check(athena_root: Path, folder: Path, ledger: Ledger,
          baseline: Baseline | None = None) -> list[dict]:
    """Compare the disk to the baseline and classify every difference."""
    baseline = baseline or Baseline(athena_root, folder)
    data = baseline.load()
    known = data.get("files") or {}
    if not known:
        return [{"severity": "medium", "team": "blue", "kind": "no-baseline",
                 "path": "", "detail": "no baseline exists — build one with `./athena.sh security baseline`",
                 "guidance": "The architecture cannot tell you whether it has been changed. Ask the user to "
                             "re-baseline once they are satisfied the tree is clean."}]

    findings = []
    seen = set()
    for path in _iter_critical(athena_root):
        rel = str(path.relative_to(athena_root))
        seen.add(rel)
        try:
            stat = path.stat()
            digest = _sha256(path)
        except OSError:
            continue
        record = known.get(rel)
        if record is None:
            team, why = classify(rel, ledger)
            findings.append({"severity": "high", "team": team, "kind": "added",
                             "path": rel, "detail": f"new file in the critical set ({why})",
                             "guidance": _guidance(team, "added")})
            continue
        if record.get("sha256") != digest:
            team, why = classify(rel, ledger)
            findings.append({"severity": "critical", "team": team, "kind": "modified",
                             "path": rel, "detail": f"content changed ({why})",
                             "guidance": _guidance(team, "modified")})
        elif record.get("mode") and record["mode"] != oct(stat.st_mode)[-3:]:
            team, why = classify(rel, ledger)
            findings.append({"severity": "high", "team": team, "kind": "permissions",
                             "path": rel,
                             "detail": f"mode {record['mode']} → {oct(stat.st_mode)[-3:]} ({why})",
                             "guidance": _guidance(team, "permissions")})

    for rel in known:
        if rel not in seen:
            team, why = classify(rel, ledger)
            findings.append({"severity": "critical", "team": team, "kind": "removed",
                             "path": rel, "detail": f"file is gone ({why})",
                             "guidance": _guidance(team, "removed")})
    return sorted(findings, key=lambda f: (-SEVERITY.get(f["severity"], 0), f["path"]))


def _guidance(team: str, kind: str) -> str:
    """What an agent should *do* — short, specific, and never "try harder"."""
    if team == "red":
        return ("An internal attempt reached this path. Do not retry the action, do not look for another route, and "
                "do not attempt to repair it yourself — say what you tried and stop. The user decides.")
    return {
        "modified": ("Something outside the architecture changed this file. Do not overwrite it to 'restore' it — "
                     "say which file changed and let the user check what did it. If you need it working now, work "
                     "from your own sandbox copy."),
        "added": ("A file appeared that the architecture did not put there. Do not run it and do not import it. "
                  "Report the path; the user decides whether it belongs."),
        "removed": ("A file the architecture needs is missing. Do not recreate it from memory — the version you "
                    "would write is not the version that was there. Report it and stop."),
        "permissions": ("Access rights changed on a file the architecture protects. Report the path and the modes; "
                        "do not 'fix' the mode yourself."),
    }.get(kind, "Report this to the user.")


def report(findings: list[dict], baseline: dict, ledger_rows: int) -> str:
    """A short markdown report — this is what the agents are pointed at."""
    red = [f for f in findings if f["team"] == "red"]
    blue = [f for f in findings if f["team"] == "blue"]
    lines = ["# Security report", ""]
    built = baseline.get("built_at")
    lines.append(f"baseline: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(built)) if built else 'none'} "
                 f"({len(baseline.get('files') or {})} files) · ledger events in the last hour: {ledger_rows}")
    lines.append("")
    if not findings:
        lines += ["Nothing differs from the baseline. No internal attempts, no external changes.", ""]
        return "\n".join(lines)

    for title, group in (("Red team — tampering from inside", red),
                         ("Blue team — tampering from outside", blue)):
        lines.append(f"## {title} ({len(group)})")
        lines.append("")
        if not group:
            lines += ["nothing to report.", ""]
            continue
        for finding in group[:20]:
            lines.append(f"- **{finding['severity']}** `{finding['kind']}` {finding['path'] or '(no path)'}")
            lines.append(f"  - {finding['detail']}")
            lines.append(f"  - what to do: {finding['guidance']}")
        if len(group) > 20:
            lines.append(f"- … and {len(group) - 20} more")
        lines.append("")
    return "\n".join(lines)


class SecurityWatch(threading.Thread):
    """The background half: check on an interval, write the report, leave the acting to the agents."""

    def __init__(self, athena_root: Path, folder: Path, interval_s: float = 300.0, on_finding=None, extras=None):
        super().__init__(daemon=True, name="athena-security")
        self.athena_root = Path(athena_root)
        self.folder = Path(folder)
        self.interval_s = max(30.0, float(interval_s))
        self.onset = on_finding
        self.extras = extras          # callable() -> extra findings (e.g. the socket audit)
        self._stop = threading.Event()
        self.ledger = Ledger(self.folder)
        self.baseline = Baseline(self.athena_root, self.folder)
        self.last: dict = {"at": 0.0, "findings": [], "counts": {"red": 0, "blue": 0}}

    def sweep(self) -> dict:
        findings = check(self.athena_root, self.folder, self.ledger, self.baseline)
        if self.extras is not None:
            try:
                findings = findings + list(self.extras() or [])
            except Exception:                        # noqa: BLE001 — a watcher must not die on one source
                pass
        meta = self.baseline.load()
        text = report(findings, meta, len(self.ledger.recent()))
        try:
            (self.folder / REPORT_NAME).write_text(text, encoding="utf-8")
        except OSError:
            pass
        counts = {"red": sum(1 for f in findings if f["team"] == "red"),
                  "blue": sum(1 for f in findings if f["team"] == "blue")}
        self.last = {"at": time.time(), "findings": findings, "counts": counts}
        if findings and self.onset is not None:
            try:
                self.onset(findings)
            except Exception:                        # noqa: BLE001 — a watcher must not die
                pass
        return self.last

    def run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.sweep()

    def stop(self) -> None:
        self._stop.set()

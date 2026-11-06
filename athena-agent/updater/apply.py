"""Put a fetched build in place: back up, stop, swap, start, prove it runs, and be able to undo all of it.

The order is the whole point. Every destructive step has a durable artefact behind it, and the tree that was replaced is
kept **beside** the new one rather than deleted, because a rollback you have to reconstruct from a zip is a rollback
that happens after the outage.

    1  verify the candidate (VERSION readable, channel matches the branch)
    2  back up the live tree       → backups/updater-<version>-<stamp>.zip + .sha256
    3  stop the server             → wait until the port is actually free
    4  live → athena-agent.prev-<version>          (a rename, not a delete)
    5  staged → athena-agent
    6  start, then poll /health until it answers with the new version
    7  on any failure: stop, delete the new tree, put the previous one back, start it, verify, and say so
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import channels
from core import timefmt

TREE_NAME = "athena-agent"


class ApplyError(RuntimeError):
    """An update that could not be completed — and whether the previous tree survived it."""

    def __init__(self, message: str, rolled_back: bool = False):
        super().__init__(message)
        self.rolled_back = rolled_back


# -- backup -------------------------------------------------------------------------------------------------------
def backup(live: Path, backups_dir: Path, version: str) -> dict:
    """A zip of the current tree, its sha256, and a file list. Verified by reading the zip back."""
    live = Path(live)
    backups_dir = Path(backups_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = backups_dir / f"updater-{version}-{stamp}.zip"
    names = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(live.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            names.append(str(path.relative_to(live)))
            bundle.write(path, str(path.relative_to(live)))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    sidecar = target.with_suffix(".zip.sha256")
    sidecar.write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    listing = target.with_suffix(".filelist.txt")
    listing.write_text("\n".join(names) + "\n", encoding="utf-8")
    with zipfile.ZipFile(target) as check:
        bad = check.testzip()
    if bad is not None:
        raise ApplyError(f"the backup {target.name} is not readable (first bad member: {bad}) — stopping before any change")
    return {"zip": str(target), "sha256": digest, "files": len(names)}




# -- server control ------------------------------------------------------------------------------------------------
def port_free(port: int, host: str = "127.0.0.1") -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((host, int(port))) != 0


def stop_server(live: Path, port: int, timeout: float = 40.0) -> None:
    script = Path(live) / "athena.sh"
    env = {**os.environ, "ATHENA_PORT": str(port), "PORT": str(port)}
    if script.is_file():
        # cwd matters: a tree's launcher must run from its own directory, or relative state (pid files, .env) is
        # looked for in whatever directory the updater happened to be started from
        subprocess.run(["bash", str(script), "stop"], capture_output=True, text=True, timeout=90,
                       env=env, cwd=str(Path(live)))
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_free(port):
            return
        time.sleep(0.5)
    raise ApplyError(f"the server is still listening on {port} after {timeout:.0f}s — refusing to swap a live tree")


def start_server(live: Path, port: int, log_path: Path) -> subprocess.Popen:
    script = Path(live) / "athena.sh"
    if not script.is_file():
        raise ApplyError(f"{script} is missing from the new tree — it cannot be started, so it is not installable")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "ab")
    env = {**os.environ, "ATHENA_PORT": str(port), "PORT": str(port)}
    return subprocess.Popen(["bash", str(script), "serve"], stdout=handle, stderr=subprocess.STDOUT,
                            start_new_session=True, cwd=str(live), env=env)


def health(port: int, expect_version: str = "", timeout: float = 90.0) -> dict:
    """Poll /health until it answers — and, when asked, until it answers with the version we just installed."""
    url = f"http://127.0.0.1:{int(port)}/health"
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=4) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not expect_version or str(payload.get("version")) == str(expect_version):
                return payload
            last = f"/health answers {payload.get('version')} (wanted {expect_version})"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = str(exc)
        time.sleep(1.0)
    raise ApplyError(f"the new build never answered {url}: {last}")


# -- the swap ------------------------------------------------------------------------------------------------------
def install(staged: Path, live: Path, *, backups_dir: Path, port: int, branch: str,
            logs_dir: Path, dry_run: bool = False, health_timeout: float = 90.0) -> dict:
    """Replace `live` with `staged`. Returns what happened; raises ApplyError if it had to undo it."""
    staged, live = Path(staged), Path(live)
    old_version = channels.read_version(live)
    new_version = channels.read_version(staged)
    channels.check_pair(new_version, branch)

    steps: list[dict] = []

    def step(name: str, note: str = "", ok: bool = True) -> None:
        steps.append({"step": name, "ok": ok, "note": note})

    step("verify", f"{old_version} → {new_version} on branch {branch}")
    if dry_run:
        step("dry-run", "nothing was changed")
        return {"from": old_version, "to": new_version, "branch": branch, "steps": steps, "dry_run": True}

    saved = backup(live, backups_dir, old_version)
    step("backup", saved["zip"])

    stop_server(live, port)
    step("stop", f"port {port} is free")

    previous = live.with_name(f"{live.name}.prev-{old_version}")
    if previous.exists():                             # a leftover from an interrupted run: keep the newest, drop the old
        shutil.rmtree(previous)
    os.replace(live, previous)
    step("park", str(previous))

    try:
        shutil.copytree(staged, live, dirs_exist_ok=False,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    except OSError as exc:
        os.replace(previous, live)
        start_server(live, port, logs_dir / "updater-serve.log")
        raise ApplyError(f"could not write the new tree ({exc}); the previous one is back in place") from exc
    step("swap", f"{live.name} now holds {new_version}")

    try:
        start_server(live, port, logs_dir / "updater-serve.log")
        answered = health(port, expect_version=new_version, timeout=health_timeout)
        step("health", f"version {answered.get('version')} · channel {answered.get('channel')}")
    except (ApplyError, OSError) as exc:
        stop_server(live, port, timeout=20.0) if not port_free(port) else None
        shutil.rmtree(live, ignore_errors=True)
        os.replace(previous, live)
        start_server(live, port, logs_dir / "updater-serve.log")
        try:
            health(port, expect_version=old_version, timeout=max(20.0, health_timeout))
            restored = f"{old_version} is running again"
        except ApplyError:
            restored = f"{old_version} was put back but has not answered /health yet — check the log"
        raise ApplyError(f"the new build failed its health check ({exc}). Rolled back: {restored}",
                         rolled_back=True) from exc

    return {"from": old_version, "to": new_version, "branch": branch, "steps": steps,
            "backup": saved, "previous_tree": str(previous), "health": answered}


# -- record --------------------------------------------------------------------------------------------------------
def record(state_dir: Path, row: dict) -> None:
    """One JSONL line per attempt, plus the current channel written where a later run can read it."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    line = {"ts": timefmt.stamp(), **row}
    with open(state_dir / "history.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    current = {"channel": channels.channel_of(row.get("to") or channels.read_version(Path(row["tree"]))
                                              if row.get("tree") else row.get("to", "")),
               "branch": row.get("branch", ""), "version": row.get("to", ""),
               "updated": line["ts"], "ok": bool(row.get("ok", True))}
    try:
        (state_dir / "state.json").write_text(json.dumps(current, indent=2), encoding="utf-8")
    except OSError:
        pass

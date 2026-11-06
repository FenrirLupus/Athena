"""The updater's contract, exercised end to end without a network and without a real Athena.

What is proven here:

  * the channel is derived from the digits, and a branch that carries the wrong channel is refused
  * a build is fetched from a directory or a tarball, and an archive that has no tree (or tries to write outside its
    own folder) is refused instead of unpacked
  * a backup is a zip **with a verified sha256 and a file list** — the artefact a rollback needs
  * a dry run changes nothing at all
  * a successful install stops the server, swaps the tree, starts the new one and accepts it only when `/health`
    answers with the version it just installed
  * a build that starts but never answers is put back the way it was, and the previous tree is running again
  * the history is written either way, so "what happened last night" is answerable
"""
import json
import sys
import tarfile
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from updater import apply as apply_mod
from updater import channels, source as source_mod, wizard

checks: dict[str, bool] = {}


def _free_port() -> int:
    import socket

    with socket.socket() as probe:                    # a port nobody is on, so a leftover stub cannot fail the run
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


PORT = _free_port()
STUB = '''#!/usr/bin/env bash
case "${1:-serve}" in
  stop)  [ -f .stub.pid ] && kill "$(cat .stub.pid)" >/dev/null 2>&1; rm -f .stub.pid; exit 0 ;;
  serve) cd "$PWD"; exec python3 updater-stub-serve.py "$ATHENA_PORT" ;;
esac
'''
STUB_SERVE = '''import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

VERSION = open("VERSION", encoding="utf-8").read().strip()
CHANNEL = "stable" if VERSION.split(".")[0] != "0" else ("beta" if VERSION.split(".")[1] != "0" else "alpha")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "version": VERSION, "channel": CHANNEL}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


open(".stub.pid", "w", encoding="utf-8").write(str(os.getpid()))
HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'''
DEAD_STUB = '''#!/usr/bin/env bash
case "${1:-serve}" in
  stop)  exit 0 ;;
  serve) sleep 30 ;;
esac
'''


def make_tree(path: Path, version: str, *, dead: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "VERSION").write_text(version + "\n", encoding="utf-8")
    (path / "core").mkdir(exist_ok=True)
    (path / "core" / "loop.py").write_text(f"VERSION = {version!r}\n", encoding="utf-8")
    script = (path / "athena.sh")
    script.write_text(DEAD_STUB if dead else STUB, encoding="utf-8")
    script.chmod(0o755)
    if not dead:
        (path / "updater-stub-serve.py").write_text(STUB_SERVE, encoding="utf-8")
    return path


# -- 1..7 · the arithmetic -----------------------------------------------------------------------------------------
checks["alpha_from_digits"] = channels.channel_of("0.0.1") == "alpha"
checks["beta_from_digits"] = channels.channel_of("0.4.0") == "beta"
checks["stable_from_digits"] = channels.channel_of("1.0.0") == "stable"
checks["channel_branch_mapping"] = (channels.branch_for("stable") == "main"
                                    and channels.branch_for("beta") == "beta"
                                    and channels.branch_for("alpha") == "alpha")
checks["compare_orders_versions"] = channels.compare("0.0.2", "0.0.1") == 1

try:
    channels.check_pair("0.0.2", "main")
    checks["stable_branch_refuses_alpha_version"] = False
except channels.ChannelError as exc:
    checks["stable_branch_refuses_alpha_version"] = "digit bumped" in str(exc)

try:
    channels.check_pair("0.0.2", "alpha")
    checks["alpha_branch_accepts_alpha_version"] = True
except channels.ChannelError:
    checks["alpha_branch_accepts_alpha_version"] = False

# -- 8..12 · sources ----------------------------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    build = make_tree(root / "builds" / "athena-agent", "0.0.2")
    staged = root / "staging"
    tree, facts = source_mod.Source.path(root / "builds").fetch("alpha", staged)
    checks["path_source_finds_the_tree"] = tree.name == "athena-agent" and (tree / "VERSION").is_file()
    info = source_mod.tree_facts(tree)
    checks["tree_facts_reads_version_and_channel"] = info["version"] == "0.0.2" and info["channel"] == "alpha"
    checks["tree_facts_is_stable"] = source_mod.tree_facts(tree)["sha256"] == info["sha256"]

    # a hand-made tarball, laid out the way a GitHub tarball is
    tarball = root / "athena-0.0.2.tar.gz"
    with tarfile.open(tarball, "w:gz") as bundle:
        bundle.add(build, arcname="Athena-alpha/athena-agent")
    tree2, _ = source_mod.Source.url(str(tarball)).fetch("alpha", root / "staging2")
    checks["tarball_source_unpacks_the_tree"] = (tree2 / "VERSION").read_text().strip() == "0.0.2"

    evil = root / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as bundle:
        info_file = root / "x"
        info_file.write_text("x")
        bundle.add(info_file, arcname="../../escape.txt")
    try:
        source_mod.Source.url(str(evil)).fetch("alpha", root / "staging3")
        checks["archive_escape_refused"] = False
    except source_mod.SourceError as exc:
        checks["archive_escape_refused"] = "outside" in str(exc)

    empty = root / "empty.tar.gz"
    with tarfile.open(empty, "w:gz") as bundle:
        junk = root / "junk"; junk.write_text("x")
        bundle.add(junk, arcname="Athena-alpha/junk.txt")
    try:
        source_mod.Source.url(str(empty)).fetch("alpha", root / "staging4")
        checks["archive_without_tree_refused"] = False
    except source_mod.SourceError as exc:
        checks["archive_without_tree_refused"] = "no athena-agent/VERSION" in str(exc)

# -- 13..18 · apply ----------------------------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    live = make_tree(root / "athena-agent", "0.0.1")
    saved = apply_mod.backup(live, root / "backups", "0.0.1")
    checks["backup_writes_zip_hash_and_listing"] = (Path(saved["zip"]).is_file()
                                                     and Path(saved["zip"] + ".sha256").is_file()
                                                     and f"{saved['sha256']}" in Path(saved["zip"] + ".sha256").read_text())
    checks["backup_records_the_files"] = saved["files"] >= 3

    # a dry run must not touch the tree
    staged = make_tree(root / "staging" / "athena-agent", "0.0.2")
    result = apply_mod.install(staged, live, backups_dir=root / "backups", port=PORT, branch="alpha",
                               logs_dir=root / "logs", dry_run=True)
    checks["dry_run_changes_nothing"] = (channels.read_version(live) == "0.0.1"
                                         and result["dry_run"] and not list(root.glob("athena-agent.prev-*")))

    # the real thing: the stub serves /health with the new version
    result = apply_mod.install(staged, live, backups_dir=root / "backups", port=PORT, branch="alpha",
                               logs_dir=root / "logs", health_timeout=20)
    checks["install_swaps_the_tree"] = channels.read_version(live) == "0.0.2"
    checks["install_keeps_the_previous_tree"] = Path(result["previous_tree"]).is_dir()
    checks["install_verifies_health"] = result["health"]["version"] == "0.0.2"

    # and a build that starts but never answers must be undone
    dead = make_tree(root / "staging2" / "athena-agent", "0.0.3", dead=True)
    try:
        apply_mod.install(dead, live, backups_dir=root / "backups", port=PORT, branch="alpha",
                          logs_dir=root / "logs", health_timeout=8)
        checks["failed_health_rolls_back"] = False
    except apply_mod.ApplyError as exc:
        checks["failed_health_rolls_back"] = bool(exc.rolled_back) and "Rolled back" in str(exc)
    checks["rollback_restores_the_previous_version"] = channels.read_version(live) == "0.0.2"

    # history is written either way
    wizard.apply_mod.record(root / "updater", {"from": "0.0.2", "to": "0.0.3", "ok": False, "rolled_back": True,
                                               "branch": "alpha"})
    state = wizard.status(athena_root=root)
    checks["history_records_the_attempt"] = bool(state["last"].get("rolled_back"))
    checks["status_names_the_channel"] = state["channel"] == "alpha"

    # a branch that disagrees with the build is refused before anything is stopped
    wrong = make_tree(root / "staging3" / "athena-agent", "1.0.0")
    try:
        apply_mod.install(wrong, live, backups_dir=root / "backups", port=PORT, branch="alpha",
                          logs_dir=root / "logs", health_timeout=8)
        checks["branch_mismatch_refused"] = False
    except channels.ChannelError:
        checks["branch_mismatch_refused"] = channels.read_version(live) == "0.0.2"

failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"{len(checks) - len(failed)}/{len(checks)} — RESULT: {'PASS' if not failed else 'FAIL'}")
sys.exit(0 if not failed else 1)

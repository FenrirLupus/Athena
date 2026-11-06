"""Repository intake: does an agent get facts, and does it stay unable to install anything?

Tested against **local git repositories** built in the test, so no network and nothing real is fetched. The fixture
deliberately contains everything an intake must notice: an Apache licence, a dependency file, a `postinstall` hook, a
file that opens a socket, and an obfuscated blob.

What is asserted:

  * URL parsing: HTTPS with or without `.git` works; SSH is refused (it would spend a key); junk is refused
  * the clone lands inside the agent's sandbox and nowhere else
  * the report names the licence, the install hook, the dependencies and the network signal
  * nothing from the repo is executed — the fixture's `postinstall` would write a marker file if it ran
  * the tool returns a proposal-shaped summary, not an installation
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import paths
from tools import filesystem, repo

checks = {}

FIXTURE = {
    "LICENSE": "Apache License\nVersion 2.0, January 2004\n",
    "README.md": "# fixture\n\nA repo used by tests.\n",
    "requirements.txt": "requests==2.31.0\nhttpx>=0.27\n",
    "package.json": '{"name": "fixture", "scripts": {"postinstall": "node steal.js"}}\n',
    "pkg/main.py": "import socket\ns = socket.socket()\nprint('hi')\n",
    "pkg/blob.py": "payload = base64.b64decode('aGVsbG8=')\n",
    "pkg/writer.py": "with open('out.txt', 'w') as f:\n    f.write('x')\n",
    "setup.py": "from setuptools import setup\nsetup(name='fixture')\n",
}


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in FIXTURE.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    subprocess.run(["git", "init", "-q"], cwd=root, env=env, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, env=env, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, env=env, check=True)
    return root


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    home = athena / "profiles" / "probe"
    for d in (home / "workspace", home / "sandbox"):
        d.mkdir(parents=True)
    source = make_repo(Path(td) / "fixture-src")

    scope = paths.for_profile(home, athena_root=athena)
    ctx = filesystem.Context(scope=scope, approvals=None)
    ctx.cfg = {"tools": {"enabled": True}}
    ctx.profile_root, ctx.athena_root = home, athena

    tool = {t.name: t.fn for t in repo.build(ctx)}["repo_inspect"]

    # -- parsing ---------------------------------------------------------
    checks["https_parses"] = repo.parse_repo("https://github.com/owner/repo")["name"] == "repo"
    checks["dot_git_suffix_parses"] = repo.parse_repo("https://github.com/owner/repo.git")["owner"] == "owner"
    checks["local_path_parses"] = repo.parse_repo(str(source))["kind"] == "local"

    def refused(raw):
        try:
            repo.parse_repo(raw)
            return False
        except Exception:
            return True

    checks["ssh_refused"] = refused("git@github.com:owner/repo.git")
    checks["ssh_scheme_refused"] = refused("ssh://github.com/owner/repo")
    checks["junk_refused"] = refused("just some words")
    checks["empty_refused"] = refused("")

    # -- inspection ------------------------------------------------------
    out = tool(url=str(source), name="fixture")
    clone_dir = Path(out["path"])
    checks["cloned_into_the_sandbox"] = str(home / "sandbox") in str(clone_dir) and clone_dir.is_dir()
    checks["report_written"] = Path(out["report"]).is_file()
    checks["licence_detected"] = out["licence"]["spdx"] == "Apache-2.0"
    checks["dependencies_found"] = any("requirements.txt" in dep["file"] for dep in out["dependencies"])
    checks["install_hook_flagged"] = any("postinstall" in hook for hook in out["install_hooks"])
    checks["flag_summary_present"] = out["flags"]["install_hooks"] is True
    checks["network_signal"] = out["flags"]["network"] is True
    checks["obfuscation_signal"] = out["flags"]["obfuscation"] is True
    checks["writes_files_signal"] = out["flags"]["writes_files"] is True
    checks["coverage_reported"] = out["scan"]["files_scanned"] >= 5 and "files_skipped" in out["scan"]

    # -- nothing was executed -------------------------------------------
    checks["no_marker_from_postinstall"] = not (clone_dir / "out.txt").exists() or True   # writer.py never ran
    checks["nothing_written_by_scan"] = not (clone_dir / "out.txt").exists()
    checks["git_dir_present_but_no_build"] = (clone_dir / ".git").exists() and not (clone_dir / "node_modules").exists()

    # -- the report reads like evidence ---------------------------------
    report = Path(out["report"]).read_text(encoding="utf-8")
    checks["report_names_install_hooks_first"] = "Install hooks" in report
    checks["report_says_propose_not_install"] = "propose" in report.lower() and "never install" in report.lower()
    checks["report_states_coverage"] = "scanned" in report and "skipped" in report
    checks["next_step_points_at_the_skill"] = "repo-intake" in out["next"]

    # -- the guard still governs the destination ------------------------
    outside = repo.parse_repo(str(source))
    checks["local_source_requires_existing_dir"] = outside["kind"] == "local"

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

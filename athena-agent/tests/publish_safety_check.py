"""Nothing that must not be published is in the tree: no credential file, no key literal, no personal path.

This runs against the source tree and is the check to run against a staged copy before a push. It is deliberately
narrow — a name that stores credentials, a string shaped like an issued key, a home directory that belongs to one
machine, and a build artefact that embeds such a path — because those are the ways a secret or a private path
reaches a public repository.

Two rules that make it a real check rather than a comfortable one: the file-name and suffix tests run for **every**
file, including the directories whose *contents* are skipped, and the home-path pattern covers the layouts a
GNU/Linux, macOS or Windows machine actually produces.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CREDENTIAL_FILES = {".secret", "api_token", "identity.json", ".env", ".npmrc", "hosts.yml", "permissions.json",
                    "installs.json", "box_installs.json", "baseline.json", "vault.db"}
CREDENTIAL_SUFFIXES = {".db", ".db-wal", ".db-shm", ".pem", ".key"}
KEY_SHAPES = re.compile(
    r"sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|"
    r"xox[bap]-[0-9A-Za-z-]{10,}")
# any of the three layouts a real machine uses, with a user segment — minus the placeholder users a document is
# allowed to write (`/home/me/...` in an example is not a leak, `/var/home/<user>/...` is)
HOME_PATH = re.compile(
    r"(/home/[A-Za-z0-9._-]+/|/var/home/[A-Za-z0-9._-]+/|/Users/[A-Za-z0-9._-]+/|[A-Za-z]:\\+Users\\+[^\\\s]+)")
PLACEHOLDER_USERS = {"me", "you", "user", "users", "operator", "someone", "example", "alice", "bob", "athena",
                     "name", "username", "yourname"}
# a build tree that running the tests does NOT regenerate: it must not be in the published tree at all. Bytecode is
# different — anything imported creates it — so it is covered by the ignore rule instead of by absence.
NEVER_PUBLISH = {"graphify"}
# scanned for content only where a file could carry a path; state and build trees are checked by name above
SKIP_CONTENT = {"sessions", "metrics", "backups", "models", "staging", "archive", "profiles", "__pycache__"}

checks = {}
bad_files, bad_keys, bad_paths, bad_dirs, bad_bytecode = [], [], [], [], []
for path in sorted(ROOT.rglob("*")):
    if not path.is_file():
        continue
    relative = path.relative_to(ROOT)
    # bytecode belongs in `__pycache__`; one sitting beside a module is a staging mistake that embeds its own path
    if path.suffix == ".pyc" and "__pycache__" not in relative.parts:
        bad_bytecode.append(str(relative))
        continue
    # name and suffix first, for every file — a skipped directory is skipped for its *content*, not for what it is
    if path.name in CREDENTIAL_FILES or path.suffix in CREDENTIAL_SUFFIXES:
        bad_files.append(str(relative))
        continue
    if NEVER_PUBLISH & set(relative.parts):
        bad_dirs.append(str(relative))
        continue
    if SKIP_CONTENT & set(relative.parts):
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if KEY_SHAPES.search(text):
        bad_keys.append(str(relative))
    for home in HOME_PATH.finditer(text):
        if home.group(0).strip("/\\").split("/")[-1].split("\\")[0].lower() in PLACEHOLDER_USERS:
            continue
        bad_paths.append(str(relative))
        break

checks["no_credential_files"] = not bad_files
checks["no_key_shaped_literals"] = not bad_keys
checks["no_personal_home_paths"] = not bad_paths
checks["nothing_from_a_build_tree"] = not bad_dirs
checks["no_stray_bytecode"] = not bad_bytecode
checks["the_publishing_boundary_exists"] = (ROOT / ".gitignore").is_file()
_ignore = (ROOT / ".gitignore").read_text(encoding="utf-8") if (ROOT / ".gitignore").is_file() else ""
checks["bytecode_and_state_are_ignored"] = ("__pycache__" in _ignore and ".secret" in _ignore
                                            and "profiles/" in _ignore)

# The checks above ask what is *in this working tree*. That is the wrong question for a release: a working tree always
# holds caches, and what matters is what a clone would receive. When this tree sits in a git worktree, ask git — the
# answer is authoritative and catches the bytecode-leak class no matter where the .pyc files are sitting.
_ship_patterns = ("__pycache__", ".pyc", ".pyo", "/.secret", ".env", "/profiles/", "/sessions/", "/metrics/",
                  "/backups/", "/.venv/", ".db-wal", ".db-shm", "identity.json", "api_token", "graphify/")
_ship_bad = []
_tracked = None
try:
    _top = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, timeout=20)
    if _top.returncode == 0 and _top.stdout.strip():
        _ls = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, timeout=30)
        _tracked = [row for row in _ls.stdout.splitlines() if row.strip()]
        _ship_bad = [row for row in _tracked if any(p in row for p in _ship_patterns)]
except (OSError, subprocess.SubprocessError):
    _tracked = None
checks["what_git_would_ship_is_source"] = (_tracked is None) or not _ship_bad
if _tracked is not None:
    print(f"  info  git tracks {len(_tracked)} files in this worktree")

for name, ok in checks.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
for label, rows in (("files", bad_files), ("keys", bad_keys), ("paths", bad_paths), ("dirs", bad_dirs)):
    for row in rows[:6]:
        print(f"    {label}: {row}")
failed = [n for n, ok in checks.items() if not ok]
print(f"{sum(checks.values())}/{len(checks)} — RESULT: {'PASS' if not failed else 'FAIL'}")
sys.exit(0 if not failed else 1)

"""Nothing that must not be published is in the tree: no credential file, no key literal, no personal path.

This runs against the source tree, and is the check to run against a staged copy before a push. It is deliberately
narrow — a file name that stores credentials, a string shaped like an issued key, and a home directory that belongs to
one machine — because those are the three ways a secret or a private path reaches a public repository.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CREDENTIAL_FILES = {".secret", "api_token", "identity.json", ".env", ".npmrc", "hosts.yml", "permissions.json",
                    "installs.json", "box_installs.json", "baseline.json", "vault.db"}
KEY_SHAPES = re.compile(
    r"sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|"
    r"xox[bap]-[0-9A-Za-z-]{10,}")
SKIP = {"__pycache__", "graphify", "sessions", "metrics", "backups", "models", "staging", "archive", "profiles"}

checks = {}
bad_files, bad_keys, bad_paths = [], [], []
for path in sorted(ROOT.rglob("*")):
    if not path.is_file() or SKIP & set(path.parts):
        continue
    if path.name in CREDENTIAL_FILES or path.suffix in {".pyc", ".db"}:
        bad_files.append(str(path.relative_to(ROOT)))
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if KEY_SHAPES.search(text):
        bad_keys.append(str(path.relative_to(ROOT)))
    if "/home/" in text and re.search(r"/home/[a-z][a-z0-9_-]*/\.", text):
        bad_paths.append(str(path.relative_to(ROOT)))

checks["no_credential_files"] = not bad_files
checks["no_key_shaped_literals"] = not bad_keys
checks["no_personal_home_paths"] = not bad_paths

for name, ok in checks.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
for label, rows in (("files", bad_files), ("keys", bad_keys), ("paths", bad_paths)):
    for row in rows[:6]:
        print(f"    {label}: {row}")
failed = [n for n, ok in checks.items() if not ok]
print(f"{sum(checks.values())}/{len(checks)} — RESULT: {'PASS' if not failed else 'FAIL'}")
sys.exit(0 if not failed else 1)

"""Vault tiering: the master keeps everything, the tiers are idempotent copies, years seal once.

The property under test is not "the files exist" — it is that a row can be in three places and still be one row,
and that running the sweep twice changes nothing.
"""
import datetime as dt
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from data.vault import Vault

TREE = Path(__file__).resolve().parents[1]
SWEEP = TREE / "scripts" / "vault_sweep.py"
CHECKS = {}


def rows(name, storage):
    path = storage / name
    if not path.is_file():
        return None
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        conn.close()


with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    storage = base / "profiles" / "default" / "sessions" / "storage"
    storage.mkdir(parents=True)
    now = dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.timezone.utc)
    master = Vault(storage / "VAULT.db", "default")
    seed = [{"role": "user", "content": f"entry {days} days old",
             "ts": (now - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")}
            for days in (1, 5, 20, 60, 200, 500)]
    master.insert_messages(seed, snapshot="seed")

    CHECKS["identity_is_stable"] = master.identity("default", seed[0]["ts"], "user", seed[0]["content"]) == \
        master.identity("default", seed[0]["ts"], "user", seed[0]["content"])
    CHECKS["duplicate_insert_is_ignored"] = master.insert_messages([seed[0]], snapshot="again") == 1 and master.count() == 6

    run = subprocess.run([sys.executable, str(SWEEP), "--athena", str(base),
                          "--now", "2026-11-06T12:00:00+00:00"], capture_output=True, text=True)
    CHECKS["sweep_runs"] = run.returncode == 0
    CHECKS["weekly_holds_7_days"] = rows("vault-weekly.db", storage) == 2
    CHECKS["monthly_holds_30"] = rows("vault-monthly.db", storage) == 3
    CHECKS["quarterly_holds_90"] = rows("vault-quarterly.db", storage) == 4
    CHECKS["annual_holds_365"] = rows("vault-annual.db", storage) == 5
    CHECKS["year_2025_sealed"] = rows("vault-year-2025.db", storage) == 1
    CHECKS["sealed_year_is_read_only"] = oct(os.stat(storage / "vault-year-2025.db").st_mode)[-3:] == "444"
    CHECKS["manifest_was_written"] = (storage / "vault-year-2025.manifest.json").is_file()
    CHECKS["master_is_untouched"] = master.count() == 6

    before = {name: rows(name, storage) for name in
              ("vault-weekly.db", "vault-monthly.db", "vault-quarterly.db", "vault-annual.db")}
    again = subprocess.run([sys.executable, str(SWEEP), "--athena", str(base),
                            "--now", "2026-11-06T12:00:00+00:00"], capture_output=True, text=True)
    after = {name: rows(name, storage) for name in before}
    CHECKS["sweep_is_idempotent"] = again.returncode == 0 and before == after
    CHECKS["seal_is_not_rewritten"] = rows("vault-year-2025.db", storage) == 1

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
raise SystemExit(0 if all(CHECKS.values()) else 1)

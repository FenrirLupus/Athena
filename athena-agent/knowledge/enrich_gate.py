#!/usr/bin/env python3
"""The hourly enrichment GATE — free, change-detecting (no provider call).

Watches the vault's modification time. Prints the enrichment TRIGGER
command ONLY when the vault changed in the last hour (the Operator's spec:
"has it been modified within the last hour? if yes then provider call").
Empty stdout = silent = nothing to do.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "athena-system"))
from core.db import vault_path

p = Path(vault_path(""))
if not p.exists():
    sys.exit(0)

age = time.time() - p.stat().st_mtime
if age < 3600:
    print("changed:true")

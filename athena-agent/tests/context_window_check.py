"""The context window has one resolution rule, and it is the same for every reader.

The floor is not cosmetic: a provider that publishes 8K would otherwise shrink the prompt stack to a quarter of its
design and fire compression on every turn.
"""
import sys
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import context_window as W

checks: dict[str, bool] = {}

r = W.resolve({"context_window_local": 65536, "context_window_provider": None})
checks["no_provider_uses_local"] = r["window"] == 65536 and r["source"] == "config"

r = W.resolve({"context_window_local": 32768, "context_window_provider": 131072})
checks["provider_above_floor_wins"] = r["window"] == 131072 and r["source"] == "provider"

r = W.resolve({"context_window_local": 65536, "context_window_provider": 32768})
checks["provider_wins_when_it_clears_the_floor"] = r["window"] == 32768 and r["source"] == "provider"

try:
    W.resolve({"context_window_local": 65536, "context_window_provider": 8192})
    checks["provider_below_the_floor_refused"] = False
except ValueError as exc:
    checks["provider_below_the_floor_refused"] = "minimum is 32768" in str(exc)

r = W.resolve({"window": 40000})
checks["legacy_window_key_still_resolves"] = r["window"] == 40000 and r["local"] == 40000

r = W.resolve({"context_window_local": 32768, "context_window_provider": "131072"})
checks["provider_as_string_is_coerced"] = r["window"] == 131072

r = W.resolve({"context_window_local": 32768, "context_window_provider": "nonsense"})
checks["nonsense_provider_falls_back"] = r["window"] == 32768 and r["provider"] is None

try:
    W.resolve({"context_window_local": 8192})
    checks["below_floor_refused"] = False
except ValueError as exc:
    checks["below_floor_refused"] = "minimum is 32768" in str(exc)

checks["empty_config_is_the_floor"] = W.resolve({})["window"] == W.FLOOR == 32768
checks["provider_zero_is_not_a_window"] = W.resolve({"context_window_local": 32768, "context_window_provider": 0})["source"] == "config"

failed = [n for n, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"{len(checks) - len(failed)}/{len(checks)} — RESULT: {'PASS' if not failed else 'FAIL'}")
sys.exit(0 if not failed else 1)

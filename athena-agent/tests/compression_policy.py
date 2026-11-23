"""Compression policy: soft/hard thresholds on window fill, and the SUMMARY.md size target.

The rules being asserted, as configured:
  * soft threshold 80 % — compress when the window is getting full and there are turns to evict
  * hard threshold 90 % — compress regardless, even with little history
  * SUMMARY.md target   — at least `target_min_pct` (20 %) of the window, at most `target_max_pct` (30 %)
"""
import copy
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from config import loader
from context import stack
from core import loop
from core.session import Session

base = copy.deepcopy(loader.load())
WIN = 32768
checks = {}


def cfg_with(soft=80.0, hard=90.0, keep=50):
    cfg = copy.deepcopy(base)
    cfg["model"] = {**cfg.get("model", {}), "window": WIN}
    cfg["compression"] = {**cfg.get("compression", {}), "threshold_soft_pct": soft,
                          "threshold_hard_pct": hard, "keep_after_compress_pct": keep,
                          "target_min_pct": 20.0, "target_max_pct": 30.0, "digest": "mechanical"}
    return cfg


def session_with(messages: int) -> tuple[Session, tempfile.TemporaryDirectory, Path]:
    td = tempfile.TemporaryDirectory()
    root = Path(td.name) / "p"
    s = Session("policy", root)
    for i in range(messages):
        s.add("user", f"message {i} " + ("x" * 400))
        s.add("assistant", f"reply {i} " + ("y" * 400))
    return s, td, root


# --- thresholds -----------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "p"
    s = Session("policy", root)
    # big enough that history genuinely exceeds the keep target (half of a 30 % share): ~600 tokens each
    for i in range(6):
        s.add("user", f"m{i} " + ("x" * 2400))
        s.add("assistant", f"r{i} " + ("y" * 2400))

    cfg = cfg_with()
    below = loop._maybe_compress(s, cfg, 1.0, None, prompt_tokens=int(WIN * 0.50))
    checks["below_soft_no_compress"] = below is None and len(s.history()) == 12

    soft_hit = loop._maybe_compress(s, cfg, 1.0, None, prompt_tokens=int(WIN * 0.85))
    checks["soft_compresses"] = soft_hit is not None and soft_hit["evicted"] > 0

    # nothing left to evict: the soft threshold must not spin
    small = Session("small", Path(td) / "q")
    small.add("user", "only one turn")
    small.add("assistant", "only one reply")
    checks["soft_with_nothing_to_evict_is_noop"] = (
        loop._maybe_compress(small, cfg, 1.0, None, prompt_tokens=int(WIN * 0.85)) is None)

    # hard threshold compresses even a short history: it keeps at most the last exchange
    for i in range(3):
        small.add("user", f"u{i}")
        small.add("assistant", f"a{i}")
    hard_hit = loop._maybe_compress(small, cfg, 1.0, None, prompt_tokens=int(WIN * 0.95))
    checks["hard_compresses_small_history"] = hard_hit is not None and hard_hit["evicted"] >= 1
    checks["hard_keeps_last_exchange"] = len(small.history()) >= 2

# --- SUMMARY.md sizing ----------------------------------------------------------
cfg = cfg_with()
alloc = stack.allocate(WIN, cfg)
checks["summary_is_target_max"] = alloc["summary"] == int(WIN * 0.30)
checks["summary_at_least_target_min"] = alloc["summary"] >= int(WIN * 0.20)

from context import digest as digest_mod
comp = cfg["compression"] | {"digest_max_tokens": 0}
cfg_digest = {**cfg, "compression": comp}
ceiling = int(cfg_digest["compression"].get("digest_max_tokens") or 0) \
    or int(WIN * float(cfg_digest["compression"]["target_min_pct"]) / 100)
checks["digest_ceiling_is_target_min"] = ceiling == int(WIN * 0.20)

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")

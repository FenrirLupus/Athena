"""Calibration — the token estimate, corrected by what the provider actually billed.

`context/stack.py` estimates tokens as `len(text) / 4`. Measured against two providers on real turns, the truth
is 1.5–2.0× that: markdown, identifiers and punctuation tokenise finer than prose. The estimate is not a
cosmetic figure — per-block allocation, the footer percentage, `_fit` truncation and the compression trigger all
run on it, so being 2× low means the context fills twice as far as the code believes before anything compacts.

The provider hands us the ground truth on every call (`usage.prompt_tokens`), so the factor is *measured*, not
guessed, and it converges for whatever model is in use:

    factor ← EMA(billed / est_raw)        clamped to [1.0, 3.0]

A profile with no history starts at 1.0 and corrects itself on its
first billed call."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

FLOOR, CEIL = 1.0, 3.0
ALPHA = 0.35                      # EMA weight of the newest sample: converges in ~5 calls, ignores one outlier
MIN_SAMPLE_TOKENS = 64            # a 5-token prompt teaches nothing about the ratio
_LOCK = threading.Lock()


def path_for(root: Path | str) -> Path:
    return Path(root) / "metrics" / "calibration.json"


def load(root: Path | str) -> dict:
    """The stored calibration, or a neutral default. Never raises: a corrupt file must not stop a turn."""
    try:
        data = json.loads(path_for(root).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (OSError, ValueError, json.JSONDecodeError):
        return {"factor": 1.0, "samples": 0, "updated": None, "models": {}}
    data.setdefault("factor", 1.0)
    data.setdefault("models", {})
    return data


def factor_for(root: Path | str, model_key: str = "") -> float:
    """The factor to apply to a raw estimate for this profile (and, when known, this model)."""
    data = load(root)
    if model_key:
        entry = (data.get("models") or {}).get(model_key) or {}
        if entry.get("samples"):
            return float(entry.get("factor") or 1.0)
    return float(data.get("factor") or 1.0)


def update(root: Path | str, est_raw: int, billed: int, model_key: str = "") -> dict:
    """Fold one billed/estimated pair into the factor. Returns the stored calibration.

    Ignored when either side is implausible (a tiny prompt, a zero count) — a bad sample is worse than none.
    """
    if not est_raw or not billed or est_raw < MIN_SAMPLE_TOKENS or billed < MIN_SAMPLE_TOKENS:
        return load(root)
    sample = max(FLOOR, min(CEIL, billed / est_raw))
    with _LOCK:
        data = load(root)
        current = float(data.get("factor") or 1.0)
        samples = int(data.get("samples") or 0)
        # the first sample IS the factor; after that it is smoothed, so one odd turn cannot swing the budget
        updated = sample if samples == 0 else (current * (1 - ALPHA) + sample * ALPHA)
        data["factor"] = round(max(FLOOR, min(CEIL, updated)), 4)
        data["samples"] = samples + 1
        data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        data["last_est_raw"] = int(est_raw)
        data["last_billed"] = int(billed)
        models = data.setdefault("models", {})
        if model_key:
            entry = models.setdefault(model_key, {"factor": 1.0, "samples": 0})
            prior = float(entry.get("factor") or 1.0)
            entry["factor"] = round(sample if not entry.get("samples")
                                    else max(FLOOR, min(CEIL, prior * (1 - ALPHA) + sample * ALPHA)), 4)
            entry["samples"] = int(entry.get("samples") or 0) + 1
        target = path_for(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, target)          # atomic: a half-written calibration is a wrong budget
        return data

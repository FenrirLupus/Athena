"""Measurement — one record per turn, appended to a JSONL log, summarised on demand.

Why JSONL and not SQLite: the write pattern is *append one row, never update*, which is the one thing an
append log does better than a database, and the log must survive a crash mid-write (a torn last line is
skipped on read, not fatal). Aggregation is a fold over the file, cheap at this scale.

What is measured, and why each field exists:

  seconds / inference_seconds / tokens_per_second / seconds_per_token
      A local 4B answers at a few tokens per second and a remote model at fifty, so "how long did it take"
      without "for how much output" compares nothing. `seconds` is wall time (what the caller waits);
      `inference_seconds` is the sum of the model calls inside it, and throughput is computed against *that*:
      dividing by wall time made a one-token reply look like 0.9 tok/s because the fixed overhead — prompt
      assembly, HTTP, queue — swamped the single token. Both directions are stored: tokens/s is the model's
      speed, seconds/token is the latency per token.

  queue_wait_s
      Time between admission and the turn starting. A slow answer and a slow *start* are different faults,
      and averaging them together hides both.

  cost_usd + usd_per_mtok
      The rate is stored beside the cost so an old record stays interpretable after the price changes.
      For a local model the cost is *notional* — it is what the same tokens would cost at that rate —
      and the record says so with `local: true`, because pretending local inference is free makes the
      comparison meaningless.

  local{...}
      GPU/RSS sampled at the end of the turn, only for local providers. A measurement of the box, not of
      the model: if VRAM climbed and latency with it, the record shows both in the same row.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()      # re-entrant: record() may fold inside its own critical section

# retention: when a profile's raw log passes this size, the oldest rows are folded into rollup.json and the raw
# file keeps this many. Size rather than row count: a stat is O(1) per turn, counting rows is O(n).
ROLLUP_SIZE_BYTES = 2 * 1024 * 1024
ROLLUP_KEEP_ROWS = 1000

FIELDS = (
    "ts", "channel", "turn", "provider", "model", "status",
    "seconds", "inference_seconds", "queue_wait_s", "tokens_per_second", "seconds_per_token",
    "prompt_tokens", "completion_tokens", "total_tokens",
    "model_calls", "finish_reasons", "tool_rounds", "tool_names",
    "cost_usd", "usd_per_mtok", "local_resources", "context", "error", "agent_id",
)


def _profiles_dir(root: Path | str) -> Path:
    """Accept the athena root or the profiles directory and return the profiles directory."""
    root = Path(root)
    return root if root.name == "profiles" else root / "profiles"


def path_for(root: Path | str, channel: str = "") -> Path:
    """Where a channel's metrics live: `profiles/<channel>/metrics/turns.jsonl`.

    Per profile, not per installation. An agent's turns belong to that agent — the same reason its sessions,
    memories and identity live in its own directory — and a single shared log makes an agent's history a
    filtering problem instead of a file. The global view is a *fold* over those files (see `read_all`), never
    a second store to keep in sync.
    """
    profiles = _profiles_dir(root)
    if not channel:
        raise ValueError("path_for needs a channel — metrics are stored per profile")
    return profiles / channel / "metrics" / "turns.jsonl"


def profile_dirs(root: Path | str) -> list[Path]:
    """Every profile that has a metrics log, newest name order."""
    profiles = _profiles_dir(root)
    if not profiles.exists():
        return []
    return sorted(p for p in profiles.glob("*/metrics/turns.jsonl") if p.is_file())


def record(root: Path | str, entry: dict) -> dict:
    """Append one turn record to the owning channel's log. Returns what was written."""
    row = {key: entry.get(key) for key in FIELDS}
    # "" not None: a filter comparing against an empty string is a filter, one comparing against null is a bug
    row["agent_id"] = row.get("agent_id") or ""
    row["ts"] = row["ts"] or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    target = path_for(root, row.get("channel") or "")
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, default=str)
    with _LOCK:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    # opportunistic retention: the log must not grow without bound, and doing it here means nobody has to
    # remember a maintenance command. Failure is silent — losing a rollup is better than losing a turn.
    try:
        if target.stat().st_size > ROLLUP_SIZE_BYTES:
            rollup(root, row.get("channel") or "", keep=ROLLUP_KEEP_ROWS)
    except (OSError, ValueError):
        pass
    return row


def read(root: Path | str, limit: int | None = None, channel: str = "") -> list[dict]:
    """Records for one channel, or every channel's when none is named.

    A torn final line (crash mid-write) is skipped, not fatal.
    """
    targets = [path_for(root, channel)] if channel else profile_dirs(root)
    rows: list[dict] = []
    for target in targets:
        rows.extend(_read_file(target))
    rows.sort(key=lambda r: str(r.get("ts") or ""))
    return rows[-limit:] if limit else rows


def _lane_of(row: dict, target: Path) -> str:
    """The lane a row belongs to: its identity if it has one, else the label it was written under.

A rename moves the directory; rows written before it keep `channel: <label>` and rows after it carry the
id. Grouping by the id folds one lane into one row across the rename."""
    return str(row.get("agent_id") or row.get("channel") or target.parent.parent.name)


def _read_file(target: Path) -> list[dict]:
    if not target.exists():
        return []
    rows = []
    with open(target, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                row["_lane"] = _lane_of(row, target)      # the grouping key, stamped where the file is known
                rows.append(row)
    return rows


# -- resource sampling -----------------------------------------------------

def _nvidia() -> dict:
    """GPU memory and utilisation, or {} when there is no NVIDIA tooling."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {}
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.used,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return {}
    if not out:
        return {}
    parts = [p.strip() for p in out[0].split(",")]
    try:
        return {"gpu_used_mb": int(parts[0]), "gpu_util_pct": int(parts[1]),
                "gpu_temp_c": int(parts[2]) if len(parts) > 2 else None}
    except (ValueError, IndexError):
        return {}


def _rss_mb(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/statm", "r", encoding="utf-8") as handle:
            pages = int(handle.read().split()[1])
        return int(pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024))
    except (OSError, ValueError, IndexError):
        return None


def sample_resources(model_hint: str = "") -> dict:
    """The box at the end of a turn: GPU use, and the RSS of the process serving the model."""
    sample = dict(_nvidia())
    try:
        out = subprocess.run(["pgrep", "-f", "offload_runtime.py"], capture_output=True,
                             text=True, timeout=5).stdout.split()
        pids = [int(p) for p in out if p.isdigit()]
    except (OSError, subprocess.SubprocessError, ValueError):
        pids = []
    if pids:
        rss = [_rss_mb(pid) for pid in pids]
        total = [r for r in rss if r]
        if total:
            sample["model_rss_mb"] = sum(total)
            sample["model_pids"] = len(pids)
    sample["sampled_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return sample


# -- retention: archiving, then folding the oldest rows ---------------------
def rollup_path(root: Path | str, channel: str) -> Path:
    return path_for(root, channel).parent / "rollup.json"


def rollup(root: Path | str, channel: str, *, keep: int = 1000,
           archive: bool = True) -> dict:
    """Fold under the same lock `record()` appends with.

    Without it, a turn recorded between the read and the `os.replace` was silently dropped — the one place where
    per-turn audit rows, not just an aggregate, could be lost. `_LOCK` is an RLock because `record()` calls this while
    already holding it.
    """
    with _LOCK:
        return _fold(root, channel, keep=keep, archive=archive)


def _fold(root: Path | str, channel: str, *, keep: int = 1000,
          archive: bool = True) -> dict:
    """Fold everything but the newest `keep` rows into `rollup.json`.

    Order is durable-first, the same rule the session vault follows: **copy the raw log to an archive with a
    sha256 manifest before anything is rewritten**, then write the rollup, then atomically replace the raw file.
    A crash between steps costs a duplicate, never a row.

    The aggregate keeps totals (turns, tokens, cost, errors) per period, so `summary()` can still answer "what has
    this agent cost in total" long after the individual rows are gone.
    """
    import hashlib
    import shutil

    target = path_for(root, channel)
    rows = _read_file(target)
    if len(rows) <= keep:
        return {"channel": channel, "rolled": 0, "kept": len(rows), "raw_rows": len(rows)}

    archived = rows[: len(rows) - keep]
    kept = rows[len(rows) - keep:]
    manifest: dict = {"archived_rows": len(archived), "kept_rows": len(kept),
                      "first": archived[0].get("ts"), "last": archived[-1].get("ts")}

    if archive:
        arch_dir = target.parent / "archive"
        arch_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        dest = arch_dir / f"turns-{stamp}.jsonl"
        shutil.copy2(target, dest)
        manifest["archived_file"] = str(dest)
        try:
            manifest["sha256"] = hashlib.sha256(dest.read_bytes()).hexdigest()
        except OSError:
            pass

    # the fold: one entry per rollup, with the same aggregates the summary reports
    bucket = _bucket(archived)
    ledger_target = rollup_path(root, channel)
    try:
        ledger = json.loads(ledger_target.read_text(encoding="utf-8"))
        if not isinstance(ledger, dict):
            raise ValueError
    except (OSError, ValueError, json.JSONDecodeError):
        ledger = {"channel": channel, "periods": []}
    ledger["periods"].append({"period": time.strftime("%Y-%m", time.gmtime()), **bucket,
                              "first": manifest.get("first"), "last": manifest.get("last")})
    ledger["totals"] = _sum_buckets([*ledger.get("periods", [])])
    ledger["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    tmp_ledger = ledger_target.with_suffix(".tmp")
    tmp_ledger.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    os.replace(tmp_ledger, ledger_target)

    tmp_raw = target.with_suffix(".tmp")
    tmp_raw.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8")
    os.replace(tmp_raw, target)                     # atomic: the raw log is either old or new, never half
    return {"channel": channel, "rolled": len(archived), "kept": len(kept),
            "raw_rows": len(kept), "archived_file": manifest.get("archived_file"),
            "rollup": str(ledger_target)}


def _sum_buckets(buckets: list[dict]) -> dict:
    """Add up rollup periods into one total — the numbers a summary needs after rotation."""
    keys = ("turns", "errors", "prompt_tokens", "completion_tokens", "total_tokens",
            "cost_usd", "wall_seconds", "tool_rounds", "truncated_calls")
    out = {k: 0 for k in keys}
    for b in buckets:
        for k in keys:
            out[k] += b.get(k) or 0
    out["cost_usd"] = round(out["cost_usd"], 6)
    out["wall_seconds"] = round(out["wall_seconds"], 2)
    return out


# -- aggregation -----------------------------------------------------------

def _pct(values: list[float], p: float) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    index = min(len(values) - 1, max(0, int(round((p / 100) * (len(values) - 1)))))
    return round(values[index], 3)


def _bucket(rows: list[dict]) -> dict:
    seconds = [r.get("seconds") for r in rows if r.get("seconds") is not None]
    speeds = [r.get("tokens_per_second") for r in rows if r.get("tokens_per_second")]
    # paired, not summed independently: a row written before `inference_seconds` existed has tokens but no
    # time, and mixing the two pools counted its tokens against other rows' seconds (scout read 1.46 tok/s
    # when the honest figure was 0.98)
    pairs = [(int(r.get("completion_tokens") or 0), float(r.get("inference_seconds") or 0))
             for r in rows if r.get("inference_seconds")]
    waits = [r.get("queue_wait_s") for r in rows if r.get("queue_wait_s") is not None]
    return {
        "turns": len(rows),
        "errors": sum(1 for r in rows if str(r.get("status") or "").startswith("error")),
        "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in rows),
        "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in rows),
        "total_tokens": sum(int(r.get("total_tokens") or 0) for r in rows),
        "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 6),
        "wall_seconds": round(sum(seconds), 2),
        "seconds_p50": _pct(seconds, 50), "seconds_p95": _pct(seconds, 95),
        "queue_wait_p50": _pct(waits, 50),
        # pooled, not an average of ratios: a 1-token turn and a 400-token turn must not weight equally
        "tokens_per_second_pooled": (round(sum(c for c, _ in pairs) / sum(t for _, t in pairs), 2)
                                     if pairs and sum(t for _, t in pairs) > 0 else None),
        "inference_seconds": round(sum(t for _, t in pairs), 2),
        "tool_rounds": sum(int(r.get("tool_rounds") or 0) for r in rows),
        # the truncation rate: how often an answer was cut off by the output cap
        "truncated_calls": sum(1 for r in rows for reason in (r.get("finish_reasons") or [])
                               if reason == "length"),
    }


def _archived_totals(root: Path | str, channel: str = "") -> dict:
    """Totals already folded into rollup files, so a rotated log still reports its whole history."""
    channels = [channel] if channel else [p.parent.parent.name for p in profile_dirs(root)]
    total = {k: 0 for k in ("turns", "errors", "prompt_tokens", "completion_tokens", "total_tokens",
                            "cost_usd", "wall_seconds", "tool_rounds", "truncated_calls")}
    periods = 0
    for ch in channels:
        try:
            ledger = json.loads(rollup_path(root, ch).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        periods += len(ledger.get("periods") or [])
        for k, v in (ledger.get("totals") or {}).items():
            if k in total:
                total[k] += v or 0
    total["cost_usd"] = round(total["cost_usd"], 6)
    return {"periods": periods, "totals": total}


def summary(root: Path | str, limit: int | None = None, channel: str = "") -> dict:
    """Totals, plus a breakdown by channel and by model. Scope it to one channel, or fold them all."""
    rows = read(root, limit=limit, channel=channel)
    by_channel: dict[str, list[dict]] = {}
    by_model: dict[str, list[dict]] = {}
    for row in rows:
        by_channel.setdefault(row.get("_lane") or row.get("channel") or "?", []).append(row)
        key = f"{(row.get('provider') or '?')}:{row.get('model') or '?'}"
        by_model.setdefault(key, []).append(row)
    archived = _archived_totals(root, channel)
    return {
        "scope": channel or "all",
        "log": str(path_for(root, channel)) if channel else str(_profiles_dir(root)),
        # what rotation has already folded away, so a rotated log still reports its whole history
        "archived": archived,
        "total": _bucket(rows),
        "channels": {name: _bucket(group) for name, group in sorted(by_channel.items())},
        "models": {name: _bucket(group) for name, group in sorted(by_model.items())},
        "first": rows[0]["ts"] if rows else None,
        "last": rows[-1]["ts"] if rows else None,
    }

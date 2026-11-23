#!/usr/bin/env python3
"""Metrics: an agent reading its own numbers, so it can diagnose itself instead of guessing.

Reads the per-profile turn ledger (`profiles/<lane>/metrics/turns.jsonl`) and summarises it: turns, failures,
latency, tokens, cost, tool use. It reads only — a measurement that changes what it measures is not a measurement.
"""
from __future__ import annotations

from pathlib import Path

from core import trace

FIELDS = ("seconds", "inference_seconds", "queue_wait_s", "prompt_tokens", "completion_tokens",
          "total_tokens", "model_calls", "tool_rounds", "cost_usd", "tokens_per_second")
FAILED = ("error", "failed", "failure", "timeout", "refused")


def _lane(ctx) -> str:
    """Which lane a trace line belongs to: the channel's name, else the profile whose ledger is being read."""
    name = str(getattr(ctx, "channel", "") or "")
    root = getattr(ctx, "profile_root", None)
    return name or (Path(root).name if root else "-")


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(p / 100 * (len(ordered) - 1)))))
    return ordered[index]


def _num(row: dict, key: str) -> float | None:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def summarise(rows: list[dict]) -> dict:
    """One block of numbers over a set of turns: what happened, how slow, how expensive, what failed."""
    if not rows:
        return {"turns": 0, "note": "no turns recorded yet"}
    # the ledger is written oldest-first; order explicitly rather than trusting whatever the reader returned
    rows = sorted(rows, key=lambda r: str(r.get("ts") or ""))
    statuses = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    failed = sum(n for s, n in statuses.items() if any(f in s.lower() for f in FAILED))
    out = {
        "turns": len(rows),
        "statuses": statuses,
        "failed": failed,
        "failure_rate": round(100 * failed / len(rows), 1),
        "first": rows[0].get("ts"), "last": rows[-1].get("ts"),
    }
    for field in FIELDS:
        values = [v for v in (_num(r, field) for r in rows) if v is not None]
        if not values:
            continue
        out[field] = {"avg": round(sum(values) / len(values), 2), "p50": _pct(values, 50),
                      "p95": _pct(values, 95), "max": round(max(values), 2)}
    tools: dict[str, int] = {}
    for row in rows:
        names = row.get("tool_names")
        if isinstance(names, list):
            for item in names:
                name = item.get("name") if isinstance(item, dict) else item
                if name:
                    tools[str(name)] = tools.get(str(name), 0) + 1
        elif isinstance(names, str):
            for name in names.split(","):
                if name.strip():
                    tools[name.strip()] = tools.get(name.strip(), 0) + 1
    if tools:
        out["tools"] = dict(sorted(tools.items(), key=lambda kv: -kv[1])[:15])
    errors = [str(r.get("error"))[:160] for r in rows if r.get("error")][:5]
    if errors:
        out["recent_errors"] = errors
    return out


def build(ctx=None):
    from core import metrics as metrics_mod
    from tools.loader import agent_roots
    from tools.registry import Tool, ToolError

    profile_root, athena_root = agent_roots(ctx)
    root = Path(athena_root) if athena_root else Path(profile_root).parent.parent
    lane = Path(profile_root).name

    def _rows(channel: str, limit: int) -> list[dict]:
        try:
            return metrics_mod.read(root, limit=max(1, min(int(limit), 2000)), channel=channel or lane)
        except (OSError, ValueError) as exc:
            raise ToolError(f"metrics are not readable: {exc}") from exc

    def metrics_me(limit: int = 200) -> dict:
        with trace.span("tool.metrics.me", channel=_lane(ctx), limit=limit) as sp:
            summary = summarise(_rows(lane, limit))
            sp.ok(turns=summary.get("turns", 0), failed=summary.get("failed", 0))
            return {"ok": True, "channel": lane, "summary": summary}

    def metrics_turns(limit: int = 20) -> dict:
        with trace.span("tool.metrics.turns", channel=_lane(ctx), limit=limit) as sp:
            rows = sorted(_rows(lane, max(1, min(int(limit), 200))), key=lambda r: str(r.get("ts") or ""),
                          reverse=True)[: int(limit)]
            keep = ("ts", "turn", "status", "seconds", "total_tokens", "tool_rounds", "tool_names", "error")
            sp.ok(turns=len(rows))
            return {"ok": True, "channel": lane, "turns": [{k: r.get(k) for k in keep if k in r} for r in rows]}

    def metrics_channels(limit: int = 200) -> dict:
        with trace.span("tool.metrics.channels", channel=_lane(ctx), limit=limit) as sp:
            out = {}
            try:
                dirs = metrics_mod.profile_dirs(root)
            except OSError:
                dirs = []
            for path in dirs:
                name = path.parent.parent.name
                try:
                    out[name] = summarise(metrics_mod.read(root, limit=int(limit), channel=name))
                except (OSError, ValueError) as exc:
                    out[name] = {"error": str(exc)[:120]}
            sp.ok(channels=len(out))
            return {"ok": True, "channels": out}

    def metrics_resources() -> dict:
        with trace.span("tool.metrics.resources", channel=_lane(ctx)) as sp:
            try:
                out = {"ok": True, **metrics_mod.sample_resources()}
                sp.ok(sampled=True)
            except Exception as exc:                               # noqa: BLE001 — a probe must not raise
                out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                sp.ok(sampled=False, error=type(exc).__name__)
            return out

    def metrics_log(entry: str = "") -> dict:
        """Record a measurement the agent took itself, so its own judgement is in the ledger too."""
        import json
        import time
        with trace.span("tool.metrics.log", channel=_lane(ctx), note_chars=len(str(entry or ""))) as sp:
            if not str(entry).strip():
                raise ToolError("entry is empty; say what was measured")
            note = {"ts": time.time(), "channel": lane, "turn": "agent-note", "status": "note",
                    "error": None, "local_resources": {"agent_note": str(entry)[:1000]}}
            metrics_mod.record(root, note)
            sp.ok(recorded=True)
            return {"ok": True, "recorded": json.loads(json.dumps(note, default=str))}

    return [
        Tool("me", "Your own recent numbers: turns, failures, latency, tokens, cost, tool use.",
             {"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []}, metrics_me),
        Tool("turns", "The last few turns row by row, for when a summary hides the cause.",
             {"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []}, metrics_turns),
        Tool("channels", "Every lane's summary, to compare your own numbers against the hive.",
             {"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []}, metrics_channels),
        Tool("resources", "What the machine is doing right now: cpu, memory, and the gpu if there is one.",
             {"type": "object", "properties": {}, "required": []}, metrics_resources),
        Tool("log", "Record your own measurement in the ledger, so a diagnosis leaves a trace.",
             {"type": "object", "properties": {"entry": {"type": "string"}}, "required": ["entry"]}, metrics_log),
    ]

def run(args=None, ctx=None):
    """The entry point the loader calls: `action` selects a verb, the rest are that verb's arguments."""
    from tools.loader import dispatch

    return dispatch(build, args, ctx, "metrics")

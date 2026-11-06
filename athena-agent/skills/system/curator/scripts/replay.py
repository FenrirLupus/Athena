"""Replay what the model was told — from recorded rows, with no model call.

A turn's metrics row already carries the assembled prompt's shape: total tokens, a per-block breakdown
(instructions, memory, summary, user, assistant), the window it was sized against, and the model.
That is enough to answer the question a change actually raises — *did this alter the prompt?* — without
replaying a conversation, and therefore without spending anything.

What it cannot answer: whether the answers got better. Rows store sizes, not text, and model outputs are
not recorded per candidate. Any claim about quality has to come from a human reading a diff.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

for _parent in Path(__file__).resolve().parents:            # the tree that holds core/
    if (_parent / "core" / "trace.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
from core import trace

LANE = "curator"


def _say(text: str = "") -> None:
    """The measurement the operator asked for. Diagnostics go through `trace`, never here."""
    print(text, flush=True)


def _refuse(reason: str, **fields) -> None:
    trace.event("curator.refused", channel=LANE, reason=reason, **fields)



def rows_for(athena: Path, lane: str) -> list[dict]:
    path = athena / "profiles" / lane / "metrics" / "turns.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def profile(rows: list[dict]) -> dict:
    tokens = [int((r.get("context") or {}).get("prompt_tokens_est") or r.get("prompt_tokens") or 0) for r in rows]
    blocks: dict[str, list[float]] = {}
    for row in rows:
        for name, value in ((row.get("context") or {}).get("blocks") or {}).items():
            blocks.setdefault(name, []).append(float(value or 0))
    return {
        "turns": len(rows),
        "prompt_tokens_p50": statistics.median(tokens) if tokens else 0,
        "prompt_tokens_p90": sorted(tokens)[int(len(tokens) * 0.9) - 1] if len(tokens) > 1 else (tokens[0] if tokens else 0),
        "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 4),
        "blocks_mean": {k: round(sum(v) / len(v), 1) for k, v in sorted(blocks.items())},
        "windows": sorted({int((r.get("context") or {}).get("window") or 0) for r in rows}),
        "models": sorted({str(r.get("model") or "") for r in rows}),
    }


def show(label: str, data: dict) -> None:
    _say(f"{label}: {data['turns']} turns · prompt p50 {data['prompt_tokens_p50']:,.0f} · "
         f"p90 {data['prompt_tokens_p90']:,.0f} · cost ${data['cost_usd']:.4f}")
    _say(f"  windows {data['windows']} · models {data['models']}")
    for name, value in data["blocks_mean"].items():
        _say(f"  {name:14} {value:>10,.1f} tokens")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare what the model was told, before and after a change.")
    ap.add_argument("--athena", default=str(Path(__file__).resolve().parents[5]))
    ap.add_argument("--lane", default="default")
    ap.add_argument("--before", default="", help="ISO timestamp; rows strictly before it")
    ap.add_argument("--after", default="", help="ISO timestamp; rows at or after it")
    args = ap.parse_args(argv)

    athena = Path(args.athena)
    rows = rows_for(athena, args.lane)
    if not rows:
        _refuse("no metric rows", lane=args.lane, athena=str(athena))
        raise SystemExit(f"no metric rows for lane {args.lane!r} under {athena}")

    if not args.before and not args.after:
        old = new = rows                      # no window asked for: describe the whole lane
    else:
        old = [r for r in rows if args.before and str(r.get("ts") or "") < args.before]
        new = [r for r in rows if args.after and str(r.get("ts") or "") >= args.after]
    if not old or not new:
        # falling back to "all rows" made an empty window look like a measurement of zero change
        _refuse("no rows in the requested window", lane=args.lane, before=args.before, after=args.after,
                lane_rows=len(rows))
        raise SystemExit(f"no rows in the requested window (before={args.before!r} after={args.after!r}, "
                         f"lane has {len(rows)} rows)")

    with trace.span("curator.replay", channel=LANE, lane=args.lane, before=args.before or "*",
                    after=args.after or "*", rows=len(rows)) as sp:
        show("before", profile(old))
        if args.before or args.after:
            _say()
            show("after ", profile(new))

        # the comparison a curator needs: did any block move, and did the prompt grow or shrink
        a, b = profile(old), profile(new)
        delta = {}
        for name in set(a["blocks_mean"]) | set(b["blocks_mean"]):
            was = a["blocks_mean"].get(name, 0.0)
            now = b["blocks_mean"].get(name, 0.0)
            if abs(now - was) >= 1.0:
                delta[name] = round(now - was, 1)
        _say()
        _say(f"prompt delta: {b['prompt_tokens_p50'] - a['prompt_tokens_p50']:+,.0f} tokens (p50)")
        _say(f"block deltas : {json.dumps(delta, sort_keys=True) if delta else 'none'}")
        _say("note: sizes only — this cannot tell you whether answers improved, only what the model was told")
        sp.ok(turns_before=len(old), turns_after=len(new), p50_before=a["prompt_tokens_p50"],
              p50_after=b["prompt_tokens_p50"], windowed=bool(args.before or args.after),
              blocks_moved=len(delta), models=len(b["models"]))
    return 0



if __name__ == "__main__":
    raise SystemExit(main())

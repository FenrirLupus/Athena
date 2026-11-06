"""Draft a curator proposal prompt — and stop before spending anything.

Drafting is the first step of this system that costs money, so this script does the cheap half and refuses the
expensive one: it turns the newest scan findings into self-contained prompts with an estimated price, writes them
beside the proposal, and exits. Nothing is sent to a model here.

Run the prompt yourself (an agent turn, or a coding agent) once you have read the estimate. That separation is
deliberate: an unattended process that both decides what to change and pays to design the change is the failure mode
this system exists to avoid.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve()
for parent in ROOT.parents:
    if (parent / "tests" / "run_gate.sh").is_file():
        TREE = parent
        break
else:
    raise SystemExit("cannot find the tree")
CURATOR = TREE.parent / "documentation" / "optimization" / "curator"

if str(TREE) not in sys.path:
    sys.path.insert(0, str(TREE))
from core import trace     # noqa: E402

LANE = "curator"


def _say(text: str = "") -> None:
    """The cost estimate and where the prompts went — the reason this command exists. Not a diagnostic."""
    print(text, flush=True)



def newest_scan() -> dict:
    files = sorted(CURATOR.glob("scan-*.json"))
    if not files:
        raise SystemExit("no scan found — run scan.py first")
    return json.loads(files[-1].read_text(encoding="utf-8"))


def estimate(prompt: str, rate_per_mtok: float, completion_tokens: int = 600) -> float:
    """Rough price: prompt characters / 3.5 tokens, at the configured rate, plus a completion allowance."""
    prompt_tokens = len(prompt) / 3.5
    return round((prompt_tokens + completion_tokens) / 1_000_000 * rate_per_mtok, 4)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write drafting prompts for the newest scan; never sends them.")
    ap.add_argument("--limit", type=int, default=3, help="how many findings to draft for")
    ap.add_argument("--rate", type=float, default=1.0, help="USD per million tokens the provider charges")
    args = ap.parse_args(argv)

    with trace.span("curator.draft", channel=LANE, limit=args.limit, rate=args.rate) as sp:
        scan = newest_scan()
        pairs = (scan.get("duplicates") or {}).get("pairs") or []
        largest = (scan.get("bulk") or {}).get("largest") or []
        if not pairs and not largest:
            sp.ok(drafted=0, why="the last scan found no duplicates")
            _say("nothing to draft: the last scan found no duplicates")
            return 0

        folder = CURATOR / "drafts"
        folder.mkdir(parents=True, exist_ok=True)
        total = 0.0
        written = []
        for index, pair in enumerate(pairs[: args.limit], 1):
            prompt = (
                "You are drafting a curator proposal for Athena, a self-hosted agent system.\n"
                "Two blocks of code are near-duplicates. Read both, then state whether they are genuinely the same "
                "work and, if so, the single helper that should replace them — or why they must stay separate.\n\n"
                f"A: {pair['a']}\nB: {pair['b']}\nsimilarity: {pair['similarity']}\n\n"
                "Answer with: VERDICT (merge / keep / investigate), REASON (two sentences), and the exact replacement "
                "if merging. Do not write code you have not read."
            )
            cost = estimate(prompt, args.rate)
            total += cost
            path = folder / f"draft-{index}-{abs(hash(pair['a'])) % 10**8}.md"
            path.write_text(f"<!-- estimated ${cost:.4f} at ${args.rate}/MTok -->\n\n{prompt}\n", encoding="utf-8")
            written.append((path.name, cost))
            trace.event("curator.draft.pair", level="debug", channel=LANE, index=index,
                        similarity=pair["similarity"], a=pair["a"], b=pair["b"], usd=cost, out=path.name)

        _say(f"wrote {len(written)} prompt(s) to {folder}")
        for name, cost in written:
            _say(f"  {name}  ≈ ${cost:.4f}")
        _say(f"estimated total: ${total:.4f} — nothing was sent; running the prompts is your decision")
        sp.ok(drafted=len(written), total_usd=round(total, 4), folder=str(folder),
              pairs_available=len(pairs), largest=len(largest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

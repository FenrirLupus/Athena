"""Probe a lane's recall index: does asking again actually find the answer again?

Read-only. With no arguments it picks the ten most repeated questions in the lane and asks each one again —
every one must match its own record, which is the cheapest possible check that the index, the scoring and the
quotation agree. `--audit` lists the free answers already in the transcript with the row they quoted.

    python3 scripts/recall_probe.py --athena ~/.athena --lane default [--audit]

Exit codes: 0 every probe matched · 1 at least one did not (printed with its reason).
"""
from __future__ import annotations

import argparse
import re
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve()
for parent in ROOT.parents:
    if (parent / "core").is_dir() and (parent / "server").is_dir():
        TREE = parent
        break
else:
    raise SystemExit("cannot find the tree")

sys.path.insert(0, str(TREE))

from config import loader          # noqa: E402
from core import recall            # noqa: E402
from core import timefmt           # noqa: E402
from data.index import Index       # noqa: E402

PROBES = 10


def _most_repeated(index: Index, limit: int = PROBES) -> list[str]:
    """The most repeated questions, minus the ones no matcher may answer.

    A question that normalises to fewer content words than `recall.min_tokens` is refused by rule (`too_thin`),
    so including it would report a miss for a guard that is working. Filtering keeps hits/N a measure of the
    index rather than of the probe set.
    """
    from core.recall import tokens

    conn = sqlite3.connect(f"file:{index.path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT norm, COUNT(*) AS n, MIN(content) FROM idx_messages"
            " WHERE channel=? AND role='user' AND kind <> 'free' AND kind <> 'seam'"
            " GROUP BY norm HAVING n >= 2 ORDER BY n DESC, norm LIMIT ?", (index.lane, limit * 4)).fetchall()
    finally:
        conn.close()
    picked = []
    for _norm, _count, content in rows:
        if len(tokens(content)) < 2:
            continue
        picked.append(content)
        if len(picked) >= limit:
            break
    return picked


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ask a lane's ten most repeated questions again.")
    parser.add_argument("--athena", default=str(TREE.parent))
    parser.add_argument("--lane", default="default")
    parser.add_argument("--audit", action="store_true", help="list the free answers already recorded")
    parser.add_argument("--synonyms", action="store_true",
                        help="the map's shape: groups, members, and every word in two groups")
    parser.add_argument("--pairs", action="store_true",
                        help="the labelled table from tests/fixtures/recall_pairs.json")
    args = parser.parse_args(argv)

    root = Path(args.athena) / "profiles" / args.lane
    cfg = loader.load()
    if args.synonyms:
        from core import synonyms as synonyms_mod
        name = str((cfg.get("recall") or {}).get("synonyms") or "")
        path = Path(name) if Path(name).is_absolute() else Path(TREE) / name
        mapping = synonyms_mod.load(path)
        two = sorted({word for group in mapping.groups for word in group
                      if sum(1 for other in mapping.groups if word in other) > 1})
        sizes = sorted({len(group) for group in mapping.groups})
        print(f"{mapping.state} · {len(mapping.groups)} groups · {len(mapping.words)} words · "
              f"members per group {sizes} · in two groups: {', '.join(two) or 'none'}")
        if mapping.state != "ok":
            print(f"  reason: {mapping.reason} (line {mapping.line})")
        return 0
    if args.pairs:
        import json
        from core import recall as recall_mod
        from core import synonyms as synonyms_mod
        fixtures = json.loads((Path(TREE) / "tests" / "fixtures" / "recall_pairs.json").read_text(encoding="utf-8"))
        name = str((cfg.get("recall") or {}).get("synonyms") or "")
        path = Path(name) if Path(name).is_absolute() else Path(TREE) / name
        mapping = synonyms_mod.load(path)
        now = time.time()
        for set_name, pairs in fixtures.items():
            fired = 0
            for pair in pairs:
                verdict = recall_mod.decide(
                    pair["a"], [{"role": "user", "content": pair["b"], "uid": "candidate", "author": "",
                                 "ts": timefmt.stamp_from(now - 300)}],
                    cfg, now, "", 0, 100, synonyms=mapping,
                    answer_for=lambda c: {"content": "ok, handled", "uid": "answer", "ts": timefmt.stamp_from(now - 299)})
                hit = verdict["mode"] in ("quote", "pointer")
                fired += hit
                if set_name in ("paraphrase", "negative") and (hit if set_name == "negative" else not hit):
                    print(f"  {'FALSE+' if set_name == 'negative' else 'MISS  '} {pair['a']!r} / {pair['b']!r}"
                          f" -> {verdict['reason']} score={verdict['score']}")
            print(f"{set_name:11} {fired}/{len(pairs)}")
        return 0
    index = Index.for_lane(root, args.lane)
    if not index.exists():
        print(f"no index for {args.lane} — run scripts/recall_reindex.py first", file=sys.stderr)
        return 1

    if args.audit:
        rows = []
        try:
            conn = sqlite3.connect(f"file:{index.path}?mode=ro", uri=True)
            rows = conn.execute(
                "SELECT ts, substr(content,1,100) FROM idx_messages"
                " WHERE channel=? AND kind='free' ORDER BY id DESC LIMIT 20", (args.lane,)).fetchall()
        finally:
            conn.close()
        print(json.dumps([{"ts": r[0], "reply": r[1]} for r in rows], indent=2))
        return 0

    probes = _most_repeated(index)
    if not probes:
        print("no repeated questions in this lane yet — nothing to probe", file=sys.stderr)
        return 1

    now = time.time()
    hits = 0
    for text in probes:
        # ask as whoever asked it: a peer's question is not answered with the operator's words (R12), so the
        # probe has to carry the same author the row was recorded under or it is testing the wrong thing
        sender = re.match(r"^\s*\[from @([A-Za-z0-9._-]+)\]", text)
        verdict = recall.lookup(text, root, cfg, now, author=(sender.group(1) if sender else ""))
        fired = verdict.get("mode") in ("quote", "pointer")
        hits += 1 if fired else 0
        print(f"  {'HIT ' if fired else 'MISS'} {text[:70]!r} -> {verdict.get('reason')}"
              f" score={verdict.get('score')} jac={verdict.get('jaccard')}")
    print(f"\nhits {hits}/{len(probes)}")
    return 0 if hits == len(probes) else 1


if __name__ == "__main__":
    raise SystemExit(main())

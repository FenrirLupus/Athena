"""Free answers from the lane's own record — a repeat is a quotation, never a guess.

The operator's rule (documentation/proposals/recall/notes.md): when the same thing is asked again, the agent
answers from what it already said, without a provider call. So the bar for firing is not "similar topic" — it is
"the same question, to within a word". Everything else costs a model call, which is the correct price for
judgement.

Four guards decide, and each exists because of a measured failure (design.md §3c):

  coverage     |A ∩ B| / |A| >= min_score     how much of what was just asked was asked before
  jaccard      |A ∩ B| / |A ∪ B| >= min_jaccard   stops a two-word message matching a two-hundred-word one
  literals     WAKE1 vs WAKE3 score 0.88 and are different questions — a token with a digit, `_`, `.`, or 16+
               characters must appear on both sides, or the match is refused
  quotable     a matched question whose recorded answer is an error line answered nothing; it is not a quotation

No model, no embeddings, no weights: tokens, set arithmetic, and the transcript.
"""
from __future__ import annotations

import re
from pathlib import Path

from core import temporal
from core import timefmt
from core import trace

__all__ = ["normalize", "tokens", "literals", "quotable", "score", "render", "decide",
           "consecutive_free", "lookup", "FUNCTION_WORDS"]

# Runtime markers are stored inside `content` (core/session.py:109; server/channels.py:425). Stripped repeatedly,
# because `[from @scout] [Message] hi` carries two.
_MARKERS = re.compile(r"^\s*(?:\[(?:Message|Voice|Time|interrupt)\]|\[from @[A-Za-z0-9._-]+\])\s*")
_KEEP = re.compile(r"[^a-z0-9_.]+")

# Function words carry no topic. The negations are deliberately absent: "delete the file" and "do not delete the
# file" must never look alike, and dropping `not` would make them identical.
FUNCTION_WORDS = frozenset("""
a an the and or but if then than that this these those there here is are was were be been being am do does did
doing have has had having i you he she it we they me him her us them my your his its our their to of in on at
by for with from as so such can could would should will shall may might must about into over under again further
once very just only own same too also what which who whom whose when where why how all any both each few more
most other some such no
""".split())

# A recorded answer that is not an answer. Both are shapes this lane actually contains.
_UNQUOTABLE = ("model call failed", "loop error:", "channel error:", "The provider refused this turn")
_TOOL_SYNTAX = ("<tool_call>", "<function=")

# Polarity. `normalize()` keeps these on purpose (core/recall.py FUNCTION_WORDS omits them), and this set is what
# makes that worth doing: "delete the file" and "do not delete the file" share every content word, so without a
# polarity guard the free path answers the first with the second's answer — measured, cover 1.00, jaccard 0.67.
# `no` never survives normalization (it is a function word), which is a known hole: fixing it would change every
# stored norm and require a reindex, so it is out of this round and named in the deviations.
NEGATIONS = frozenset({"not", "never", "cannot", "except", "unless", "without"})

# A stored question may not be much longer than what was asked: expansion-compressed jaccard cannot see length
# (a thematically identical passage and a four-word question collapse to nearly the same synonym sets, measured
# 0.67), so the length ratio does that job directly. Two is the bar: a repeat is the same question, and a stored
# question twice the length of what was asked is a different one.
MAX_LENGTH_RATIO = 2


def normalize(text: str) -> list[str]:
    """The token list both sides of a comparison are reduced to. Order does not matter; contents do.

    Steps, in order (design.md §3c): strip runtime markers repeatedly, lowercase, split on anything that is not
    `[a-z0-9_.]`, strip a token's leading and trailing dots (a sentence-final dot must not become a literal),
    drop tokens under two characters unless they carry a digit, drop function words.
    """
    body = str(text or "")
    while True:
        stripped = _MARKERS.sub("", body, count=1)
        if stripped == body:
            break
        body = stripped
    cleaned = _KEEP.sub(" ", body.lower())
    out: list[str] = []
    for raw in cleaned.split():
        token = raw.strip(".")
        if not token:
            continue
        if len(token) < 2 and not any(ch.isdigit() for ch in token):
            continue
        if token in FUNCTION_WORDS:
            continue
        out.append(token)
    return out


def tokens(text: str) -> list[str]:
    """`normalize`, named for what the scorer calls it."""
    return normalize(text)


def literals(words) -> set[str]:
    """Tokens where a one-character difference changes the answer: `wake3`, `disk_usage`, `MEMORY.md`, a URL."""
    return {w for w in words if any(ch.isdigit() for ch in w) or "_" in w or "." in w or len(w) >= 16}


def negations(words) -> set[str]:
    """The negations present — one side of the polarity guard. Both branches must agree on this set."""
    return {w for w in words if w in NEGATIONS}


def score(asked, stored) -> tuple[float, float, int]:
    """(coverage, jaccard, shared count) over content tokens. Coverage is the deciding figure."""
    a, b = set(asked), set(stored)
    if not a or not b:
        return 0.0, 0.0, 0
    shared = len(a & b)
    return shared / len(a), shared / len(a | b), shared


def quotable(content: str) -> bool:
    """Is this recorded text an answer a reply may quote?

    An error line answered nothing, so quoting it would present a failure as an answer. Tool-call syntax is a
    transcript artefact, not prose. Both are refused rather than cleaned, because cleaning would invent.
    """
    text = str(content or "").strip()
    while True:
        stripped = _MARKERS.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    if len(text) < 2:
        return False
    if any(text.startswith(prefix) for prefix in _UNQUOTABLE):
        return False
    if any(marker in text for marker in _TOOL_SYNTAX):
        return False
    return True


def _age_phrase(age_s: int) -> str:
    if age_s < 60:
        return "just now"
    return f"{temporal.elapsed(age_s)} ago"


def _quote(answered: str, limit: int) -> tuple[str, bool]:
    """(text to quote, truncated?) — cut at the last space before the cap, never mid-word."""
    body = str(answered or "").strip()
    while True:
        stripped = _MARKERS.sub("", body, count=1)
        if stripped == body:
            break
        body = stripped
    if len(body) <= limit:
        return body, False
    cut = body[:limit]
    at = cut.rfind(" ")
    return (cut[:at] if at > 0 else cut).rstrip() + "…", True


def render(mode: str, source_ts: str, source_uid: str, answered: str, age_s: int, staleness: str,
           quote_chars: int) -> str:
    """The reply, character for character (design.md §3d). The closing line is fixed and never varies: a free
    answer must always be identifiable as one."""
    head = (f"I answered this before — {source_ts}, {_age_phrase(age_s)}"
            f" (row {source_uid[:12]}).")
    body, truncated = _quote(answered, quote_chars)
    quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in body.splitlines())
    parts = [head]
    if staleness:
        parts.append(staleness)
    parts.append(quoted)
    if mode == "pointer" or truncated:
        parts.append(f"That is the opening of a longer answer; the rest is in row {source_uid[:12]}.")
    parts.append("Quoted from my own record, not a fresh answer. If you meant something else, "
                 "say so and I will look properly.")
    return "\n\n".join(parts) + "\n"


def _config(cfg: dict) -> dict:
    return cfg.get("recall") or {}


def decide(text: str, candidates: list[dict], cfg: dict, now: float, author: str = "",
           consecutive: int = 0, index_rows: int = 0, answer_for=None, synonyms=None) -> dict:
    """The verdict for one incoming message. Pure: same inputs, same answer, no I/O, no clock of its own.

    `candidates` are past *question* rows (role user, same author), best-rank first. `answer_for(candidate)` returns
    the recorded answer row `{content, ts, uid}` or None. Without it every match is unquotable, which is the honest
    reading: a question with no recorded answer is not a repeat that can be served.
    """
    recall = _config(cfg)
    verdict = {"mode": "none", "reason": "", "reply": "", "source_uid": "", "source_ts": "",
               "age_s": None, "band": "", "score": 0.0, "jaccard": 0.0, "shared": 0,
               "branch": "", "semantic": None, "candidate_uid": "", "index_rows": int(index_rows)}

    if not recall.get("enabled", True):
        verdict["reason"] = "disabled"
        return verdict
    min_rows = int(recall.get("min_history_rows", 50))
    if int(index_rows) < min_rows:
        verdict["reason"] = "cold"
        return verdict
    cap = int(recall.get("max_consecutive", 3))
    if cap and int(consecutive) >= cap:
        verdict["reason"] = "capped"
        return verdict

    asked = tokens(text)
    min_tokens = int(recall.get("min_tokens", 2))
    if len(asked) < min_tokens:
        verdict["reason"] = "too_thin"
        return verdict

    min_score = float(recall.get("min_score", 0.80))
    min_semantic = float(recall.get("min_semantic", 0.80))
    min_jaccard = float(recall.get("min_jaccard", 0.5))
    max_age = float(recall.get("max_age_days", 30)) * 86400
    asked_literals = literals(asked)
    asked_negations = negations(asked)
    semantic_ok = synonyms is not None and getattr(synonyms, "state", "") == "ok"

    best = None
    best_reason = "no_candidate"
    best_attempt = {"score": 0.0, "jaccard": 0.0, "shared": 0, "branch": "", "semantic": None,
                    "candidate_uid": ""}
    for candidate in candidates or []:
        if str(candidate.get("author") or "") != str(author or ""):
            best_reason = "author_mismatch"
            continue
        stored = tokens(candidate.get("content") or "")
        if len(stored) > max(len(asked), min_tokens) * MAX_LENGTH_RATIO:
            best_reason = "below_bar"
            continue
        cover, jac, shared = score(asked, stored)
        branch, matched, used_cover, used_jac, sem_cover = "", 0, 0.0, 0.0, None
        if cover >= min_score and jac >= min_jaccard:
            branch, matched, used_cover, used_jac = "lexical", shared, cover, jac
        else:
            # The semantic pass runs only where lexical failed — the set the second check exists for.
            comparison = synonyms.compare(asked, stored) if semantic_ok else None
            if comparison is not None:
                sem_cover = round(comparison.cover, 4)
                if comparison.cover >= min_semantic and comparison.jaccard >= min_jaccard:
                    branch, matched = "semantic", comparison.matched
                    used_cover, used_jac = comparison.cover, comparison.jaccard
        if not branch:
            best_reason = "below_bar"
            best_attempt = max(best_attempt, {"score": round(cover, 4), "jaccard": round(jac, 4),
                                              "shared": shared, "branch": "", "semantic": sem_cover,
                                              "candidate_uid": str(candidate.get("uid") or "")},
                               key=lambda item: (item["score"], item["jaccard"]))
            continue
        best_attempt = max(best_attempt, {"score": round(used_cover, 4), "jaccard": round(used_jac, 4),
                                          "shared": matched, "branch": branch, "semantic": sem_cover,
                                          "candidate_uid": str(candidate.get("uid") or "")},
                           key=lambda item: (item["score"], item["jaccard"]))
        # `matched` counts words the winning branch covered — a pure paraphrase shares no literal word, so
        # testing the literal intersection here would refuse it before the semantic branch could speak.
        if matched < min_tokens:
            best_reason = "below_bar"
            continue
        if literals(stored) != asked_literals:
            # the WAKE1/WAKE3 case: 0.88 coverage, different question. Refuse rather than answer the wrong one.
            best_reason = "literal_mismatch"
            continue
        if negations(stored) != asked_negations:
            # "delete the file" is not "do not delete the file", however identical the content words
            best_reason = "polarity_mismatch"
            continue
        if answer_for is None:
            best_reason = "answer_unquotable"
            continue
        answer = answer_for(candidate) or None
        if not answer or not quotable(answer.get("content")):
            best_reason = "answer_unquotable"
            continue
        # the age that matters is the age of what would be QUOTED, not of the question that matched it: an old
        # question answered yesterday is current, a fresh question answered last month is not
        try:
            age_s = int(max(0.0, float(now) - timefmt.parse(answer.get("ts") or candidate.get("ts"))))
        except (TypeError, ValueError):
            verdict["reason"] = "unreadable_stamp"
            continue
        if age_s > max_age:
            best_reason = "too_old"
            continue
        best = (candidate, answer)
        verdict.update({
            "mode": "quote" if len(str(answer.get("content") or "")) <= int(recall.get("quote_chars", 600))
                    else "pointer",
            "reason": "matched",
            "source_uid": str(answer.get("uid") or ""),
            "source_ts": str(answer.get("ts") or ""),
            "age_s": age_s,
            "band": temporal.band(age_s, cfg),
            "score": round(used_cover, 4),
            "jaccard": round(used_jac, 4),
            "shared": matched,
            "branch": branch,
            "semantic": sem_cover,
            "candidate_uid": str(candidate.get("uid") or ""),
        })
        break

    if best is None:
        verdict["reason"] = best_reason
        verdict.update(best_attempt)      # the near-miss, so a refusal is tunable rather than mysterious
        trace.event("recall.declined", level="debug", reason=best_reason,
                    score=verdict["score"], jaccard=verdict["jaccard"], shared=verdict["shared"],
                    candidates=len(candidates or []))
        return verdict

    staleness = ""
    if temporal.band(verdict["age_s"], cfg) not in ("continuous", "sitting"):
        staleness = ("That is old enough that it may no longer be current: treat it as what I said then, "
                     "not as what is true now.")
    verdict["reply"] = render(verdict["mode"], verdict["source_ts"], verdict["source_uid"],
                              str(best[1].get("content") or ""), verdict["age_s"], staleness,
                              int(recall.get("quote_chars", 600)))
    trace.event("recall.verdict", level="debug", mode=verdict["mode"], reason=verdict["reason"],
                score=verdict["score"], jaccard=verdict["jaccard"], shared=verdict["shared"],
                age_s=verdict["age_s"], band=verdict["band"], consecutive=int(consecutive),
                index_rows=int(index_rows))
    return verdict


def consecutive_free(rows) -> int:
    """Trailing run of free answers in the transcript — the cap is read from the record, not from memory."""
    count = 0
    for row in reversed(list(rows or [])):
        if row.get("role") == "assistant" and row.get("kind") == "free":
            count += 1
            continue
        if row.get("role") == "user" and row.get("kind") == "seam":
            continue
        break
    return count


def lookup(text: str, root: Path, cfg: dict, now: float, author: str = "", consecutive: int = 0) -> dict:
    """The only I/O in this module: open the lane's index and map, get candidates and the recorded answer, decide."""
    from core import synonyms as synonyms_mod      # local: the pure half must not need SQLite or a file
    from data.index import Index

    recall = _config(cfg)

    def _empty(reason: str) -> dict:
        return {"mode": "none", "reason": reason, "reply": "", "source_uid": "", "source_ts": "",
                "age_s": None, "band": "", "score": 0.0, "jaccard": 0.0, "shared": 0,
                "branch": "", "semantic": None, "candidate_uid": "", "index_rows": 0}

    if not recall.get("enabled", True):
        return _empty("disabled")
    mapping = synonyms_mod.empty()
    name = str(recall.get("synonyms") or "")
    if name:
        path = Path(name)
        if not path.is_absolute():
            # config-relative, so a lane's file and the shipped one resolve the same way from any cwd
            path = Path(__file__).resolve().parents[1] / name
        try:
            mapping = synonyms_mod.load(path)
        except Exception as exc:                     # noqa: BLE001 — load() does not raise; belt and braces
            trace.failure("recall.synonyms.failed", exc, path=str(path))
            mapping = synonyms_mod.empty()
    index = Index.for_lane(Path(root), Path(root).name)
    if not index.exists():
        return _empty("cold")
    try:
        rows = index.count()
        candidates = index.search(text, int(recall.get("candidates", 40)), author=author,
                                  aliases=mapping.expand_words(tokens(text)))
    except Exception as exc:                         # noqa: BLE001 — a broken index must never fail a turn
        trace.failure("recall.index.failed", exc, lane=Path(root).name)
        return _empty("index_error")
    return decide(text, candidates, cfg, now, author=author, consecutive=consecutive,
                  index_rows=rows, answer_for=index.answer_for, synonyms=mapping)

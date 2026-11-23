"""The synonym map — a reviewable data file, and the semantic half of the similarity ladder.

The map is not a concept graph and must never become one: it holds words that mean the same thing *in one sense*,
one group per line, capped at four constants so growth fails in CI rather than in a lane (design-two-check.md §3).
Its quality is the semantic check's entire quality, which is why it is text a reviewer can read rather than a dict
buried in `core/recall.py`.

Matching is one hop, never transitive, and symmetric: `a` matches `b` when they share a group, in either direction.
Expansion is only used for the asked side's denominator, which is what keeps "how much of what was just asked did
I understand" comparable to the lexical coverage number beside it.

A broken file never raises into a turn. `load()` returns a Map whose `state` is not `"ok"`, `compare()` returns
None, and the caller falls back to lexical-only — the same discipline the index failure already follows.
"""
from __future__ import annotations

import functools
from pathlib import Path

from core import trace
from core.recall import FUNCTION_WORDS, literals, tokens

__all__ = ["MAX_GROUPS", "MAX_MEMBERS", "MAX_TOKENS", "MAX_QUERY_TERMS",
           "Map", "Comparison", "load", "empty"]

# Code constants, not config keys: raising one is a diff a reviewer sees, whereas a config key is a number an
# operator can raise without reading the file it governs.
MAX_GROUPS = 300
MAX_MEMBERS = 8
MAX_TOKENS = 1200
MAX_QUERY_TERMS = 64

# Pairs that must never sit in one group: a synonym file that equates two of them turns the free path into a
# machine that answers "delete it" with "I did not delete it".
_ANTONYMS = (("install", "remove"), ("add", "delete"), ("enable", "disable"), ("remember", "forget"),
             ("before", "after"), ("start", "stop"), ("open", "close"), ("allow", "deny"),
             ("true", "false"), ("on", "off"), ("accept", "reject"), ("lock", "unlock"),
             ("mount", "unmount"), ("create", "destroy"), ("begin", "end"), ("push", "pull"))


class Comparison:
    """One semantic comparison's numbers. `cover` decides; `jaccard` and `matched` are the guards."""

    __slots__ = ("cover", "jaccard", "matched")

    def __init__(self, cover: float, jaccard: float, matched: int):
        self.cover = cover
        self.jaccard = jaccard
        self.matched = matched

    def __repr__(self) -> str:                     # a debug line, not a payload
        return f"Comparison(cover={self.cover:.2f}, jaccard={self.jaccard:.2f}, matched={self.matched})"


class Map:
    """An immutable, parsed synonym file. `state` says whether it may be used at all."""

    __slots__ = ("state", "reason", "line", "groups", "words", "_expand")

    def __init__(self, state: str, groups: tuple[tuple[str, ...], ...] = (), words: frozenset = frozenset(),
                 reason: str = "", line: int = 0):
        self.state = state
        self.reason = reason
        self.line = line
        self.groups = groups
        self.words = words
        self._expand = {}
        for group in groups:
            for word in group:
                self._expand.setdefault(word, set()).update(group)

    def __repr__(self) -> str:
        return f"Map(state={self.state!r}, groups={len(self.groups)}, words={len(self.words)})"

    def expand(self, word: str) -> frozenset:
        """The word and every word it shares a group with — one hop, never further."""
        return frozenset(self._expand.get(word) or {word})

    def expand_words(self, words) -> list[str]:
        """Expansion terms for a query, de-duplicated, order preserved — what `Index.search` is handed."""
        out: list[str] = []
        for word in words or []:
            for term in (word, *sorted(self._expand.get(word, ()))):
                if term not in out:
                    out.append(term)
                if len(out) >= MAX_QUERY_TERMS:
                    return out
        return out

    def compare(self, asked, stored) -> Comparison | None:
        """(cover, jaccard, matched) over the asked side — or None when this map may not be used."""
        if self.state != "ok":
            return None
        a, b = list(asked or []), list(stored or [])
        if not a or not b:
            return Comparison(0.0, 0.0, 0)
        ea = {t for w in a for t in self.expand(w)}
        eb = {t for w in b for t in self.expand(w)}
        matched = sum(1 for w in a if self.expand(w) & eb)
        union = ea | eb
        cover = matched / len(a)
        jaccard = (len(ea & eb) / len(union)) if union else 0.0
        return Comparison(cover, jaccard, matched)


def empty(state: str = "missing", reason: str = "") -> Map:
    return Map(state, reason=reason)


def _bad(line: int, reason: str) -> Map:
    trace.event(f"recall.synonyms.{reason}", level="info", line=line, file="synonyms", state="invalid")
    return Map("invalid", reason=reason, line=line)


def _group_words(raw: str) -> list[str]:
    body = raw.split("#", 1)[0].strip()
    return body.split() if body else []


def _antonym_pair(words: list[str]) -> tuple[str, str] | None:
    present = set(words)
    for left, right in _ANTONYMS:
        if left in present and right in present:
            return left, right
    return None


def _parse(path: Path) -> Map:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Map("missing", reason="file not found")
    except (OSError, UnicodeDecodeError) as exc:
        return Map("invalid", reason=f"{type(exc).__name__}: {exc}"[:120])
    groups: list[tuple[str, ...]] = []
    membership: dict[str, int] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            raw.encode("ascii")
        except UnicodeEncodeError:
            return _bad(number, "non-ascii")
        words = _group_words(raw)
        if len(words) < 2:
            return _bad(number, "group-of-one")
        if len(words) > MAX_MEMBERS:
            return _bad(number, "group-too-large")
        if len(set(words)) != len(words):
            return _bad(number, "duplicate-word")
        for word in words:
            if len(word) < 2 or len(word) > 24:
                return _bad(number, "token-length")
            if word in FUNCTION_WORDS:
                return _bad(number, "function-word")
            if literals([word]) or word.isdigit() or "_" in word or "." in word:
                return _bad(number, "literal-not-allowed")
            if not all(ch.isalnum() or ch in "_-." for ch in word):
                return _bad(number, "token-shape")
            if word not in tokens(word):
                return _bad(number, "not-a-normalized-token")
        pair = _antonym_pair(words)
        if pair:
            return _bad(number, f"antonyms:{pair[0]}/{pair[1]}")
        for word in words:
            membership[word] = membership.get(word, 0) + 1
            if membership[word] > 2:
                return _bad(number, "three-groups")
        groups.append(tuple(words))
        if len(groups) > MAX_GROUPS:
            return Map("too_large", reason=f"more than {MAX_GROUPS} groups", line=number)
        if len(membership) > MAX_TOKENS:
            return Map("too_large", reason=f"more than {MAX_TOKENS} distinct words", line=number)
    return Map("ok", tuple(groups), frozenset(membership))


@functools.lru_cache(maxsize=4)
def _load_cached(path_str: str, mtime_ns: int, size: int) -> Map:
    return _parse(Path(path_str))


def load(path) -> Map:
    """Load and cache the map. Never raises: a missing or broken file disables the semantic branch.

    Keyed on (path, mtime, size), so an edit is picked up on the next turn without a restart, an unchanged file
    costs one `stat()`, and a malformed edit is reported on the next turn rather than at import.
    """
    if not path:
        return Map("missing", reason="recall.synonyms is empty")
    try:
        target = Path(path)
        stat = target.stat()
    except OSError as exc:
        trace.event("recall.synonyms.missing", level="info", path=str(path), error=type(exc).__name__)
        return Map("missing", reason="file not found")
    parsed = _load_cached(str(target), stat.st_mtime_ns, stat.st_size)
    if parsed.state != "ok":
        trace.event(f"recall.synonyms.{parsed.state}", level="info", path=str(target),
                    reason=parsed.reason, line=parsed.line)
    return parsed

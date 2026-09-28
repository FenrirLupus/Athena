"""Retrieval — how the loop finds more information when context is thin.

Priority (the Operator's spec): SESSION first, then INDEX, then VAULT. Keyword +
semantic.

    retrieve(query, session_id):
      1. session.db  — keyword search over the session's messages
         └─ hits? return FULL rows (the immediate store wins)
      2. index.db    — categories matching the query (the table of contents)
      3. vault.db    — FTS5 keyword search over entries_fts
      4. semantic    — embeddings similarity re-rank (LM Studio local model)

Returns full rows always — a row is the entry, columns are its pieces.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Optional

from core import db as db_layer
from core.config import load_config
from providers.provider import _post_json


def _tokenize(text: str) -> set[str]:
    """Simple keyword tokens (lowercased alphanumeric runs)."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _matches(query_tokens: set, text: str, min_hits: int = 1) -> bool:
    text_tokens = _tokenize(text)
    return len(query_tokens & text_tokens) >= min_hits


def search_session(session_id: str, query: str, limit: int = 10,
                   profile: str = "") -> list[dict]:
    """Stage 1: keyword search over the session's own messages (FULL rows)."""
    # READ-ONLY (the Operator's hygiene rule): a missing session returns
    # no results — it must not be created by a search.
    from core import db as db_layer
    path = db_layer.session_path(session_id, profile or "default")
    if not path.exists():
        return []
    conn = db_layer.connect_session(session_id, profile=profile or "default",
                                    create=False)
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY seq DESC LIMIT ?",
            (session_id, max(limit * 5, 50)),
        ).fetchall()
    finally:
        conn.close()
    q = _tokenize(query)
    hits = []
    for row in rows:
        content = row["content"] or ""
        if _matches(q, content):
            hits.append(dict(row))
        if len(hits) >= limit:
            break
    return hits


def search_index(query: str, limit: int = 10, profile: str = "") -> list[dict]:
    """Stage 2: find index categories (TOC sections) matching the query."""
    q = _tokenize(query)
    hits = []
    for sec in db_layer.list_index(limit=200, profile=profile):
        if _matches(q, sec["category"]):
            hits.append(sec)
        if len(hits) >= limit:
            break
    return hits


def search_vault_keyword(query: str, limit: int = 10,
                         exclude_session: str = "",
                         profile: str = "") -> list[dict]:
    """Stage 3: FTS5 keyword search over the archive (FULL rows).

    The vault has no meta column (the columns ARE the metadata), so no
    session-based row exclusion happens here — the session stage covers
    the immediate store; the vault is the long-term archive.
    """
    q = _tokenize(query)
    if not q:
        return []
    fts_query = " OR ".join(f'"{tok}"' for tok in q)
    conn = db_layer.connect_vault(profile)
    try:
        rows = conn.execute(
            "SELECT rowid, * FROM entries_fts WHERE entries_fts MATCH ? LIMIT ?",
            (fts_query, limit * 5),
        ).fetchall()
        # Resolve FTS rows to FULL entries. the external-content FTS is
        # keyed by rowid (entries.rowid), Athena's standalone FTS carries
        # its own rowid too — so rowid is the portable key. Prefer the
        # explicit id when present (Athena standalone), else rowid.
        results = []
        for row in rows:
            rowid = row["rowid"]
            entry = None
            if "id" in row.keys() and row["id"]:
                entry = conn.execute(
                    "SELECT * FROM entries WHERE id=? AND deleted=0",
                    (row["id"],),
                ).fetchone()
            if entry is None:
                entry = conn.execute(
                    "SELECT * FROM entries WHERE rowid=? AND deleted=0",
                    (rowid,),
                ).fetchone()
            if not entry:
                continue
            # (The old meta-based session exclusion is gone — the vault has
            # no meta column; the columns ARE the metadata. The session
            # stage already handles the current session's history.)
            results.append(dict(entry))
            if len(results) >= limit:
                break
    finally:
        conn.close()
    return results


# -- Semantic (stage 4) -------------------------------------------------

def _embed(texts: list[str], model: str, base_url: str,
           api_key: str = "", timeout: float = 30.0) -> list[list[float]]:
    """Embed texts via an OpenAI-compatible /embeddings endpoint.

    THE 08-15 FIX: when the provider 404s (a chat-only relay like
    opencode serving no /embeddings), the semantic stage marks the
    provider as UNSUPPORTED — subsequent retrievals SKIP the stage
    entirely instead of hammering the dead endpoint every turn (the
    recurring L3 provider 404 noise).
    """
    global _embedding_disabled
    url = f"{base_url.rstrip('/')}/embeddings"
    try:
        data = _post_json(url, api_key, {
            "model": model,
            "input": texts,
        }, timeout=timeout)
    except Exception as exc:
        # A 404/400 from a chat-only relay = this provider cannot embed.
        # Disable the semantic stage for the process (it can never work
        # against this provider) — the rerank falls back to keyword order.
        try:
            if "404" in str(exc) or "400" in str(exc) or "not found" in str(exc).lower():
                if not _rerank_warned():
                    from core.logging import log_event
                    log_event(2, "embedding endpoint unsupported (404) — "
                                 "semantic rerank disabled for this process",
                              source="context", action="semantic_rerank")
                _embedding_disabled = True
                # THE 08-15 PERSISTENT MARKER: the endpoint is dead —
                # record it so future boots skip it too (no per-boot 404).
                _mark_embedding_unsupported()
        except Exception:
            pass
        raise
    return [item["embedding"] for item in data["data"]]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def semantic_rerank(query: str, candidates: list[dict],
                    embedding_model: str, base_url: str,
                    api_key: str = "", top: int = 5) -> list[dict]:
    """Embed the query + candidates, return the top by cosine similarity."""
    if not candidates:
        return []
    try:
        texts = [c.get("content", "") for c in candidates]
        query_vec = _embed([query], embedding_model, base_url, api_key)[0]
        cand_vecs = _embed(texts, embedding_model, base_url, api_key)
        scored = [
            (c, _cosine(query_vec, vec))
            for c, vec in zip(candidates, cand_vecs)
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        top_results = [c for c, _score in scored[:top]]
        for c, score in zip(top_results, [s for _c, s in scored[:top]]):
            c["_similarity"] = round(score, 4)
        return top_results
    except Exception as exc:
        # A dead LOCAL embedding endpoint (lmstudio/vllm/ollama not running)
        # is an EXPECTED condition, not a system error — log it ONCE per
        # process (L2 notice), then fall back silently to keyword order.
        # Repeated L3 warnings on every retrieval would drown the logs.
        if not _rerank_warned():
            from core.logging import log_event
            log_event(2, f"semantic rerank unavailable (falling back): {exc}",
                      source="context", action="semantic_rerank")
        # Semantic fails gracefully — fall back to keyword order.
        return candidates[:top]


_rerank_notice_sent = False
_embedding_disabled = False   # THE 08-15 FIX: set when the embedding
                              # provider 404s — skip the semantic stage
                              # for the rest of the process.
_EMBED_MARKER = None          # THE 08-15 PERSISTENT MARKER (lazy path)


def _embed_marker_path() -> Path:
    """The marker file: survives boots so the dead endpoint is never
    called again (the opencode relay has no /embeddings — a 404 every
    boot was per-boot noise)."""
    global _EMBED_MARKER
    if _EMBED_MARKER is None:
        try:
            from filesystem.safety import ATHENA_ROOT
            _EMBED_MARKER = ATHENA_ROOT / ".embedding_unsupported"
        except Exception:
            _EMBED_MARKER = Path.home() / ".athena" / ".embedding_unsupported"
    return _EMBED_MARKER


def _embedding_supported() -> bool:
    """True when the semantic stage may call the endpoint — false once a
    404 has been confirmed (marker exists), surviving processes."""
    try:
        return not _embed_marker_path().exists()
    except Exception:
        return True


def _mark_embedding_unsupported() -> None:
    """Persist the 404: the endpoint is dead — never call it again."""
    try:
        _embed_marker_path().write_text("opencode relay: no /embeddings\n",
                                        encoding="utf-8")
    except Exception:
        pass


def _rerank_warned() -> bool:
    """Once-per-process notice for an unavailable embedding server."""
    global _rerank_notice_sent
    if _rerank_notice_sent:
        return True
    _rerank_notice_sent = True
    return False


def retrieve(query: str, session_id: str = "", *, config: Optional[dict] = None,
             profile: str = "") -> dict:
    """The full retrieval ladder. Returns {query, session, index, vault, semantic}.

    Priority is SESSION-first, but the vault is still consulted — the spec
    says look for more information through the index AND the vault AND the
    session; the session's hits are simply ranked first. A weak session
    hit (e.g. the model's own earlier reply) must not block the archive.
    All stages run against the PROFILE's stores (named profiles keep their
    own sessions/vault under profiles/<name>/).
    """
    cfg = config or load_config()
    retr = cfg.get("retrieval", {})
    limit = int(retr.get("limit", 5))

    result = {"query": query, "session": [], "index": [], "vault": [], "semantic": []}

    # Stage 1: SESSION (the immediate store — ranked first, always searched).
    if session_id:
        result["session"] = search_session(session_id, query, limit=limit, profile=profile)

    # Stage 2: INDEX (the table of contents).
    result["index"] = search_index(query, limit=limit, profile=profile)

    # Stage 3: VAULT keyword (FTS5) — consulted even when the session hit,
    # but EXCLUDING the current session's own rows (the session stage owns
    # the immediate store; the vault holds OTHER knowledge).
    result["vault"] = search_vault_keyword(
        query, limit=max(limit * 3, 15), exclude_session=session_id or "",
        profile=profile,
    )

    # Stage 4: SEMANTIC re-rank of the vault candidates (local embeddings).
    # THE 08-15 FIX: when the embedding provider 404'd (a chat-only relay
    # with no /embeddings), the stage is DISABLED — per-process via the
    # flag, AND across boots via the marker file.
    if (retr.get("semantic", True) and result["vault"]
            and not _embedding_disabled and _embedding_supported()):
        provider = None
        try:
            from providers.models import resolve_models
            resolved = resolve_models("embedding", cfg)
            if resolved["none"]:
                # No embedding model anywhere — a one-time notice (the
                # same once-per-process rule as an unreachable server),
                # then fall back to keyword order silently.
                if not _rerank_warned():
                    from core.logging import log_event
                    log_event(2, "no model set for embedding — semantic rerank skipped",
                              source="context", action="semantic_rerank")
                provider = None
            else:
                # Try the resolved models in order against the provider
                # that serves them (feature models, else the main provider).
                from providers.provider import ProviderChain
                chain = ProviderChain(cfg)
                emb_model = resolved["models"][0]
                for p in chain.providers:
                    if emb_model in (p.models or []) or resolved["fallback"]:
                        provider = p
                        break
                else:
                    for p in chain.providers:
                        if "lmstudio" in p.name:
                            provider = p
                            break
        except Exception:
            provider = None
        if provider is not None:
            result["semantic"] = semantic_rerank(
                query,
                result["vault"],
                embedding_model=resolved.get("models", ["text-embedding-nomic-embed-text-v1.5"])[0],
                base_url=provider.base_url,
                api_key=provider.api_key,
                top=limit,
            )
    return result

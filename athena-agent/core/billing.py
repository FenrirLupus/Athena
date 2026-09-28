"""Billing — usage accounting (the Operator's spec: fully set up).

The vault records usage_prompt/usage_completion/usage_total per entry,
tagged with api_provider/api_model. This module aggregates that into
per-provider billing views: totals, per-model breakdowns, and the
spend picture the GUI/CLI show. Pure read — no provider calls.
"""
from __future__ import annotations

from core import db as db_layer


def _log(level: int, msg: str, source: str = "billing") -> None:
    """Billing is an operational module — its failures are system events."""
    try:
        from metrics.logger import log
        log(level, msg, source=source)
    except Exception:
        pass


def usage_summary(profile: str = "") -> dict:
    """The overall usage picture for a profile's vault."""
    conn = db_layer.connect_vault(profile)
    row = conn.execute(
        "SELECT COUNT(*) as calls, "
        "COALESCE(SUM(usage_prompt),0) as prompt, "
        "COALESCE(SUM(usage_completion),0) as completion, "
        "COALESCE(SUM(usage_total),0) as total "
        "FROM entries WHERE deleted=0 AND usage_total IS NOT NULL"
    ).fetchone()
    conn.close()
    return {
        "calls": row["calls"],
        "prompt_tokens": row["prompt"],
        "completion_tokens": row["completion"],
        "total_tokens": row["total"],
    }


def per_provider(profile: str = "") -> list[dict]:
    """Usage grouped by provider (api_provider). Newest providers first."""
    conn = db_layer.connect_vault(profile)
    rows = conn.execute(
        "SELECT api_provider, api_model, COUNT(*) as calls, "
        "SUM(usage_prompt) as prompt, SUM(usage_completion) as completion, "
        "SUM(usage_total) as total "
        "FROM entries WHERE deleted=0 AND usage_total IS NOT NULL "
        "AND api_provider IS NOT NULL "
        "GROUP BY api_provider, api_model "
        "ORDER BY total DESC"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        out.append({
            "provider": r["api_provider"],
            "model": r["api_model"],
            "calls": r["calls"],
            "prompt_tokens": r["prompt"],
            "completion_tokens": r["completion"],
            "total_tokens": r["total"],
        })
    return out


def per_session(profile: str = "", limit: int = 20) -> list[dict]:
    """The most recent sessions with their usage (session-level billing)."""
    from core import db as dbmod
    sessions = db_layer.uuid_session_ids(profile=profile)[:limit]
    out = []
    for sid in sessions:
        try:
            conn = db_layer.connect_session(sid, profile=profile,
                                            create=False)
            row = conn.execute(
                "SELECT COUNT(*) as msgs, "
                "COALESCE(SUM(usage_prompt),0) as prompt, "
                "COALESCE(SUM(usage_completion),0) as completion, "
                "COALESCE(SUM(usage_total),0) as total "
                "FROM messages WHERE usage_total IS NOT NULL"
            ).fetchone()
            conn.close()
            if row["total"]:
                out.append({
                    "session_id": sid,
                    "messages": row["msgs"],
                    "prompt_tokens": row["prompt"],
                    "completion_tokens": row["completion"],
                    "total_tokens": row["total"],
                })
        except Exception as exc:
            _log(3, f"billing per_session failed for {sid}: {exc}",
                 source="billing")
            continue
    return out

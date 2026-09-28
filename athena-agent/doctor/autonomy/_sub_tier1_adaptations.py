"""Tier-1 adaptations test — the Operator's five specs.

1. Turn retry state — transient failures get ONE retry per turn.
2. Model metadata — per-model context windows (right-sized compression).
3. Turn summary — a one-line recap stored on the session.
4. Error classifier — categorize failures for nurse triage.
5. Session activity — last-active + staleness.
"""
from __future__ import annotations


def run() -> list[dict]:
    import tempfile
    from pathlib import Path
    from core.turn_retry import (begin_turn, end_turn, should_retry,
                                 is_transient, retry_stats)
    from core.model_metadata import lookup, context_window, active_model_context
    from core.turn_summary import build_summary
    from core.error_classifier import classify, describe, nurse_needed
    from core import db as db_layer
    import core.db as dbmod

    checks = []

    # 1. Turn retry: transient → retry once, then exhausted.
    begin_turn("t-retry-test")
    first = should_retry("t-retry-test", "p", "m", "timed out after 10s")
    second = should_retry("t-retry-test", "p", "m", "timed out again")
    non_transient = is_transient("401 unauthorized")
    checks.append({
        "name": "turn retry: one retry per provider/model per turn",
        "status": "ok" if first and not second and not non_transient
        else "fail",
        "detail": f"first={first} second={second} non_transient={non_transient}",
    })
    end_turn("t-retry-test")

    # 2. Model metadata: known model → its window; unknown → configured.
    checks.append({
        "name": "model metadata resolves known models",
        "status": "ok" if lookup("deepseek-v4-flash")
        and context_window("deepseek-v4-flash") == 32768
        and lookup("claude-sonnet-4") else "fail",
        "detail": f"deepseek window={context_window('deepseek-v4-flash')}",
    })

    # 3. Turn summary: builds a one-line recap.
    s = build_summary("check the weather", "it is sunny",
                      tool_names=["weather"])
    checks.append({
        "name": "turn summary builds a recap",
        "status": "ok" if s and "check the weather" in s
        and "weather" in s else "fail",
        "detail": s[:60],
    })

    # 4. Error classifier: the five categories.
    cases = [
        ("timed out after 30s", "transient"),
        ("401 unauthorized: bad api key", "config"),
        ("no space left on device", "resource"),
        ("no such table: entries", "logic"),
        ("mystery failure", "unknown"),
    ]
    ok_cases = all(classify(err) == want for err, want in cases)
    checks.append({
        "name": "error classifier: 5 categories",
        "status": "ok" if ok_cases else "fail",
        "detail": str([classify(e) for e, _ in cases]),
    })
    checks.append({
        "name": "error classifier: nurse needed for logic errors",
        "status": "ok" if nurse_needed("no such table: x")
        and not nurse_needed("timed out") else "fail",
        "detail": "logic → nurse; transient → retry",
    })

    # 5. Session activity: a temp session shows active, not stale.
    import core.db as dbmod2
    orig_vault = db_layer.vault_path
    orig_sessions = dbmod.sessions_dir
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        db_layer.vault_path = staticmethod(
            lambda *a, **k: td_path / "vault.db")
        dbmod.sessions_dir = staticmethod(
            lambda *a, **k: td_path / "sessions")
        (td_path / "sessions").mkdir(parents=True, exist_ok=True)
        sid = db_layer.new_session(profile="")
        db_layer.record_session_message(sid, "user", "hi", profile="")
        # THE BOUNDARY MARGIN (the 08-14 fix): stale_after_s=1 put a
        # just-created session exactly ON the threshold (age=1s → stale
        # flake). 2s gives the write + check time to complete well
        # inside the freshness window.
        act = db_layer.session_activity(profile="", stale_after_s=2)
        fresh = next((a for a in act if a["session_id"] == sid), None)
        checks.append({
            "name": "session activity: fresh session not stale",
            "status": "ok" if fresh and not fresh["stale"]
            and fresh["messages"] >= 1 else "fail",
            "detail": f"age={fresh.get('age_s') if fresh else '?'}s",
        })
    db_layer.vault_path = orig_vault
    dbmod.sessions_dir = orig_sessions
    return checks

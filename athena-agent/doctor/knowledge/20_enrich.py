"""Knowledge enrichment test — the Operator's hourly change-detecting sweep.

The vault's scene columns (context/setting/location/emotion/mood/
activity) are filled by the enrichment pass: an hourly service that
checks whether the vault changed (the free GATE), then fills each
incomplete row one by one using the +/-3 sliding window (previous +
next history) — applicable fields only, decided from content alone.
"""
from __future__ import annotations


def run() -> list[dict]:
    import tempfile
    import time
    import os
    from pathlib import Path
    from unittest.mock import patch
    from core import db as db_layer
    import knowledge.enrich as enrich

    checks = []
    orig_vault = db_layer.vault_path
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        db_layer.vault_path = staticmethod(lambda *a, **k: td_path / "vault.db")
        try:
            db_layer.record_vault_entry(
                "message", "she walked into the bedroom and sat on the bed",
                role="user", context="", dedup=False)
            db_layer.record_vault_entry(
                "message", "the moonlight came through the window",
                role="assistant", context="", dedup=False)
            db_layer.record_vault_entry(
                "message", "he leaned closer, heart racing",
                role="user", context="", dedup=False)

            # 1. Incomplete-row baseline.
            rows = enrich.incomplete_rows("")
            checks.append({
                "name": "enrich: incomplete rows baseline",
                "status": "ok" if len(rows) == 3 else "fail",
                "detail": f"{len(rows)} candidates",
            })

            # 2. The +/-3 sliding window (previous + next history).
            tgt = rows[-1]["rowid"]
            win = enrich.sliding_window(tgt, profile="")
            checks.append({
                "name": "enrich: sliding window +/-3",
                "status": "ok" if len(win["previous"]) + len(win["next"]) >= 2
                and "target" in win else "fail",
                "detail": f"prev={len(win['previous'])} next={len(win['next'])}",
            })

            # 3. The change-detecting gate (fresh = changed, old = not).
            fresh = enrich.vault_modified_since(3600, profile="")
            vp = td_path / "vault.db"
            old = time.time() - 7200
            os.utime(vp, (old, old))
            stale = enrich.vault_modified_since(3600, profile="")
            checks.append({
                "name": "enrich: gate change-detecting",
                "status": "ok" if fresh and not stale else "fail",
                "detail": f"fresh={fresh} stale={stale}",
            })

            # 4. The sweep fills applicable fields (mocked provider).
            class FakeResult:
                def __init__(self, reply):
                    self.reply = reply
                    self.tool_transcript = []
                    self.finish_reason = "stop"
                    self.usage = None
                    self.exit_reason = "completed"
                    self.api_calls = 0
                    self.tool_calls_made = 0
                    self.updated_history = []
            fake_json = ('{"context": "an intimate moment in the bedroom", '
                         '"setting": "a dim bedroom with moonlight", '
                         '"location": "bedroom", "emotion": "nervous", '
                         '"mood": "excited", "activity": "leaning closer"}')
            with patch("core.message_loop.MessageLoop.run_turn",
                       return_value=FakeResult(fake_json)):
                res = enrich.run_once("", dry_run=False)
            conn = db_layer.connect_vault("")
            n = conn.execute("SELECT COUNT(*) FROM entries WHERE context IS NOT NULL AND context!=''").fetchone()[0]
            conn.close()
            checks.append({
                "name": "enrich: sweep fills rows",
                "status": "ok" if n == 3 and res["filled"] == 3 else "fail",
                "detail": f"filled={res['filled']} context_rows={n}",
            })

            # 5. The service is registered (hourly cadence).
            from autonomy.scheduler import SERVICES
            names = [s[0] for s in SERVICES]
            checks.append({
                "name": "enrich: hourly service registered",
                "status": "ok" if "enrich" in names else "fail",
                "detail": "hourly" if "enrich" in names else "missing",
            })
        finally:
            db_layer.vault_path = orig_vault
    return checks

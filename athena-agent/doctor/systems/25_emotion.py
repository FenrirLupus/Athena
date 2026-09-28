"""Emotion system test — the Operator's 08-11 spec.

The emotion framework: EMOTION.md per profile (assistant + user), the
Plutchik 8-axis ternary vector (-1..+1, three equal bands), the Emotional
Axis Array (band classifier), the 24×24 pair map (same+same step-up,
adjacent dyads, complementary grief/ecstasy diameter, arc rule), prompt
injection (blocks 2+4), vault emotion/mood columns, the gauge cycle, the
CLI/GUI surface, and the Behavior page trio.
"""
from __future__ import annotations
from core.config import ATHENA_ROOT


def run() -> list[dict]:
    from pathlib import Path
    checks = []

    # 1. Every profile has EMOTION.md on BOTH sides.
    from intelligence.profiles import list_profiles
    profiles = list_profiles()
    missing = []
    for p in profiles:
        for side in ("assistant", "user"):
            if not (p.root / side / "EMOTION.md").exists():
                missing.append(f"{p.name}:{side}")
    checks.append({
        "name": "every profile has EMOTION.md (assistant + user)",
        "status": "ok" if not missing else "fail",
        "detail": ", ".join(missing) if missing else f"{len(profiles)} profiles seeded",
    })

    # 1b. The COMPLETENESS rule (the Operator's 08-11 spec): every profile
    #     has ALL six system files — identity, memory, emotion on both
    #     sides. ensure_profile_files creates any missing default.
    from intelligence.profiles import ensure_profile_files
    _six = ["assistant/ASSISTANT.md", "user/USER.md",
            "assistant/MEMORY.md", "user/MEMORY.md",
            "assistant/EMOTION.md", "user/EMOTION.md"]
    _all_complete = True
    for p in profiles:
        ensure_profile_files(p)
        for f in _six:
            if not (p.root / f).exists():
                _all_complete = False
    checks.append({
        "name": "every profile has all 6 system files (completeness)",
        "status": "ok" if _all_complete else "fail",
        "detail": f"{len(profiles)} profiles × 6 files",
    })
    # A BORN profile gets all six automatically. ISOLATED: the born
    # profile lives in a tempdir so it never touches the real tree.
    try:
        from intelligence.profiles import create_profile
        import intelligence.profiles as _iprof
        import core.config as _cfg_mod
        import tempfile as _tmpfile
        from pathlib import Path as _Path
        import shutil as _shutil
        import uuid as _uuid
        _opd = _iprof.PROFILES_DIR
        _oroot = _cfg_mod.ATHENA_ROOT
        with _tmpfile.TemporaryDirectory() as _td:
            _t = _Path(_td)
            _iprof.PROFILES_DIR = _t / "profiles"
            _cfg_mod.ATHENA_ROOT = _t
            try:
                _bp_name = f"doctor-test-profile-{_uuid.uuid4().hex[:8]}"
                _bp = create_profile(_bp_name)
                _born_ok = all((_bp.root / f).exists() for f in _six)
                _shutil.rmtree(_bp.root, ignore_errors=True)
            finally:
                _iprof.PROFILES_DIR = _opd
                _cfg_mod.ATHENA_ROOT = _oroot
    except Exception as exc:  # noqa: BLE001
        _born_ok = False
    checks.append({
        "name": "born profile gets all 6 files at birth",
        "status": "ok" if _born_ok else "fail",
        "detail": "create_profile → ensure_layout → ensure_profile_files",
    })

    # 2. The 8 axes × 3 bands shape: names match the spec, bands classify.
    from core.emotion import (AXES, WHEEL, band_of, emotion_name,
                              default_emotion, read_emotion, write_emotion,
                              active_combinations, combine, EMOTION_ORDER)
    shape_ok = (len(AXES) == 8 and len(EMOTION_ORDER) == 24
                and all(len(WHEEL[a]) == 3 for a in AXES))
    checks.append({
        "name": "8 axes × 3 bands = 24 named emotions",
        "status": "ok" if shape_ok else "fail",
        "detail": f"{len(AXES)} axes, {len(EMOTION_ORDER)} emotions",
    })

    band_ok = (band_of(-0.5) == -1 and band_of(0.0) == 0 and band_of(0.8) == 1
               and emotion_name("joy", -0.5) == "Serenity"
               and emotion_name("joy", 0.0) == "Joy"
               and emotion_name("joy", 0.8) == "Ecstasy")
    checks.append({
        "name": "ternary bands classify correctly",
        "status": "ok" if band_ok else "fail",
        "detail": "low/neutral/high → deactivated/neutral/activated names",
    })

    # 3. The pair map: same+same step-up, adjacent dyads, complementary.
    same = combine("joy", 0.0, "joy", 0.0)
    dyad = combine("joy", 0.0, "trust", 0.0)
    down = combine("anger", -0.2, "fear", -0.3)
    up = combine("anger", 0.5, "fear", 0.4)
    pair_ok = (same["canonical"] == "Ecstasy"
               and dyad["canonical"] == "Love"
               and down["canonical"] == "Grief"
               and up["canonical"] == "Ecstasy")
    checks.append({
        "name": "pair map: step-up + dyads + complementary",
        "status": "ok" if pair_ok else "fail",
        "detail": f"Joy+Joy={same['canonical']} Joy+Trust={dyad['canonical']} "
                  f"Anger+Fear↓={down['canonical']} ↑={up['canonical']}",
    })

    # 4. Neutral vector → no active combination; felt vector → the mood
    #    LIST (dominant's own feel + each pair).
    neutral = default_emotion()
    felt = dict(neutral); felt["joy"] = 0.5; felt["trust"] = 0.4
    _cmbs = active_combinations(felt)
    combos_ok = (not active_combinations(neutral)
                 and len(_cmbs) == 2
                 and _cmbs[0]["canonical"] == "Ecstasy"   # joy's own feel
                 and _cmbs[1]["canonical"] == "Love")     # joy+trust → Love
    checks.append({
        "name": "active combinations: neutral → none, felt → mood list",
        "status": "ok" if combos_ok else "fail",
        "detail": f"neutral=[] felt={[c['canonical'] for c in _cmbs]}",
    })

    # 4b. The 24×24 table: (0,0)=Neutral, symmetric, ≤2 per row/col,
    # enriched vocabulary (dyads + blends + midpoint).
    from core.emotion import table_grid, highlight_cells, NEUTRAL
    from collections import Counter as _Cnt
    grid = table_grid()
    _sym = all(grid[i][j] == grid[j][i]
               for i in range(25) for j in range(25))
    _rows_ok = all(max(_Cnt(grid[i][j] for j in range(1, 25)).values()) <= 2
                   for i in range(1, 25))
    _cols_ok = all(max(_Cnt(grid[i][j] for i in range(1, 25)).values()) <= 2
                   for j in range(1, 25))
    table_ok = (len(grid) == 25 and len(grid[0]) == 25
                and grid[0][0] == NEUTRAL
                and _sym and _rows_ok and _cols_ok
                and grid[2][2] == "Joy"          # Joy+Joy → itself
                and grid[2][5] == "Love"         # Joy+Trust → the dyad
                and grid[18][21] == "Hate")      # Loathing+Rage → the blend
    hl = highlight_cells(felt)
    # joy+trust → the dominant's own cell (3,3) + the mix cell (3,6).
    hl_ok = set(map(tuple, hl)) == {(3, 3), (3, 6)}
    checks.append({
        "name": "24×24 table: symmetric, ≤2/row+col, enriched vocab",
        "status": "ok" if table_ok else "fail",
        "detail": f"grid={len(grid)}x{len(grid[0])} sym={_sym} "
                  f"rows<=2={_rows_ok} cols<=2={_cols_ok} "
                  f"(0,0)={grid[0][0]} Joy+Trust={grid[2][5]} "
                  f"Loathing+Rage={grid[18][21]}",
    })
    checks.append({
        "name": "24×24 table: live vector highlights its active cells",
        "status": "ok" if hl_ok else "fail",
        "detail": f"joy+trust → highlight {hl} (want [[3,3],[3,6]])",
    })

    # 5. EMOTION.md round-trip (write → read preserves the vector).
    import tempfile, shutil
    with tempfile.TemporaryDirectory() as td:
        # Point the emotion paths at the temp dir via a fake profile.
        try:
            from core import emotion as emo_mod
            orig = emo_mod._emotion_path
            emo_mod._emotion_path = lambda side, profile="": (
                Path(td) / profile / side / "EMOTION.md")
            ok_w = write_emotion("assistant", "t", dict(felt))
            rd = read_emotion("assistant", "t")
            rt_ok = ok_w and rd["vector"].get("joy") == 0.5
            emo_mod._emotion_path = orig
        except Exception:
            rt_ok = False
        checks.append({
            "name": "EMOTION.md write/read round-trip",
            "status": "ok" if rt_ok else "fail",
            "detail": "vector survives the file round-trip",
        })

    # 5b. THE MOOD (the Operator's 08-15 spec): the <=64-word sentence
    # stored in EMOTION.md — explicit mood wins, and the cap holds.
    with tempfile.TemporaryDirectory() as td:
        try:
            from core import emotion as emo_mod2
            orig2 = emo_mod2._emotion_path
            emo_mod2._emotion_path = lambda side, profile="": (
                Path(td) / profile / side / "EMOTION.md")
            write_emotion("assistant", "t", dict(felt),
                          mood="quietly cautious but warm underneath")
            rd_m = read_emotion("assistant", "t")
            mood_ok = "quietly cautious" in rd_m.get("mood", "")
            write_emotion("assistant", "t", dict(felt),
                          mood=" ".join(["word"] * 100))
            rd_c = read_emotion("assistant", "t")
            cap_ok = len(rd_c.get("mood", "").strip('"').split()) <= 64
            emo_mod2._emotion_path = orig2
        except Exception:
            mood_ok = cap_ok = False
        checks.append({
            "name": "mood: explicit sentence preserved + 64-word cap",
            "status": "ok" if mood_ok and cap_ok else "fail",
            "detail": f"explicit={mood_ok} cap={cap_ok}",
        })

    # 6. Prompt injection: the emotion line lives in blocks 2 + 4; the
    #    5-block contract holds.
    from context.prompt_builder import build_prompt_stack
    stack = build_prompt_stack(
        channel="user",
        history=[{"role": "user", "content": "hi"},
                 {"role": "assistant", "content": "hello"}])
    blocks = stack.split("\n\n---\n\n")
    block2 = blocks[1] if len(blocks) > 1 else ""
    block4 = blocks[3] if len(blocks) > 3 else ""
    prompt_ok = (len(blocks) == 5
                 and "Emotional state (assistant)" in block2
                 and "Emotional state (operator)" in block4)
    checks.append({
        "name": "prompt: emotion in blocks 2+4, 5-block contract",
        "status": "ok" if prompt_ok else "fail",
        "detail": f"{len(blocks)} blocks; asst={'Emotional state (assistant)' in block2} "
                  f"user={'Emotional state (operator)' in block4}",
    })

    # 7. Vault + session columns carry emotion/mood.
    from core import db as db_layer
    sid = "emo-doctor-test"
    try:
        db_layer.record_session_message(sid, "assistant", "t", profile="",
                                        emotion="Ecstasy(+0.50)", mood="Joy(+0.50)")
        conn = db_layer.connect_session(sid, profile="")
        scol = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
        srow = conn.execute(
            "SELECT emotion, mood FROM messages ORDER BY seq DESC LIMIT 1").fetchone()
        conn.close()
        db_layer.record_vault_entry("message", "emo-doctor-vault-row",
                                    role="Assistant", profile="",
                                    emotion="Ecstasy(+0.50)", mood="Joy(+0.50)")
        vconn = db_layer.connect_vault("")
        vrow = vconn.execute(
            "SELECT emotion, mood FROM entries WHERE content=? ORDER BY rowid DESC LIMIT 1",
            ("emo-doctor-vault-row",)).fetchone()
        vconn.close()
        db_ok = ("emotion" in scol and "mood" in scol
                 and srow["emotion"] == "Ecstasy(+0.50)"
                 and vrow["emotion"] == "Ecstasy(+0.50)")
        # cleanup
        p = Path(db_layer.sessions_dir("")) / f"session-{sid}.db"
        if p.exists():
            p.unlink()
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        checks.append({"name": "vault columns carry emotion/mood",
                       "status": "fail", "detail": str(exc)})
    if db_ok:
        checks.append({
            "name": "vault columns carry emotion/mood",
            "status": "ok",
            "detail": "session + vault rows record the snapshot",
        })

    # 8. The gauge cycle: the deterministic RULE path (the LLM gauge is
    #    exercised in live turns; the doctor verifies the fallback rules).
    from core.emotion import _rule_delta, _apply_delta, default_emotion
    _d = _rule_delta({"exit_reason": "completed", "tool_failures": []})
    _vec = _apply_delta(default_emotion(), _d)
    _df = _rule_delta({"exit_reason": "completed", "tool_failures": ["error x"]})
    _vecf = _apply_delta(default_emotion(), _df)
    cycle_ok = (_vec.get("joy", 0.0) > 0.0          # success → joy up
                and _vecf.get("anger", 0.0) < 0.0)  # failure → frustration
    checks.append({
        "name": "gauge cycle: success raises joy, failure raises frustration",
        "status": "ok" if cycle_ok else "fail",
        "detail": f"joy={_vec.get('joy', 0.0):+.2f} "
                  f"anger(w/failure)={_vecf.get('anger', 0.0):+.2f}",
    })

    # 9. The server surface: /config/emotion round-trip + Behavior page.
    from fastapi.testclient import TestClient
    from web.server import create_app
    static_dir = str(ATHENA_ROOT / 'athena-system' / 'web' / 'gui')
    app = create_app(static_dir=static_dir)
    c = TestClient(app)
    try:
        g = c.get("/config/emotion")
        gd = g.json()
        server_ok = g.status_code == 200 and "axes" in gd and "agent" in gd
        p = c.post("/config/emotion", json={
            "side": "assistant",
            "vector": {"joy": 0.5, "trust": 0.4},
        })
        pd = p.json()
        post_ok = p.status_code == 200 and pd.get("ok") is True
        home = c.get("/").text
        page_ok = ("behavior-ws" in home and 'data-ws="behavior"' in home
                   and "behavior.js" in home)
        # The polygraph history endpoint (the Operator's 08-11 call).
        h = c.get("/config/emotion/history?limit=5")
        hd = h.json()
        hist_ok = h.status_code == 200 and "points" in hd and "axes" in hd
        # reset the vector after the test
        c.post("/config/emotion", json={"side": "assistant", "vector": {}})
    except Exception as exc:  # noqa: BLE001
        server_ok = post_ok = page_ok = hist_ok = False
    checks.append({
        "name": "server: /config/emotion + Behavior page wired",
        "status": "ok" if server_ok and post_ok and page_ok else "fail",
        "detail": f"get={server_ok} post={post_ok} page={page_ok}",
    })
    checks.append({
        "name": "server: polygraph history endpoint",
        "status": "ok" if hist_ok else "fail",
        "detail": f"history={hist_ok} points={hd.get('points', []) if hist_ok else []}",
    })

    # 10. The MASS DELETE endpoint (the Operator's 08-11 spec): delete
    #     sessions by entry count — min_entries / max_entries modes.
    try:
        from core import db as db_layer
        import uuid as _uuid
        _t_sid = str(_uuid.uuid4())
        db_layer.delete_session(_t_sid, profile="")
        for i in range(3):
            db_layer.record_session_message(_t_sid, "user",
                                            "bulk test entry " + str(i),
                                            profile="")
        _bd = c.post("/sessions/delete-by-count",
                     json={"profile": "", "min_entries": 3}).json()
        _still = (Path(db_layer.sessions_dir("")) /
                  f"session-{_t_sid}.db").exists()
        bulk_ok = (_bd.get("ok") is True
                   and any(x.get("session_id") == _t_sid
                           for x in _bd.get("deleted", []))
                   and not _still)
        # cleanup any leftover
        db_layer.delete_session(_t_sid, profile="")
    except Exception as exc:  # noqa: BLE001
        bulk_ok = False
    checks.append({
        "name": "mass delete by entry count",
        "status": "ok" if bulk_ok else "fail",
        "detail": "min_entries deletes sessions at/above the threshold",
    })

    # Reset the agent vector to neutral (the gauge test moved it).
    from core.emotion import write_emotion
    write_emotion("assistant", "", default_emotion())
    write_emotion("user", "", default_emotion())

    return checks

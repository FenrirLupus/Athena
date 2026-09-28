"""Nurse profile test — the dot-prefix convention (the Operator's spec).

Dot-prefixed profiles (e.g. .nurse) are SYSTEM-based profiles: they
operate ONLY inside athena-system/ and use provider calls to diagnose and
repair carefully. Regular identity profiles have NO dot. The convention
is enforced: a system profile must be dot-prefixed, and the nurse profile
must exist.
"""
from __future__ import annotations


def run() -> list[dict]:
    from pathlib import Path
    from intelligence.profiles import list_profiles, get_profile, create_profile
    from doctor.nurse import NURSE_AGENT, NURSE_PROFILE, REPAIR_ZONE

    checks = []
    profiles = list_profiles()
    names = [p.name for p in profiles]

    # 1. The nurse profile exists and is dot-prefixed.
    nurse = get_profile(NURSE_PROFILE)
    checks.append({
        "name": ".nurse profile exists (dot-prefixed)",
        "status": "ok" if nurse is not None
        and NURSE_PROFILE.startswith(".") else "fail",
        "detail": NURSE_PROFILE if nurse else "missing",
    })
    # 2. Regular profiles have no dot, system profiles DO (the doctor's
    #    test is SELF-CONTAINED: it creates a temp regular profile to
    #    verify the classification, then DELETES it — the system is left
    #    exactly as found; no test profile survives. Runs in an ISOLATED
    #    tempdir so the born profile never touches the real profiles tree.
    import shutil
    import tempfile
    import uuid
    import intelligence.profiles as iprof
    import core.config as cfg_mod
    original_profiles_dir = iprof.PROFILES_DIR
    original_root = cfg_mod.ATHENA_ROOT
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        iprof.PROFILES_DIR = tmp / "profiles"
        cfg_mod.ATHENA_ROOT = tmp
        try:
            tmp_name = f"doctor-verify-{uuid.uuid4().hex[:8]}"
            create_profile(tmp_name)
            names_now = [p.name for p in list_profiles()]
            regular = [n for n in names_now if n != "default" and not n.startswith(".")]
            dot_sys = [n for n in names_now if n != "default" and n.startswith(".")]
            p = get_profile(tmp_name)
            ok_class = tmp_name in regular and bool(dot_sys)
            # CLEANUP: the temp profile is deleted — the test leaves no trace.
            if p is not None:
                shutil.rmtree(p.root, ignore_errors=True)
            names_after = [n.name for n in list_profiles()]
            cleaned = tmp_name not in names_after
            checks.append({
                "name": "dot = system, no-dot = identity (self-contained)",
                "status": "ok" if ok_class and cleaned else "fail",
                "detail": f"regular={regular} system={dot_sys} cleaned={cleaned}",
            })
        finally:
            iprof.PROFILES_DIR = original_profiles_dir
            cfg_mod.ATHENA_ROOT = original_root
    # 3. The nurse agent identity maps to the system profile.
    checks.append({
        "name": "nurse agent → .nurse profile",
        "status": "ok" if NURSE_AGENT == "nurse" and NURSE_PROFILE == ".nurse" else "fail",
        "detail": f"{NURSE_AGENT} → {NURSE_PROFILE}",
    })
    # 4. The nurse's repair zone is athena-system/ (the sanctum).
    checks.append({
        "name": "nurse repair zone = athena-system/",
        "status": "ok" if str(REPAIR_ZONE).endswith("athena-system") else "fail",
        "detail": str(REPAIR_ZONE),
    })
    return checks

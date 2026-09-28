"""Profiles test — registry + per-profile data routing (DYNAMIC).

Tests any profile the system has: picks a non-default profile if one
exists, else verifies the default alone. Never hardcodes a name — the
doctor must test whatever profiles are present.
"""
from __future__ import annotations


def run() -> list[dict]:
    from pathlib import Path
    from intelligence.profiles import get_profile, list_profiles, create_profile

    checks = []
    profiles = list_profiles()
    names = [p.name for p in profiles]
    # The DYNAMIC profile under test: the first non-default one, else none.
    named = next((p for p in profiles if not p.is_default), None)
    target = named.name if named else "default"

    checks.append({
        "name": "profile list non-empty",
        "status": "ok" if profiles else "fail",
        "detail": f"{names}",
    })
    checks.append({
        "name": "named profile resolves (dynamic)",
        "status": "ok" if (named is not None) or (len(profiles) == 1) else "fail",
        "detail": f"target={target}",
    })
    if named is not None:
        p = get_profile(target)
        checks.append({
            "name": "named profile has a root",
            "status": "ok" if p and p.root else "fail",
            "detail": str(p.root if p else "none"),
        })
    checks.append({
        "name": "default is root",
        "status": "ok" if get_profile("default").is_default else "fail",
        "detail": str(get_profile("default").root),
    })

    # Create → verify layout → clean up. Runs in an ISOLATED tempdir so
    # the born profile never touches the real profiles tree (the doctor's
    # isolation rule: a test that creates state must contain it).
    import tempfile
    import intelligence.profiles as iprof
    import core.config as cfg_mod
    import uuid as _uuid
    original_profiles_dir = iprof.PROFILES_DIR
    original_root = cfg_mod.ATHENA_ROOT
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        iprof.PROFILES_DIR = tmp / "profiles"
        cfg_mod.ATHENA_ROOT = tmp
        try:
            test_agent = f"doctor-test-agent-{_uuid.uuid4().hex[:8]}"
            result = create_profile(test_agent)
            p = get_profile(test_agent)
            checks.append({
                "name": "create_profile builds layout",
                "status": "ok" if p and (p.root / "assistant").exists()
                else "fail",
                "detail": str(result) if isinstance(result, str)
                else str(p.root if p else "none"),
            })
        finally:
            iprof.PROFILES_DIR = original_profiles_dir
            cfg_mod.ATHENA_ROOT = original_root
    return checks

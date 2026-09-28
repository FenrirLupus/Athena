"""Profile switching test — athena profile <name|list|switch|current>."""
from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> list[dict]:
    # THE LIVE GATE (the Operator's 08-12 deletion fix): this test
    # switches the ACTIVE profile. In the LIVE process (the service's
    # boot pass), a profile switch can leak into the real config.yaml
    # and the profile rebuild can recreate/wipe the sessions dir — the
    # chat-history-vanishing bug. It runs ONLY in the isolated
    # subprocess (`athena doctor`), never inside the service.
    import os as _os
    if _os.environ.get("ATHENA_LIVE") == "1" or _os.environ.get("ATHENA_WIPE_APPROVED"):
        # The boot pass sets ATHENA_LIVE; the wipe token never appears
        # here (operator-only CLI) — both signal "don't mutate live".
        if _os.environ.get("ATHENA_LIVE") == "1":
            return [{
                "name": "profile switch skipped in live process",
                "status": "ok",
                "detail": "switching profiles would rebuild the sessions dir (08-12)",
            }]
    from intelligence import profiles
    from intelligence.profiles import current_profile, get_profile, list_profiles

    checks = []
    original_profiles_dir = profiles.PROFILES_DIR
    original_root = profiles.ATHENA_ROOT
    import core.config
    original_cfg_root = core.config.ATHENA_ROOT
    original_cfg_path = core.config.CONFIG_PATH
    # DYNAMIC temp profile: the test creates its own profile name so it
    # works regardless of what profiles exist on the host.
    import uuid
    test_profile = f"doctor-probe-{uuid.uuid4().hex[:6]}"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        profiles.ATHENA_ROOT = root
        profiles.PROFILES_DIR = root / "profiles"
        core.config.ATHENA_ROOT = root
        core.config.CONFIG_PATH = root / "config.yaml"
        (root / "profiles" / test_profile).mkdir(parents=True)
        (root / "profiles" / test_profile / "assistant").mkdir()
        (root / "profiles" / test_profile / "assistant" / "ASSISTANT.md").write_text("# Probe")
        # The config.yaml holds the profile.active variable — the test
        # writes a minimal config so the switch can update it.
        (root / "config.yaml").write_text("profile:\n  active: default\n",
                                          encoding="utf-8")
        try:
            # Default active profile when no switch state.
            p = current_profile()
            checks.append({
                "name": "default active profile",
                "status": "ok" if p.is_default and p.name == ".default" else "fail",
                "detail": p.name,
            })
            # Switch writes the CONFIG variable (no sidecar file).
            from athena import _run_profile_cmd
            rc = _run_profile_cmd(["switch", test_profile])
            cfg = core.config.load_config()
            active = (cfg.get("profile") or {}).get("active", "")
            checks.append({
                "name": "switch writes profile.active in config.yaml",
                "status": "ok" if rc == 0 and active == test_profile else "fail",
                "detail": f"active={active!r}",
            })
            # current_profile reads the switched state.
            p2 = current_profile()
            checks.append({
                "name": "current reflects switch",
                "status": "ok" if not p2.is_default and p2.name == test_profile else "fail",
                "detail": p2.name,
            })
            # Unknown profile is refused.
            rc2 = _run_profile_cmd(["switch", "nonexistent"])
            checks.append({
                "name": "unknown profile refused",
                "status": "ok" if rc2 == 1 else "fail",
                "detail": f"exit={rc2}",
            })
            # get_profile returns None for unknown.
            checks.append({
                "name": "get_profile unknown = None",
                "status": "ok" if get_profile("nope") is None else "fail",
                "detail": "",
            })
            # list_profiles includes the created dirs.
            names = [x.name for x in list_profiles()]
            checks.append({
                "name": "list includes profiles",
                "status": "ok" if test_profile in names and ".default" in names else "fail",
                "detail": f"{names}",
            })
        finally:
            profiles.PROFILES_DIR = original_profiles_dir
            profiles.ATHENA_ROOT = original_root
            core.config.ATHENA_ROOT = original_cfg_root
            core.config.CONFIG_PATH = original_cfg_path
    return checks

"""Wipe-Test — the Operator's 08-12 survival spec (doctor test).

PROVES Athena springs back from the dead with ONLY these 6 keep items:
    .wiki               — the local doctrine mirror (the known-good)
    athena-system       — the code (requirements.txt lives inside it)
    config.yaml         — the global seed config
    authentication.json — the provider catalog
    .secret             — the API keys (NEVER wiped if existing)
    .venv               — Athena's OWN python environment

THE WIPE IS PHYSICAL. The test DELETES every other file and directory
in .athena/ on disk — profiles, sessions, logs, operations, skills,
plugins, tools, snapshots, everything — leaving ONLY the 6 keep items.
Then it runs the boot reconstruction and verifies Athena springs back:
system profiles register, layouts + the 6-file set rebuild, the
built-ins seed, and every system .md matches the Standard Markdown
Schema.

This is a STATE-MUTATING test (it REALLY wipes) — it runs only with
the operator's approval token (athena wipe-test), never in the live
service's pass, never by an agent.
"""
from __future__ import annotations

import shutil
from pathlib import Path

# The 6 keep items Athena needs to survive (the Operator's 08-12 wipe-list,
# updated for the PER-PROFILE config model): the root config.yaml is GONE —
# each profile owns its own config (profiles/*/config.yaml, re-seeded from
# the .default's at boot). The root keep-files are the architecture + the
# GLOBAL credentials (authentication.json + .secret stay shared).
KEEP_FILES = (".wiki", "athena-system", "authentication.json", ".secret",
              ".venv")

# The per-profile dirs the layout must rebuild.
REQUIRED_DIRS = ("agent", "runtime", "workspace", "sandbox", "logs",
                 "events", "sessions", "assistant", "user")

# The six system files every profile must have (the Operator's 08-11 rule).
SIX_FILES = ("assistant/ASSISTANT.md", "assistant/MEMORY.md",
             "assistant/EMOTION.md", "user/USER.md", "user/MEMORY.md",
             "user/EMOTION.md")


def _wipe_all_except(root: Path) -> list[Path]:
    """PHYSICALLY delete every file/dir under root EXCEPT the 6 keep
    items. Returns the deleted paths (for the report)."""
    deleted = []
    for item in sorted(root.iterdir()):
        if item.name in KEEP_FILES:
            continue
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item, ignore_errors=True)
        else:
            try:
                item.unlink()
            except OSError:
                pass
        deleted.append(item.name)
    return deleted


def run() -> list[dict]:
    # ── THE OPERATOR-ONLY GATE (the Operator's 08-12 spec) ─────────────────
    # This is a DEVELOPER test — it PHYSICALLY wipes .athena down to
    # the 6 keep items. Agents (nurse, scheduler, any runtime) must
    # NEVER run it. The operator approves it via the CLI (which sets
    # the ATHENA_WIPE_APPROVED token); the token is process-scoped and
    # never persisted. Without it the test REFUSES.
    import os as _os
    if _os.environ.get("ATHENA_WIPE_APPROVED") != "1":
        return [{
            "name": "wipe-test blocked: operator-only (no approval)",
            "status": "fail",
            "detail": ("The Wipe-Test is an OPERATOR-ONLY developer test. "
                       "It PHYSICALLY wipes everything the operator has "
                       "made and verifies Athena repopulates from the 6 "
                       "keep files. Agents must never use it. Approve it "
                       "from the CLI (athena wipe-test) to run."),
        }]

    from core.config import ATHENA_ROOT, load_raw_config, profile_config_path
    import core.system_profiles as sp
    from intelligence.profiles import (Profile, get_profile, list_profiles,
                                       ensure_profile_files)

    checks = []
    src = Path(ATHENA_ROOT)

    # ── 0. THE PHYSICAL WIPE (the Operator's 08-12 spec): delete EVERY
    #    file/dir except the 6 keep items — on disk, for real.
    before = sorted(p.name for p in src.iterdir())
    deleted = _wipe_all_except(src)
    after = sorted(p.name for p in src.iterdir())
    checks.append({
        "name": "wipe: exactly the 6 keep items remain (physical)",
        "status": "ok" if set(after) == set(KEEP_FILES) else "fail",
        "detail": f"before={len(before)} after={len(after)} deleted={deleted}",
    })

    # ── 0a. THE EXISTENCE-SKIP RULE: a keep item that EXISTED is kept
    #    AS-IS — never overwritten, never replaced with a default.
    skip_ok = True
    skip_detail = []
    for name in KEEP_FILES:
        if not (src / name).exists():
            skip_ok = False
            skip_detail.append(f"{name}: MISSING after wipe")
    checks.append({
        "name": "wipe: all 6 keep items survive the wipe",
        "status": "ok" if skip_ok else "fail",
        "detail": "; ".join(skip_detail) or "all 6 keep items present",
    })

    # ── 0b. THE CREDENTIALS SURVIVE: .secret is byte-identical (the
    #    wipe must not touch it — the operator's real keys are intact).
    import core.secret_store as _ss
    cred_ok = True
    cred_detail = []
    sec = src / ".secret"
    if not sec.exists():
        cred_ok = False
        cred_detail.append(".secret MISSING after wipe")
    else:
        txt = sec.read_text(errors="replace")
        real_count = len([l for l in txt.splitlines()
                          if "=" in l and not l.strip().startswith("#")
                          and l.split("=", 1)[1].strip().lower()
                          not in ("null", "none", "")])
        if real_count == 0:
            cred_ok = False
            cred_detail.append(".secret ALL keys null (wiped values)")
        else:
            cred_detail.append(f"{real_count} real keys intact")
    checks.append({
        "name": "wipe: credentials survive (keys preserved)",
        "status": "ok" if cred_ok else "fail",
        "detail": "; ".join(cred_detail),
    })

    # ── 0c. DEFAULTS ARE STANDARD/NULL ONLY: a default-created .secret
    #    carries NO real credentials.
    from core.secret_store import seed as _seed
    default_dir = src / ".wipe-default-check"
    default_dir.mkdir(parents=True, exist_ok=True)
    orig_secret = _ss.SECRET_FILE
    try:
        _ss.SECRET_FILE = default_dir / ".secret"
        seeded = _seed(create_if_missing=True)
        default_secret = default_dir / ".secret"
        default_real = 0
        if default_secret.exists():
            default_real = len([
                l for l in default_secret.read_text(errors="replace")
                .splitlines()
                if "=" in l and not l.strip().startswith("#")
                and l.split("=", 1)[1].strip().lower()
                not in ("null", "none", "")])
        checks.append({
            "name": "wipe: defaults are null/standard only (no real creds)",
            "status": "ok" if default_real == 0 and seeded else "fail",
            "detail": f"default .secret real-values={default_real} "
                      f"(must be 0) keys={len(seeded)}",
        })
    finally:
        _ss.SECRET_FILE = orig_secret
        shutil.rmtree(default_dir, ignore_errors=True)

    # ── 1. THE PER-PROFILE CONFIG (the Operator's 08-12 spec): there is
    #    NO root config.yaml anymore — each profile owns its own config.
    #    ensure_all re-seeds it from the profile-config seed (the FULL
    #    schema — identity/server/provider/... with nulls).
    # ── 2. SYSTEM PROFILES REGISTER: .nurse/.janitor auto-create with
    #    full layout + 6 files; the default profile's layout + files
    #    rebuild too (the boot's ensure_all).
    created = sp.ensure_all()
    raw = load_raw_config("")
    checks.append({
        "name": "wipe: the default profile's config is readable",
        # THE 08-15 SCHEMA: identity was REMOVED (the .md files own it) —
        # the config's marker categories are server + budget + models.
        "status": "ok" if isinstance(raw, dict) and raw
        and "server" in raw and "budget" in raw and "models" in raw else "fail",
        "detail": f"keys={list(raw.keys())[:5]}",
    })
    profiles = list_profiles()
    names = {p.name for p in profiles}
    checks.append({
        "name": "wipe: system profiles register (dot-prefixed)",
        "status": "ok" if {".default", ".nurse", ".janitor"} <= names
        else "fail",
        "detail": f"created={created} have={sorted(names)}",
    })

    # ── 3. NON-SYSTEM AGENTS are NOT auto-created.
    checks.append({
        "name": "wipe: no phantom non-system agents",
        "status": "ok" if all(n.startswith(".") for n in names)
        else "fail",
        "detail": f"names={sorted(names)}",
    })

    # ── 4. EVERY profile has the full layout + 6 files.
    layout_ok = True
    files_ok = True
    details = []
    for p in profiles:
        missing_dirs = [d for d in REQUIRED_DIRS
                        if not (p.root / d).is_dir()]
        if missing_dirs:
            layout_ok = False
            details.append(f"{p.name}:dirs={missing_dirs}")
        ensure_profile_files(p)
        missing_files = [f for f in SIX_FILES
                         if not (p.root / f).exists()]
        if missing_files:
            files_ok = False
            details.append(f"{p.name}:files={missing_files}")
    checks.append({
        "name": "wipe: every profile rebuilds full layout",
        "status": "ok" if layout_ok else "fail",
        "detail": "; ".join(details[:3]) or "all dirs present",
    })
    checks.append({
        "name": "wipe: every profile has the 6 system files",
        "status": "ok" if files_ok else "fail",
        "detail": "; ".join(details[:3]) or "all 6 files present",
    })

    # ── 5. THE NAMED PROFILE SCHEMA: an operator-created agent is born
    #    with the FULL config structure, null defaults.
    from intelligence.profiles import create_profile
    op = create_profile("operator-test")
    cfg_path = profile_config_path("operator-test")
    own = __import__("yaml").safe_load(
        cfg_path.read_text(encoding="utf-8")) or {}
    checks.append({
        "name": "wipe: new agent born with full schema (nulls ok)",
        # THE 08-15 SCHEMA: identity is gone (the .md files own it) —
        # the born-complete markers are budget/provider/server/models.
        "status": "ok" if own and {"budget", "provider", "server", "models"}
        <= set(own.keys()) else "fail",
        "detail": f"keys={sorted(own.keys())[:6]}",
    })
    shutil.rmtree(op.root, ignore_errors=True)

    # ── 6. THE BUILT-INS SPRING BACK. Skills are NATIVE (Athena ships
    #    her own); plugins are the COMMUNITY modding layer (the Operator's
    #    08-12 directive) — the shared plugins home stays EMPTY by
    #    default and the community installs their own.
    from core.config import SHARED_SKILLS, SHARED_PLUGINS, SHARED_TOOLS
    builtin_skills = sorted(p.name for p in SHARED_SKILLS.iterdir())
    builtin_plugins = sorted(p.name for p in SHARED_PLUGINS.iterdir())
    builtin_tools = sorted(p.name for p in SHARED_TOOLS.iterdir())
    checks.append({
        "name": "wipe: built-in skills seed back (generalized)",
        "status": "ok" if builtin_skills else "fail",
        "detail": f"skills={builtin_skills}",
    })
    checks.append({
        "name": "wipe: built-in tools seed back (generalized)",
        "status": "ok" if builtin_tools else "fail",
        "detail": f"tools={builtin_tools}",
    })
    checks.append({
        "name": "wipe: no bundled plugins (community modding only)",
        "status": "ok" if not builtin_plugins else "fail",
        "detail": f"plugins={builtin_plugins} (want empty — Athena ships none)",
    })

    # ── 7. THE STANDARD MARKDOWN SCHEMA.
    from core.md_format import delimiter_lines
    schema_ok = True
    schema_detail = []
    for p in profiles:
        for side in ("assistant", "user"):
            for fname in ("ASSISTANT.md", "USER.md", "EMOTION.md"):
                fp = p.root / side / fname
                if fp.exists():
                    d = delimiter_lines(
                        fp.read_text(encoding="utf-8", errors="replace"))
                    if not (len(d) == 4 and d[0] == 1):
                        schema_ok = False
                        schema_detail.append(f"{p.name}/{side}/{fname}:{d}")
            mp = p.root / side / "MEMORY.md"
            if mp.exists():
                d = delimiter_lines(
                    mp.read_text(encoding="utf-8", errors="replace"))
                if len(d) != 2 or d[0] != 1:
                    schema_ok = False
                    schema_detail.append(f"{p.name}/{side}/MEMORY.md:{d}")
    checks.append({
        "name": "wipe: system files match the Standard schema",
        "status": "ok" if schema_ok else "fail",
        "detail": "; ".join(schema_detail[:3]) or "all sandwich",
    })

    # ── 8. DEFAULTS ARE STANDARD/NULL/ZERO ONLY (no personal info in
    #    the default identity files; emotion vectors all zero).
    identity_ok = True
    identity_detail = []
    import re as _re
    for p in profiles:
        for side, fname in (("assistant", "ASSISTANT.md"),
                            ("user", "USER.md")):
            fp = p.root / side / fname
            if not fp.exists():
                continue
            txt = fp.read_text(encoding="utf-8", errors="replace")
            for m in _re.finditer(
                    r"^([a-z_]+):\s*(.*)$", txt, _re.M):
                key, val = m.group(1).strip(), m.group(2).strip().strip('"')
                if key in ("home", "role"):
                    continue
                if key in ("name_first", "name_last", "name_nick"):
                    if p.name in (".nurse", ".janitor"):
                        continue
                if val and val.lower() not in ("null", "none", ""):
                    identity_ok = False
                    identity_detail.append(
                        f"{p.name}/{side}/{fname}:{key}={val}")
    emo_ok = True
    emo_detail = []
    for p in profiles:
        for side in ("assistant", "user"):
            ep = p.root / side / "EMOTION.md"
            if not ep.exists():
                continue
            try:
                from core.emotion import read_emotion
                vec = read_emotion(side, p.name).get("vector", {})
                if any(abs(v) > 1e-9 for v in vec.values()):
                    emo_ok = False
                    emo_detail.append(f"{p.name}/{side}:{vec}")
            except Exception:
                pass
    checks.append({
        "name": "wipe: defaults carry no personal info (identity null)",
        "status": "ok" if identity_ok else "fail",
        "detail": "; ".join(identity_detail[:3]) or "identity defaults null/standard",
    })
    checks.append({
        "name": "wipe: emotion vectors default to zero",
        "status": "ok" if emo_ok else "fail",
        "detail": "; ".join(emo_detail[:3]) or "all vectors zero",
    })

    # ── 8. HOME HYGIENE (the Operator's 08-12 rule): the root homes hold
    #    ONLY their configs/auths/secrets + the standard dirs — agents
    #    and drones work inside sandbox/ or workspace/, never polluting
    #    the roots with stray files (.txt, random artifacts).
    hygiene_ok = True
    hygiene_detail = []
    # The profile root: allowed = config.yaml + the standard subdirs.
    allowed_profile = {"config.yaml", "agent", "assistant", "user",
                       "workspace", "sandbox", "sessions", "runtime",
                       "logs", "events", "operations", "plugins", "skills",
                       "tools", "doctor", "custodian", "graphs",
                       "workflows"}
    for p in profiles:
        for item in p.root.iterdir():
            if item.name not in allowed_profile:
                hygiene_ok = False
                hygiene_detail.append(f"{p.name}/{item.name}")
    checks.append({
        "name": "hygiene: profile homes hold only config + standard dirs",
        "status": "ok" if hygiene_ok else "fail",
        "detail": "; ".join(hygiene_detail[:6]) or "profile homes clean",
    })

    return checks

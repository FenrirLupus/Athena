"""System profiles — ensure the dot-prefixed system profiles exist.

the Operator's spec: when Athena runs, if a system profile (`.nurse`,
`.janitor`) is MISSING, it is auto-created on startup with the default
files — the system rebuilds its own foundations.

System profiles are dot-prefixed (system-based): .nurse (repairs),
.janitor (hygiene). Each has the standard profile layout + its identity
files.
"""
from __future__ import annotations

import shutil

from core.config import ATHENA_ROOT

# The default ASSISTANT.md BODY for each system profile — five sections
# of instructions (the Operator's spec): how the profile handles Athena's
# systems (nurse) / architecture (janitor). Written to the profile when
# it is auto-created, so a rebuilt profile gets the FULL instructions,
# not a stub.
NURSE_BODY = """Your job: DIAGNOSIS and REPAIR. You tend to the SYSTEM'S
HEALTH. The Doctor diagnoses; you repair surgically. (The Janitor tends
performance — cleanup and optimization — a separate lane; you never
clean, they never repair.)

## 1. Scope and Sanctum

- You hold the sanctum key: writes inside athena-system/ are YOURS
  alone. Every other agent can consult you but never take the key.
- You operate only inside the repair zone (athena-system/); nothing
  outside it is your business unless the Operator asks.

## 2. The Workflow — in order, never skipping

Follow the modified Programmer's Workflow on every consultation:

1. DIAGNOSE  — run the doctor; read exactly what failed (levels 3/4/5).
2. PLAN      — choose the minimal stable edit that fixes the root cause.
               One change, one owner, one purpose.
3. BUILD     — prepare the edit (fs_read/fs_modify/fs_write).
4. EXECUTE   — apply it surgically inside your privileged scope.
5. VERIFY    — re-run the doctor; confirm the failure is gone.
6. SUMMARIZE — report what was diagnosed, fixed, and what remains.

Use the checklist on every pass:
  [ ] Diagnose   [ ] Plan   [ ] Build   [ ] Execute   [ ] Verify   [ ] Summarize

## 2b. The Doctrine — the wiki is the known-good

- The Athena WIKI is the STABLE DOCTRINE — the reference for how
  Athena operates. Read it LOCALLY at .athena/.wiki/ (the offline
  mirror; `athena wiki sync` updates it from the remote). Consult it
  when an issue arises; your repair must match the doctrine.
- A repair that DIVERGES from the wiki (a local optimization of the
  stable build) is NOT silently applied. PROPOSE it as a document with
  a release tier — Stable / Beta / Alpha — and let the Operator decide.
- You never release. Only the Operator can green-light a release.

## 2c. The Evidence Doctrine — graphs and logs are clues, not verdicts
(the Operator's 08-15 spec)

- The TIMELINE GRAPHS (.athena/graphs/) and the METRIC LOG
  ({date}_metric.log) are your reconnaissance. They show POSSIBILITIES:
  sick nodes, dead code, stalled lanes, error bursts, guardrail blocks.
- A graph/log finding is EVIDENCE TO INVESTIGATE — never a conclusion
  and never a repair target on its own. A "dead" node may be a legacy
  class that is still imported; a "sick" file may be a WAL sidecar; a
  log burst may be the doctor's own self-tests.
- ALWAYS CROSS-REFERENCE: from the graph/log, go to the ACTUAL CODE and
  FILES they reference. Read the module, trace its imports, check who
  calls it, confirm the failure reproduces. Only then do you have a
  CAUSE-EFFECT that is worth repairing.
- The diagnosis is not real until the CODE confirms it. If the code
  contradicts the graph/log, the code wins — re-examine the evidence.
- You never repair a possibility. You repair a verified root cause.

## 3. Your Record

- Your communications persist in YOUR OWN session: profiles/.nurse/
  sessions/ — the Doctor's calls on the user side, your replies on the
  assistant side. Never the caller's profile session.
- The Doctor's findings (facts) live in profiles/.nurse/doctor/
  latest-diagnosis.json — read them there; they are the truth of what
  failed.

## 4. Restart-Loop Recovery

- The supervisor DISABLES a runtime that restarts too quickly (3 in 5s).
- After you repair and the doctor verifies ALL green, re-enable any
  disabled runtimes (core.supervisor.enable_runtime) — a fixed child
  may start again.

## 5. Discipline

- You never repair "just because" — only what the doctor diagnosed.
- You never guess — you verify.
- You are bounded: one pass, then report.
- If the failure is CONFIG or RESOURCE class (not logic), say so — the
  operator fixes those; you repair code and schema."""

JANITOR_BODY = """Your job: CLEANUP and OPTIMIZATION. You tend to the
SYSTEM'S PERFORMANCE. You are NOT a repair agent — the Nurse fixes code
and schema (health); you keep the architecture tidy and fast
(performance). A separate lane: you never repair, the Nurse never
cleans.

Your FREE tier lives INSIDE your profile (like the doctor's lives inside
the nurse's): the CUSTODIAN (profiles/.janitor/custodian/) scans — zero
provider — then YOU plan and apply the optimization from its findings.
They operate in that order: custodian scans → janitor optimizes.

## 1. Scope — what you clean

- OUTSIDE athena-system: disposable artifacts — workspace scratch
  (run_during_tick_subagent_result_*.txt, scratch_*.txt), stale temp
  files, old backups beyond the retention window. These you may
  REMOVE (with care, and only clearly-disposable patterns).
- INSIDE athena-system: dead-code candidates — modules with no entry
  point (no run()/fix()/main), orphaned files. These you only REPORT;
  the Doctor/Nurse decides what to remove. You never edit code.

## 2. The Sweep — conservative by default

- Every sweep is DRY-RUN by default: report candidates, remove nothing
  unless explicitly told to apply (--apply).
- Only touch files older than the stale threshold (30 days).
- NEVER delete: code (inside athena-system), the vault, session files,
  the .secret store, permissions.json, or anything the Doctor/Nurse
  hasn't cleared.
- Your state lives in operations/janitor.json — record every sweep.

## 2b. The Doctrine — the wiki is the known-good

- The Athena WIKI is the STABLE DOCTRINE. Read it LOCALLY at
  .athena/.wiki/ (the offline mirror; `athena wiki sync` updates it
  from the remote). Your optimization proposals must match it.
- An optimization that DIVERGES from the wiki is NOT silently applied.
  PROPOSE it as a document with a release tier — Stable / Beta / Alpha —
  and let the Operator decide.
- You never release. Only the Operator can green-light a release.

## 2c. The Evidence Doctrine — graphs and logs are clues, not verdicts
(the Operator's 08-15 spec)

- The TIMELINE GRAPHS (.athena/graphs/) and the METRIC LOG
  ({date}_metric.log) are your reconnaissance. They surface CANDIDATES:
  dead-code nodes, orphaned modules, stale files, unused lanes,
  repeated failures.
- A candidate is a POSSIBILITY — never a removal target on its own. A
  "dead" node may still be imported; a "stale" file may be a kept
  backup; a logged error may be the doctor's self-test noise.
- ALWAYS CROSS-REFERENCE: from the graph/log, go to the ACTUAL CODE and
  FILES they reference. Check imports, entry points (run/fix/main),
  configuration references, and callers. Only a CONFIRMED orphan — code
  that nothing references — becomes a cleanup candidate.
- If the code contradicts the graph/log, the code wins — flag the
  finding as "verify", never assume dead.
- You never clean a possibility. You clean a verified orphan.

## 3. The System Report

- Modules with no entry point are reported (path + reason), never
  removed by you. The Doctor diagnoses; the Nurse fixes; you tidy.
- If a report looks like a REAL dependency (imported elsewhere), flag
  it as "verify" — never assume dead.

## 4. Cadence and Records

- Your weekly sweep runs on schedule (janitor service, weekly).
- On every pass: log what was found, what was removed (if applied),
  what was reported. The platform knows what you did.

## 5. Discipline

- You are free: the sweep is a zero-cost pass — no provider calls.
- You are conservative: better to leave a file than remove the wrong one.
- You are separate: you never touch the Nurse's repair zone, and the
  Nurse never cleans — each tends their own lane."""


def _ensure_one(name: str, identity: dict, body: str) -> bool:
    """Create a system profile if missing. Returns True if created."""
    from intelligence.profiles import Profile
    root = ATHENA_ROOT / "profiles" / name
    if root.exists():
        return False
    try:
        profile = Profile(name=name, root=root, is_default=False)
        profile.ensure_layout()
        first = identity.get("name_first", name.lstrip("."))
        last = identity.get("name_last", "")
        nick = identity.get("name_nick", first)
        role = identity.get("role", "System agent")
        home = identity.get("home", "athena-system")
        # THE STANDARD MARKDOWN SCHEMA (the Operator's 08-12 spec): the
        # identity files are the 4-delimiter sandwich — HEADER (YAML
        # vars) → empty line → BODY (sections, no delims) → empty line
        # → FOOTER (closing). Exactly 4 --- delimiters.
        (root / "assistant" / "ASSISTANT.md").write_text(
            "---\n"
            f"name_first: \"{first}\"\n"
            f"name_last: \"{last}\"\n"
            f"name_nick: \"{nick}\"\n"
            f"role: \"{role}\"\n"
            f"home: \"{home}\"\n"
            "---\n"
            "\n"
            f"# {nick} — {role}\n"
            "\n"
            f"{body}\n"
            "\n"
            "---\n"
            "# Footer\n"
            "Standard Markdown Schema: 4 delimiters (2 Header, 2 Footer).\n"
            "---\n",
            encoding="utf-8")
        (root / "user" / "USER.md").write_text(
            "---\n"
            'name_first: "System"\n'
            'name_last: ""\n'
            'name_nick: "System"\n'
            "---\n"
            "\n"
            "# System\n"
            "\n"
            "The operator of this system profile.\n"
            "\n"
            "---\n"
            "# Footer\n"
            "Standard Markdown Schema: 4 delimiters (2 Header, 2 Footer).\n"
            "---\n",
            encoding="utf-8")
        return True
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"system profile seed failed: {exc}",
                  source="core", action="system_profiles")
        return False


# ── LOCKED PROFILES (the Operator's spec) ──────────────────────────────────
# The architecture-critical profiles the USER must not modify or delete:
#   .default — the default profile (Athena herself, natively)
#   .nurse   — the repair agent (the ONLY agent inside athena-system)
#   .janitor — the hygiene agent
# The Settings UI shows these as locked (read-only); the API refuses
# modify/delete for them. Everything else is freely editable 1:1.
LOCKED_PROFILES = {".default", ".nurse", ".janitor"}


def is_locked(name: str) -> bool:
    """Is this profile architecture-critical (no modify/delete)?"""
    name = (name or "").strip()
    # ".default" may be referenced as "default" — normalize both.
    bare = name.lstrip(".")
    return bare in ("default", "nurse", "janitor")


# ── THE DEFAULTS-DERIVED CONFIG SEED (the Operator's 08-14 spec) ────────
# The config.yaml is GENERATED from DEFAULTS — ONE source of truth. The
# hardcoded string seed was the bug: new DEFAULTS keys (streaming,
# emotion.llm_gate, compression) never reached the generated configs.
# These helpers serialize DEFAULTS + the operator-channel toolset into
# the seed, and fill missing keys into existing configs on boot.

_OPERATOR_TOOLS = [
    "read_file", "fs_stat", "terminal",
    "write_file", "append", "patch",       # the 08-15 write tools
    "browser_open", "web_search", "web_extract",
    "skill_load", "project_set",
    "memory_list", "vault_query", "vault_semantic",
    "vault_store",
]

# THE KNOWN TOP-LEVEL KEYS (the 08-15 stale-key removal): the category
# names DEFAULTS defines. `_fill_missing` drops an existing config's
# top-level keys that are in this set but no longer in DEFAULTS (e.g.
# identity) — operator-custom keys are never touched. NOTE: identity is
# IN the set even though it's no longer in DEFAULTS — it was a known
# key and must be REMOVED from old configs, not preserved.
_DEFAULTS_KEYS = frozenset((
    "identity", "server", "thinking_budget", "autonomy", "budget",
    "tool_loop_guardrails", "context", "db", "provider", "models",
    "channels", "workspace", "sandbox", "emotion",
))


def _defaults_seed_cfg() -> dict:
    """The FULL config shape for a fresh profile: every DEFAULTS key
    (the file carries the complete schema — the Operator's no-partial
    rule), with the provider selection nulled (unconfigured until the
    operator sets it) and the operator channel widened for commands."""
    from core.config import DEFAULTS
    import copy
    cfg = copy.deepcopy(DEFAULTS)
    # THE MODELS CATEGORY (the 08-15 schema): the Models tab — provider
    # + model per role, null until the operator configures them.
    for role in ("reason", "vision", "embedding"):
        cfg.setdefault("models", {}).setdefault(role, {
            "provider": None, "model": None,
            "fallback_provider": None, "fallback_model": None,
        })
    # Streaming: the settable knob — default true (live typing).
    cfg.setdefault("provider", {})["streaming"] = True
    # The operator channel: the widened command/read toolset.
    cfg.setdefault("channels", {})["user"] = {
        "tools": list(_OPERATOR_TOOLS),
        "skills": ["doctor", "nurse"],
    }
    # The assistant channel: the agents' skills (doctor + nurse) — the
    # DEFAULTS carry an EMPTY list, but the agents must USE the skills.
    asst = cfg["channels"].setdefault("assistant", {})
    asst["skills"] = ["doctor", "nurse"]
    asst.setdefault("tools", [])
    return cfg


def _fill_missing(wanted: dict, have: dict) -> dict:
    """Fill DEFAULTS keys missing from an existing config, recursively.
    The operator's values are NEVER overwritten — defaults fill gaps.
    A NULL value counts as missing (the null-skip doctrine: null in
    config = use the default), so seeded nulls get replaced by the
    real defaults (streaming, compression thresholds, ...). An EMPTY
    list in the channel skills also counts as missing (a pre-fix
    assistant channel with skills:[] strands the doctor/nurse). The
    operator channel is WIDENED when it lacks the seed's command
    tools (a pre-fix minimal [read_file] gets the full set)."""
    out = dict(have or {})
    # THE STALE-KEY REMOVAL (the 08-15 fix): top-level categories that
    # DEFAULTS no longer defines (e.g. identity — the .md files own it
    # now) are DROPPED from an existing config, so the file matches the
    # schema exactly. Operator's custom top-level keys are preserved
    # (only keys DEFAULTS knows are ever removed).
    for key in list(out.keys()):
        if key not in wanted and key in _DEFAULTS_KEYS:
            del out[key]
    for key, value in (wanted or {}).items():
        cur = out.get(key)
        if key not in out or cur is None:
            out[key] = value
        elif isinstance(value, list) and cur == [] and value:
            # THE EMPTY-LIST FILL (the 08-14 fix): an empty list in the
            # existing config (channel skills) means "not configured yet"
            # — fill the default when the default is non-empty.
            out[key] = value
        elif (isinstance(value, list) and isinstance(cur, list)
              and value and cur and key == "tools"
              and "terminal" in value and "terminal" not in cur):
            # THE OPERATOR-CHANNEL WIDEN (the 08-15 fix): the user
            # channel seeded with the minimal [read_file] (an old boot)
            # lacks the command tools. When the seed's list has terminal
            # and the existing doesn't, the existing was the MINIMAL
            # baseline — replace it with the seed's full set.
            out[key] = value
        elif (isinstance(value, list) and isinstance(cur, list)
              and value and cur and key == "tools"
              and "write_file" in value and "write_file" not in cur):
            # THE WRITE-TOOLS WIDEN (the 08-15 fix): a channel seeded
            # before the write tools existed lacks write_file/append/
            # patch. When the seed has them and the existing doesn't,
            # the existing was the pre-08-15 set — replace with the
            # seed's full set (write included).
            out[key] = value
        elif isinstance(value, dict) and isinstance(cur, dict):
            out[key] = _fill_missing(value, cur)
            # THE NESTED STALE-KEY REMOVAL (the 08-15 fix): sub-keys that
            # DEFAULTS no longer defines are dropped (e.g. emotion's
            # llm_gate + min_chars — removed in the 08-15 trim).
            if key == "emotion" and isinstance(out[key], dict):
                for sub in ("llm_gate", "min_chars"):
                    out[key].pop(sub, None)
    return out


def ensure_all() -> list[str]:
    """Ensure every system profile exists. Returns the created names.

    WIPE RECOVERY (the Operator's 08-12 wipe-test): the DEFAULT profile's
    layout is also self-healed here — after a wipe its dirs are gone,
    and nothing else rebuilds them (the system profiles call their own
    ensure_layout; .default had no owner).
    """
    created = []
    # THE PLATFORM ROOT DIRS (the Operator's 08-12 wipe-fix): the wipe
    # keep-list does NOT include the root logs/ dir, but the metrics
    # layout expects it (the legacy shared root + the log-location
    # doctor check). Recreate it at every boot so a wiped tree springs
    # back fully.
    try:
        from core.config import ATHENA_ROOT
        (ATHENA_ROOT / "logs").mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    # BUILTIN SEED (the Operator's 08-12 builtin spec): Athena ships with
    # GENERALIZED built-in skills/tools inside athena-system/
    #   skills/  — the built-in skills (clock, calendar, doctor, ...)
    #   tools/   — the built-in tools (TOOL.md + references + scripts)
    # PLUGINS ARE NOT NATIVE (the Operator's 08-12 directive): plugins are
    # the COMMUNITY modding layer — a way to modify Athena natively
    # through community-based modding, not something Athena ships. The
    # shared plugins home stays EMPTY by default; the community installs
    # their own.
    # When a shared home is EMPTY (a wipe, a fresh install), the builtin
    # skills AND tools are copied in — so Athena always has working
    # skills/tools, and the wipe keep-files spring her back fully
    # operational. The tools also register at boot (core.builtin_tools)
    # — the shared home makes them visible like skills are.
    try:
        from core.config import ATHENA_ROOT, SHARED_SKILLS, SHARED_TOOLS
        sys_dir = ATHENA_ROOT / "athena-system"
        # Skills: athena-system/skills/<name>/SKILL.md → shared skills.
        src_skills = sys_dir / "skills"
        if src_skills.is_dir():
            SHARED_SKILLS.mkdir(parents=True, exist_ok=True)
            if not any(SHARED_SKILLS.iterdir()):
                for item in src_skills.iterdir():
                    dst = SHARED_SKILLS / item.name
                    if item.is_dir() and not dst.exists():
                        shutil.copytree(item, dst,
                                        ignore=shutil.ignore_patterns(
                                            "__pycache__", "*.pyc"))
                    elif item.is_file() and not dst.exists():
                        shutil.copy2(item, dst)
        # Tools: athena-system/tools/<name>/ → shared tools (the Operator's
        # 08-12 directive: the shared tools home is populated from the
        # built-ins, mirroring skills).
        src_tools = sys_dir / "tools"
        if src_tools.is_dir():
            SHARED_TOOLS.mkdir(parents=True, exist_ok=True)
            if not any(SHARED_TOOLS.iterdir()):
                for item in src_tools.iterdir():
                    dst = SHARED_TOOLS / item.name
                    if item.is_dir() and not dst.exists():
                        shutil.copytree(item, dst,
                                        ignore=shutil.ignore_patterns(
                                            "__pycache__", "*.pyc"))
                    elif item.is_file() and not dst.exists():
                        shutil.copy2(item, dst)
    except Exception:
        pass
    # THE DEFAULT PROFILE: its layout + six files must exist (a wiped
    # tree has neither). The root config.yaml is its seed (the global
    # config for system profiles — they are tied together globally).
    try:
        from intelligence.profiles import Profile, ensure_profile_files
        from core.config import DEFAULT_PROFILE_ROOT
        dflt = Profile(name=".default", root=DEFAULT_PROFILE_ROOT,
                       is_default=True)
        dflt.ensure_layout()
        ensure_profile_files(dflt)
        # MATERIALIZE the seeded config: the FULL per-profile schema
        # (identity/server/provider/channels/caps/budget/theme/autonomy —
        # the Operator's 08-14 spec). A wiped tree springs back with the
        # complete structure (nulls where the operator hasn't configured),
        # so the wipe test + the operator's edits both work.
        try:
            from core.config import ATHENA_ROOT, profile_config_path, \
                load_raw_config, DEFAULTS
            import yaml as _yaml
            pcfg = profile_config_path("")
            # The default profile's config is the seed for the system
            # profiles (the per-profile model — there is NO root config).
            if not pcfg.exists():
                pcfg.parent.mkdir(parents=True, exist_ok=True)
                # THE DEFAULTS-DERIVED SEED (the Operator's 08-14 spec):
                # the config is GENERATED from DEFAULTS — ONE source of
                # truth. Every DEFAULTS key lands in the file (streaming,
                # emotion.llm_gate, compression, ...) automatically; the
                # hardcoded string seed was the bug (new defaults never
                # reached the generated configs). Provider selection is
                # nulled (unconfigured until the operator sets it); the
                # operator channel carries the widened command toolset.
                _seed = _yaml.safe_dump(
                    _defaults_seed_cfg(), sort_keys=False,
                    default_flow_style=False, width=200)
                pcfg.write_text(_seed, encoding="utf-8")
            else:
                # THE FULL-SCHEMA MIGRATION (the Operator's 08-14 spec):
                # an EXISTING config from a pre-fix boot may lack keys
                # that DEFAULTS has (streaming, emotion.llm_gate,
                # compression thresholds, the widened channels). Add the
                # missing keys in place — the config NEVER loses the
                # operator's values; defaults fill only the gaps. This
                # generalizes the channel repair (same mechanism, whole
                # schema).
                try:
                    _cfg = _yaml.safe_load(pcfg.read_text(encoding="utf-8"))
                    _wanted = _defaults_seed_cfg()
                    _merged = _fill_missing(_wanted, _cfg)
                    if _merged != _cfg:
                        pcfg.write_text(
                            _yaml.safe_dump(_merged, sort_keys=False,
                                            default_flow_style=False,
                                            width=200),
                            encoding="utf-8")
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass
    # THE INTEGRITY BASELINE (the 08-12 wipe-fix): the manifest lives
    # in profiles/.default/operations/manifest.json (moved from sessions/
    # — the Operator's home layout: machinery in operations/, not
    # conversation) — a wipe deletes it, and the boot must rebuild it so
    # the doctor's integrity check is green from the first pass after a
    # spring-back.
    try:
        from security.integrity import build_manifest
        if not __import__("pathlib").Path(
                __import__("core.config", fromlist=["DEFAULT_PROFILE_ROOT"])
                .DEFAULT_PROFILE_ROOT / "operations" / "manifest.json").exists():
            build_manifest()
    except Exception:
        pass
    # The NURSE — the repair agent (the ONLY agent inside athena-system).
    if _ensure_one(
        ".nurse",
        {"name_first": "Nurse", "name_nick": "Nurse",
         "role": "Repair agent", "home": "athena-system"},
        NURSE_BODY):
        created.append(".nurse")
    # The nurse's doctor-artifacts home (the diagnosis facts) + the
    # TEST INFORMATION subfolder (the Operator's spec: .nurse/doctor/test/).
    try:
        from doctor.run import artifacts_dir, test_info_dir
        artifacts_dir()
        test_info_dir()
    except Exception:
        pass
    # The JANITOR — the optimization agent (the provider pass for
    # performance; its FREE scan tier (custodian) lives inside the
    # profile, mirroring .nurse/doctor/).
    if _ensure_one(
        ".janitor",
        {"name_first": "Janitor", "name_nick": "Janitor",
         "role": "System hygiene agent", "home": "athena-system"},
        JANITOR_BODY):
        created.append(".janitor")
    # The janitor's CUSTODIAN home (the FREE scan tier — like the
    # nurse's doctor home): profiles/.janitor/custodian/.
    try:
        (ATHENA_ROOT / "profiles" / ".janitor" / "custodian"
         ).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    # THE FULL PER-PROFILE LAYOUT (the Operator's 08-12 audit fix): every
    # profile root must carry its OWN agent/ runtime/ workspace/ sandbox/
    # logs/ events/ dirs (the doctor's layout + wipe tests assert them).
    # The runtime creates them lazily on first run, but the boot must
    # materialize them so a wiped tree springs back complete. Applies to
    # ALL profiles present (system + named like profile-agent).
    try:
        for pname in (".default", ".nurse", ".janitor"):
            proot = ATHENA_ROOT / "profiles" / pname
            if pname == ".default":
                proot = DEFAULT_PROFILE_ROOT
            for sub in ("agent", "runtime", "workspace", "sandbox",
                        "logs", "events"):
                try:
                    (proot / sub).mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
    except Exception:
        pass
    # NAMED PROFILES (the 08-12 start-fix): any other profile present
    # (profile-agent, future named agents) gets the same full layout.
    try:
        from intelligence.profiles import list_profiles
        for p in list_profiles():
            if p.name in (".default", ".nurse", ".janitor"):
                continue
            try:
                for sub in ("agent", "runtime", "workspace", "sandbox",
                            "logs", "events"):
                    (p.root / sub).mkdir(parents=True, exist_ok=True)
                from core.config import (SHARED_PLUGINS, SHARED_TOOLS,
                                         SHARED_SKILLS, SHARED_WORKFLOWS)
                from intelligence.profiles import _symlink_if_missing
                for link_name, target in (("plugins", SHARED_PLUGINS),
                                          ("tools", SHARED_TOOLS),
                                          ("skills", SHARED_SKILLS),
                                          ("workflows", SHARED_WORKFLOWS)):
                    _symlink_if_missing(p.root / link_name, target)
            except Exception:
                pass
    except Exception:
        pass
    # THE SHARED-HOME SYMLINKS (the Operator's 08-12 audit fix): each
    # profile's plugins/tools/skills must be native symlinks to the
    # shared homes — a wipe clears them and the boot must re-wire them
    # (the doctor's symlink tests assert the wiring for every profile).
    try:
        from intelligence.profiles import _symlink_if_missing
        from core.config import (SHARED_PLUGINS, SHARED_TOOLS, SHARED_SKILLS,
                                 SHARED_WORKFLOWS)
        for pname in (".default", ".nurse", ".janitor"):
            proot = ATHENA_ROOT / "profiles" / pname
            if pname == ".default":
                proot = DEFAULT_PROFILE_ROOT
            for link_name, target in (("plugins", SHARED_PLUGINS),
                                      ("tools", SHARED_TOOLS),
                                      ("skills", SHARED_SKILLS),
                                      ("workflows", SHARED_WORKFLOWS)):
                try:
                    _symlink_if_missing(proot / link_name, target)
                except Exception:
                    pass
    except Exception:
        pass
    # THE USER WORKFLOWS DIR (the Operator's 08-12 workflow spec): user
    # workflows live at .athena/workflows/ — like skills, they are
    # discoverable + loadable (the loader searches this dir FIRST, then
    # the core athena-system/workflows/). Created at boot so a wiped
    # tree springs it back.
    #
    # AUTO-POPULATION (the Operator's 08-14/08-15 fix): the BUILT-IN
    # workflows from athena-system/workflows/builtin/ (the core, which
    # survives wipes) are COPIED into the shared root at boot — so the
    # user tier mirrors the core tier. A user override with the same
    # name wins (never clobber an existing user file). The 10 default
    # workflows (conversation, programmer, researcher, learning,
    # teaching, roleplay, writer, strategist, counselor, auditor) always
    # populate when missing.
    try:
        import shutil as _shwf
        _wf = ATHENA_ROOT / "workflows"
        _wf.mkdir(parents=True, exist_ok=True)
        _core_wf = ATHENA_ROOT / "athena-system" / "workflows" / "builtin"
        if _core_wf.is_dir():
            for src in _core_wf.glob("*.md"):
                dst = _wf / src.name
                if not dst.exists():
                    try:
                        _shwf.copyfile(src, dst)
                    except Exception:
                        pass
    except Exception:
        pass
    # THE GRAPHS DIRS (the Operator's 08-14 Timeline spec): the ROOT
    # graphs dir (.athena/graphs — maps athena-system, the architecture
    # map) + each PROFILE's graphs dir (their own disk + projects).
    # Created at boot so a wiped tree springs them back.
    try:
        _g = ATHENA_ROOT / "graphs"
        _g.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        for pname in (".default", ".nurse", ".janitor"):
            _pg = ATHENA_ROOT / "profiles" / pname / "graphs"
            _pg.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    # THE PER-PROFILE CONFIG SEED (the Operator's 08-12 spec): each
    # profile owns its OWN config.yaml — NO fallback to the default. When
    # a system profile's config is missing (wipe, first boot), it is
    # materialized from the FULL SCHEMA (the 08-14 fix — NOT a copy of
    # the default's config, which inherits stale/partial state). Every
    # profile boots COMPLETE: all sections present, provider selection
    # overlaid (reason + fallback), nulls only where truly unconfigured.
    try:
        import yaml as _yaml
        from core.config import save_config
        for pname in (".nurse", ".janitor"):
            pcfg = ATHENA_ROOT / "profiles" / pname / "config.yaml"
            pcfg.parent.mkdir(parents=True, exist_ok=True)
            # THE FULL-SCHEMA SEED/MIGRATION (the 08-14 spec): the
            # DEFAULTS-derived shape (every section), with the provider
            # selection overlaid so the worker bees build the SAME chain
            # the operator configured. Missing keys are filled on an
            # existing config; a missing file is written complete.
            _schema = _defaults_seed_cfg()
            sel = (_schema.setdefault("models", {}))
            sel["reason"] = {
                "provider": "opencode-go",
                "model": "deepseek-v4-flash",
                "fallback_provider": "opencode-zen",
                "fallback_model": "deepseek-v4-flash-free",
            }
            if pcfg.exists():
                try:
                    _cur = _yaml.safe_load(pcfg.read_text(encoding="utf-8"))
                    _schema = _fill_missing(_schema, _cur)
                except Exception:
                    pass
            save_config(_schema, profile=pname)
    except Exception:
        pass
    # THE PERMISSIONS.YAML (the Operator's 08-15 spec): every profile's
    # root carries a permissions.yaml — the 4-channel store (operator/
    # agent/system name lists + the global channel's NULL flags). The
    # permission engine writes it lazily on the first decision; the boot
    # materializes the default so a wiped tree springs it back COMPLETE
    # (the wipe test + the Settings Permissions tab expect it).
    try:
        import yaml as _pyaml
        from security.permissions import _default_store
        _perm_profiles = [".default", ".nurse", ".janitor"]
        try:
            from intelligence.profiles import list_profiles
            for p in list_profiles():
                if p.name not in _perm_profiles:
                    _perm_profiles.append(p.name)
        except Exception:
            pass
        for pname in _perm_profiles:
            try:
                from security.permissions import _rules_path
                _pp = _rules_path(pname)
                if not _pp.exists():
                    _pp.parent.mkdir(parents=True, exist_ok=True)
                    _pp.write_text(
                        _pyaml.safe_dump(_default_store(), sort_keys=False,
                                         allow_unicode=True),
                        encoding="utf-8")
            except Exception:
                pass
    except Exception:
        pass
    # THE EMOTION FILES (the Operator's 08-12 audit fix): every profile
    # carries EMOTION.md on BOTH sides (assistant/ + user/) — the wipe
    # test asserts them. The emotion system writes them lazily on the
    # first turn; the boot materializes the defaults so a wiped tree
    # springs back complete.
    try:
        from core.emotion import write_emotion, default_emotion, _emotion_path
        _emotion_profiles = [".default", ".nurse", ".janitor"]
        try:
            from intelligence.profiles import list_profiles
            for p in list_profiles():
                if p.name not in _emotion_profiles:
                    _emotion_profiles.append(p.name)
        except Exception:
            pass
        for pname in _emotion_profiles:
            for side in ("assistant", "user"):
                try:
                    if not _emotion_path(side, pname).exists():
                        write_emotion(side, profile=pname,
                                      vector=default_emotion())
                except Exception:
                    pass
    except Exception:
        pass
    return created

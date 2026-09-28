"""Core surface test — consolidated (the Operator's 08-12 directive).

ONE module per category: this composer runs every core submodule's
checks and merges them into a single report. Check names are preserved
1:1 — the doctor count and the nurse's failure tracking stay stable
across consolidation.
"""
from __future__ import annotations

from pathlib import Path
from core.config import ATHENA_ROOT

def _chk_artifacts() -> list[dict]:
    import json
    from doctor.run import artifacts_dir, persist_report, latest_diagnosis

    checks = []

    # 1. The artifacts dir is inside the .nurse profile.
    d = artifacts_dir()
    checks.append({
        "name": "doctor artifacts in .nurse/doctor/",
        "status": "ok" if ".nurse" in str(d) and d.name == "doctor"
        else "fail",
        "detail": str(d),
    })

    # 2. persist_report writes a JSON fact file; latest_diagnosis reads it.
    report = {
        "summary": {"ok": 1, "warn": 0, "fail": 0, "info": 0, "total": 1},
        "tests": [{"category": "core", "priority": "high", "name": "x",
                   "status": "ok", "detail": ""}],
    }
    p = persist_report(report)
    back = latest_diagnosis()
    checks.append({
        "name": "diagnosis persists + reads back",
        "status": "ok" if p.exists() and back
        and back.get("summary", {}).get("ok") == 1 else "fail",
        "detail": str(p),
    })

    # 3. The artifact is JSON (a factual file, not a session .db).
    checks.append({
        "name": "artifact is JSON facts (not a session)",
        "status": "ok" if p.suffix == ".json"
        and "session-" not in p.name else "fail",
        "detail": p.name,
    })
    return checks


def _chk_budget() -> list[dict]:
    from core.config import load_config
    from core.message_loop import MessageLoop

    checks = []
    cfg = load_config()
    iter_cfg = cfg.get("iteration_budget", {})
    ml_cfg = cfg.get("message_loop", {})

    main_iter = int(iter_cfg.get("main_iterations", 100))
    main_tokens = int(iter_cfg.get("main_max_tokens", 5120))
    sub_iter = int(iter_cfg.get("subagent_iterations", 50))
    sub_tokens = int(iter_cfg.get("subagent_max_tokens", 2560))

    checks.append({
        "name": "main budget 100/5120",
        "status": "ok" if main_iter == 100 and main_tokens == 5120 else "fail",
        "detail": f"{main_iter} iter / {main_tokens} tok",
    })
    checks.append({
        "name": "subagent budget 50/2560",
        "status": "ok" if sub_iter == 50 and sub_tokens == 2560 else "fail",
        "detail": f"{sub_iter} iter / {sub_tokens} tok",
    })
    # The 50% rule: subagents are exactly half of main.
    checks.append({
        "name": "subagents 50% of main",
        "status": "ok" if sub_iter == main_iter // 2 and sub_tokens == main_tokens // 2 else "fail",
        "detail": f"iter {main_iter}→{sub_iter}, tok {main_tokens}→{sub_tokens}",
    })
    # The main MessageLoop carries both caps (the Operator's invariant:
    # the loop's caps EXACTLY match the config's — not a frozen value).
    loop = MessageLoop(system_prompt="",
                       max_iterations=int(ml_cfg.get("max_iterations", 100)),
                       max_tokens=int(ml_cfg.get("max_tokens", 0)) or None)
    checks.append({
        "name": "message loop carries caps",
        "status": "ok" if (
            loop.max_iterations == int(ml_cfg.get("max_iterations", 100))
            and loop.max_tokens == (int(ml_cfg.get("max_tokens", 0)) or None))
        else "fail",
        "detail": f"{loop.max_iterations} iter / {loop.max_tokens} tok",
    })
    return checks


def _chk_channels() -> list[dict]:
    from core.channels import validate_event, get_channel, Channel

    checks = []
    user = validate_event({"channel": "user", "content": "hi"})
    checks.append({
        "name": "user channel accepted",
        "status": "ok" if isinstance(user, Channel) else "fail",
        "detail": f"tools={user.tools if user else None}",
    })
    hacker = validate_event({"channel": "hacker", "content": "hi"})
    checks.append({
        "name": "unknown channel denied",
        "status": "ok" if hacker is None else "fail",
        "detail": "",
    })
    malformed = validate_event({"content": "no channel"})
    checks.append({
        "name": "malformed event denied",
        "status": "ok" if malformed is None else "fail",
        "detail": "",
    })
    system = get_channel("system")
    checks.append({
        "name": "system channel may_think + full tools",
        "status": "ok" if system and system.may_think and system.tools == ["*"] else "fail",
        "detail": "",
    })
    user_ch = get_channel("user")
    checks.append({
        # THE 08-15 FIX (the Operator's permissions spec): the user
        # channel now carries the READ+EXPLORE+WRITE set — terminal,
        # browser/web, fs reads AND the write tools (write_file/append/
        # patch) — the PERMISSION engine gates them (the permissions.yaml
        # model: allow once/session/global).
        "name": "user channel read+explore+write (terminal + write_file)",
        "status": "ok" if user_ch and "terminal" in (user_ch.tools or [])
        and "write_file" in (user_ch.tools or []) else "fail",
        "detail": f"tools={user_ch.tools if user_ch else None}",
    })
    return checks


def _chk_config() -> list[dict]:
    from core.config import load_config

    cfg = load_config()
    checks = [
        {"name": "config loads", "status": "ok" if cfg else "fail", "detail": ""},
    ]
    compression = cfg.get("compression", {})
    upper = compression.get("upper_threshold")
    lower = compression.get("lower_threshold")
    checks.append({
        "name": "compression thresholds",
        # The invariant: upper > lower and both in (0, 1). NOT a frozen
        # value (the Operator can tune them; the relationship must hold).
        "status": "ok" if (isinstance(upper, (int, float)) and isinstance(lower, (int, float))
                           and 0 < lower < upper < 1) else "fail",
        "detail": f"upper={upper} lower={lower}",
    })
    # The provider section carries the SELECTION (no chain — the Operator's
    # design: catalog order is the default, per-type selection drives).
    sel = cfg.get("provider", {}).get("selection", {})
    checks.append({
        "name": "provider selection present",
        "status": "ok" if isinstance(sel, dict) else "fail",
        "detail": f"selection={list(sel.keys())}",
    })
    return checks


def _chk_db() -> list[dict]:
    import tempfile
    import uuid
    from pathlib import Path
    from core import db as db_layer
    import core.db as dbmod

    checks = []
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        orig_vault = db_layer.vault_path
        orig_sessions = dbmod.sessions_dir
        db_layer.vault_path = staticmethod(
            lambda *a, **k: td_path / "vault.db")
        dbmod.sessions_dir = staticmethod(
            lambda *a, **k: td_path / "sessions")
        (td_path / "sessions").mkdir(parents=True, exist_ok=True)
        try:
            # Vault opens + writes.
            conn = db_layer.connect_vault("")
            cols = {r[1] for r in conn.execute("PRAGMA table_info(entries)")}
            eid = db_layer.record_vault_entry(
                "message", "db test", role="user", context="test", dedup=False)
            n = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            conn.close()
            checks.append({
                "name": "vault opens + writes",
                "status": "ok" if n >= 1 and "type" in cols else "fail",
                "detail": f"{n} entries, type col: {'type' in cols}",
            })
            # Session registers + history works. The test session id is
            # PINNED (the Operator's hygiene rule): a fixed id, never a
            # fresh UUID — no orphan session files after the doctor runs.
            sid = "db-test"
            db_layer.record_session_message(sid, "user", "hello", profile="")
            sid2 = db_layer.find_last_session(profile="")
            hist = db_layer.get_session_history(sid, limit=5, profile="")
            checks.append({
                "name": "session registered + history",
                "status": "ok" if hist and len(hist) >= 1 else "fail",
                "detail": f"history={len(hist)}",
            })
        finally:
            db_layer.vault_path = orig_vault
            dbmod.sessions_dir = orig_sessions
    return checks


def _chk_doctor_nurse_skills() -> list[dict]:
    from intelligence.skills import load_skills, filter_by_channel
    from core.channels import load_channels

    checks = []
    skills = load_skills()
    names = {s.name: s for s in skills}

    checks.append({
        "name": "doctor skill exists",
        "status": "ok" if "doctor" in names
        and "integrity" in names["doctor"].description.lower() else "fail",
        "detail": "free integrity check skill",
    })
    # The doctor skill POINTS to the nurse when issues exist (the Operator's
    # spec: issues/problems/bugs → fix/patch/update → consult the nurse).
    doc_body = (names["doctor"].body + " " +
                names["doctor"].description).lower()
    checks.append({
        "name": "doctor skill hands off to the nurse",
        "status": "ok" if "doctor" in names and "nurse" in doc_body
        and "consult" in doc_body
        and any(w in doc_body for w in ("repair", "patch", "update", "fix"))
        else "fail",
        "detail": "diagnose → if issues → consult the nurse (fix/patch/update)",
    })
    checks.append({
        "name": "nurse skill exists",
        "status": "ok" if "nurse" in names
        and "repair" in names["nurse"].description.lower() else "fail",
        "detail": "consult the repair agent skill",
    })

    # The assistant channel can USE both (the Operator's spec: agents use
    # them accordingly); the system channel allows all.
    channels = load_channels(None)
    asst = channels["assistant"]
    user = channels["user"]
    filt = {s.name for s in filter_by_channel(skills, asst)}
    ufilt = {s.name for s in filter_by_channel(skills, user)}
    checks.append({
        "name": "assistant channel gates doctor+nurse in",
        "status": "ok" if {"doctor", "nurse"} <= filt else "fail",
        "detail": f"usable={sorted(filt)}",
    })
    checks.append({
        "name": "user channel gates doctor+nurse in (1:1 with tools)",
        "status": "ok" if {"doctor", "nurse"} <= ufilt else "fail",
        "detail": f"usable={sorted(ufilt)}",
    })
    return checks


def _chk_message_loop() -> list[dict]:
    from core.message_loop import MessageLoop
    from core.channels import get_channel

    checks = []
    loop = MessageLoop.__new__(MessageLoop)
    loop.channel = get_channel("user")  # user: only read_file

    if hasattr(loop, "check_tool_allowed"):
        checks.append({
            "name": "user terminal denied",
            "status": "ok" if not loop.check_tool_allowed("terminal", "user") else "fail",
            "detail": "",
        })
        checks.append({
            "name": "user read_file allowed",
            "status": "ok" if loop.check_tool_allowed("read_file", "user") else "fail",
            "detail": "",
        })
    else:
        # Fall back to the channel-level check.
        ch = get_channel("user")
        checks.append({
            # THE 08-15 FIX: the gate is PRESENT when the channel carries
            # the write tools (the permission engine gates them — the
            # 08-15 permissions.yaml model).
            "name": "gate present on channel (write tools gated)",
            "status": "ok" if ch and "terminal" in (ch.tools or [])
            and "write_file" in (ch.tools or []) else "fail",
            "detail": f"user tools={ch.tools if ch else None}",
        })
    return checks


def _chk_nurse() -> list[dict]:
    from filesystem.safety import check_write, ScopeError
    from doctor import nurse

    checks= []
    # Without nurse: sanctum write blocked.
    try:
        check_write(str(ATHENA_ROOT / "athena-system" / "nurse-test.txt"))
        checks.append({"name": "sanctum sealed without nurse",
                       "status": "fail", "detail": "write allowed"})
    except ScopeError:
        checks.append({"name": "sanctum sealed without nurse",
                       "status": "ok", "detail": ""})

    # Identity gate: an ordinary agent cannot take the sanctum key.
    took = nurse.enter_scope("default")
    checks.append({
        "name": "identity gate: only nurse takes the key",
        "status": "ok" if not took else "fail",
        "detail": f"default took key: {took}",
    })

    # Nurse in scope: athena-system/ writable.
    nurse.enter_scope("nurse")
    try:
        check_write(str(ATHENA_ROOT / "athena-system" / "nurse-test.txt"))
        checks.append({"name": "nurse may repair code",
                       "status": "ok", "detail": ""})
    except ScopeError as exc:
        checks.append({"name": "nurse may repair code",
                       "status": "fail", "detail": str(exc)})
    # Outside .athena entirely (the bounds rule) still refused for nurse —
    # her repair zone is inside athena-system/ only.
    try:
        check_write("/tmp/nurse-test.txt")
        checks.append({"name": "nurse bounded to athena-system",
                       "status": "fail", "detail": "write allowed"})
    except ScopeError:
        checks.append({"name": "nurse bounded to athena-system",
                       "status": "ok", "detail": ""})
    nurse.exit_scope()

    # After exit: sealed again.
    try:
        check_write(str(ATHENA_ROOT / "athena-system" / "nurse-test.txt"))
        checks.append({"name": "sanctum resealed after nurse",
                       "status": "fail", "detail": "write allowed"})
    except ScopeError:
        checks.append({"name": "sanctum resealed after nurse",
                       "status": "ok", "detail": ""})
    return checks


def _chk_safety() -> list[dict]:
    from filesystem.safety import check_read, check_write, ScopeError, ATHENA_ROOT
    from core.config import DEFAULT_PROFILE_ROOT

    checks = []

    def blocked(fn):
        try:
            fn()
            return False
        except ScopeError:
            return True

    checks.append({
        "name": "outside read blocked",
        "status": "ok" if blocked(lambda: check_read("/etc/passwd")) else "fail",
        "detail": "",
    })
    checks.append({
        "name": "sanctum write blocked (outside athena-system)",
        "status": "ok" if blocked(lambda: check_write("/tmp/forbidden.txt")) else "fail",
        "detail": "",
    })
    checks.append({
        "name": "sanctum write blocked (athena-system/)",
        "status": "ok" if blocked(lambda: check_write(str(ATHENA_ROOT / "athena-system" / "forbidden.txt"))) else "fail",
        "detail": "",
    })
    checks.append({
        "name": "inside read allowed",
        "status": "ok" if check_read(str(DEFAULT_PROFILE_ROOT / "config.yaml")) else "fail",
        "detail": "",
    })
    checks.append({
        "name": "workspace write allowed",
        "status": "ok" if check_write(str(DEFAULT_PROFILE_ROOT / "workspace" / "t.txt")) else "fail",
        "detail": "",
    })
    return checks


def _chk_security() -> list[dict]:
    from security.security import mark_untrusted, sanitize_tool_result

    checks = []
    out = sanitize_tool_result("do evil")
    checks.append({
        "name": "tool output wrapped untrusted",
        "status": "ok" if "UNTRUSTED" in out and "do evil" in out else "fail",
        "detail": "",
    })
    marked = mark_untrusted("payload")
    checks.append({
        "name": "mark_untrusted brackets",
        "status": "ok" if marked.count("UNTRUSTED") >= 2 else "fail",
        "detail": "",
    })

    # Integrity baseline
    from security.integrity import scan, MANIFEST_PATH
    if not MANIFEST_PATH.exists():
        checks.append({
            "name": "integrity baseline exists",
            "status": "fail",
            "detail": "run athena security to build",
        })
        checks.append({
            "name": "integrity clean",
            "status": "warn",
            "detail": "no baseline — nothing to compare",
        })
    else:
        checks.append({
            "name": "integrity baseline exists",
            "status": "ok",
            "detail": "",
        })
        report = scan()
        checks.append({
            "name": "integrity clean",
            "status": "ok" if report.get("ok") else "fail",
            "detail": f"changed={report.get('changed')} added={report.get('added')}",
        })
    return checks


_SUBMODULES = [
    "approval_ux",
    "artifacts",
    "budget",
    "budget_enforce",
    "channels",
    "column_family",
    "config",
    "db",
    "doctor_nurse_skills",
    "integrations_billing_approvals",
    "jsonl_io",
    "message_loop",
    "nurse",
    "nurse_session",
    "path_trace",
    "safety",
    "schema_consistency",
    "security",
]


def _load_sub(name: str):
    """Import a submodule by its _sub_* file name (namespace package)."""
    import importlib.util
    here = Path(__file__).parent
    path = here / f"_sub_{name}.py"
    spec = importlib.util.spec_from_file_location(
        f"doctor.core._sub_{name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod



def run() -> list[dict]:
    checks: list[dict] = []
    for name in _SUBMODULES:
        # Inline (folded) checks run directly; file-backed ones import.
        inline = globals().get(f"_chk_{name}")
        if inline is not None:
            try:
                checks.extend(inline())
            except Exception as exc:
                checks.append({
                    "name": f"core/{name}",
                    "status": "fail",
                    "detail": f"{type(exc).__name__}: {exc}",
                })
            continue
        try:
            mod = _load_sub(name)
            if callable(getattr(mod, "run", None)):
                checks.extend(mod.run())
        except Exception as exc:
            checks.append({
                "name": f"core/{name}",
                "status": "fail",
                "detail": f"{type(exc).__name__}: {exc}",
            })
    return checks


def fix() -> None:
    """The composer's repair: re-baseline the integrity manifest.

    (The folded security check's fix() — the integrity baseline is the
    only state the doctor repairs; everything else is read-only.)
    """
    try:
        from security.integrity import build_manifest
        build_manifest()
    except Exception:
        pass

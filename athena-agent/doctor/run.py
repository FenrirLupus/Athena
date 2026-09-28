"""Doctor runner — executes every test, reports by category + importance.

The single entry point for ALL surfaces. The CLI calls run_all() and the
GUI (when built) calls the SAME function — every test operates 1:1
regardless of how it's invoked (the doctor philosophy: check_ok /
check_warn / check_fail, and a diagnostic must never crash the run).

Test discovery:
    doctor/<category>/<NN>_<name>.py
    NN = importance level (10 critical, 20 high, 30 medium, 40 low).

Each test module exposes:
    run() -> list[Check] | Check
    Check = {"name": str, "status": "ok"|"warn"|"fail"|"info", "detail": str}
"""
from __future__ import annotations

import importlib.util
import os as _os
import sys
from pathlib import Path

DOCTOR_DIR = Path(__file__).parent
PRIORITY_LEVELS = {"10": "critical", "20": "high", "30": "medium", "40": "low"}


def _module_snapshot() -> dict:
    """Snapshot module-level mutable state that tests patch.

    The doctor's isolation rule (Operator 08-11): a test may patch globals
    (config paths, permission checks, registries) but the RUNNER
    guarantees they are restored even when a test raises mid-patch —
    a leak poisons every later test (the approval_ux → permissions
    cascade we hit). We snapshot the specific attributes the tests
    are known to touch; cheap and complete.
    """
    snap = {}
    try:
        import security.permissions as perm
        snap["perm.check"] = perm.check
        snap["perm.decide"] = perm.decide
        snap["perm._rules_path"] = perm._rules_path
        snap["perm._session_store"] = dict(perm._session_store)
    except Exception:
        pass
    try:
        import core.message_loop as ml
        snap["ml.tool_registry"] = ml.tool_registry
    except Exception:
        pass
    try:
        import metrics.logger as logger
        snap["logger.LOGS_DIR"] = logger.LOGS_DIR
    except Exception:
        pass
    try:
        import core.config
        snap["config.ATHENA_ROOT"] = core.config.ATHENA_ROOT
    except Exception:
        pass
    try:
        import intelligence.profiles as iprof
        snap["iprof.PROFILES_DIR"] = iprof.PROFILES_DIR
    except Exception:
        pass
    return snap


def _module_restore(snap: dict) -> None:
    """Restore module state (best-effort, never raises)."""
    for key, value in snap.items():
        try:
            if key == "perm.check":
                import security.permissions as perm
                perm.check = value
            elif key == "perm.decide":
                import security.permissions as perm
                perm.decide = value
            elif key == "perm._rules_path":
                import security.permissions as perm
                perm._rules_path = value
            elif key == "perm._session_store":
                import security.permissions as perm
                perm._session_store.clear()
                perm._session_store.update(value)
            elif key == "ml.tool_registry":
                import core.message_loop as ml
                ml.tool_registry = value
            elif key == "logger.LOGS_DIR":
                import metrics.logger as logger
                logger.LOGS_DIR = value
            elif key == "config.ATHENA_ROOT":
                import core.config
                core.config.ATHENA_ROOT = value
            elif key == "iprof.PROFILES_DIR":
                import intelligence.profiles as iprof
                iprof.PROFILES_DIR = value
        except Exception:
            pass


# The tests that MUTATE state (create profiles, snapshots, tempdirs,
# spawn subprocesses). In LIVE mode (the service's boot/hourly/nurse
# paths) these are SKIPPED — a diagnostic running inside the live
# process must never create/remove state. The full suite (including
# these) runs only in the isolated subprocess (`athena doctor`), where
# they operate on a temp copy and are deleted after. Names are the
# _sub_* files (consolidated layout, 08-12).
STATE_MUTATING = {
    "doctor/autonomy/25_self_modify.py",
    "doctor/autonomy/25_system_batch.py",
    "doctor/autonomy/_sub_agent_duties.py",  # APPLIES janitor sweep +
                                             # snapshots to the real tree
                                             # (08-12: the live boot pass
                                             # must never apply cleanups)
    "doctor/autonomy/_sub_delegation.py",    # deletes kanban tasks
    "doctor/autonomy/_sub_mcp_client.py",   # writes session-s1.db (test)
    "doctor/cli/_sub_profile_switch.py",    # switches the ACTIVE profile
                                            # to doctor-probe-* (08-12:
                                            # a live profile switch during
                                            # the boot pass can recreate/
                                            # wipe the sessions dir)
    "doctor/core/25_nurse_profile.py",
    "doctor/data/25_snapshots.py",
    "doctor/providers/25_profile_config_schema.py",
    "doctor/systems/25_emotion.py",
    "doctor/systems/25_profiles.py",
    "doctor/systems/25_wipe_test.py",
}


def _discover() -> list[dict]:
    """Find all test modules: doctor/<category>/<NN>_<name>.py, sorted."""
    tests = []
    for cat_dir in sorted(DOCTOR_DIR.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith("_"):
            continue
        for py in sorted(cat_dir.glob("[0-9][0-9]_*.py")):
            if py.name.startswith("__"):
                continue
            level = py.name[:2]
            tests.append({
                "category": cat_dir.name,
                "level": level,
                "priority": PRIORITY_LEVELS.get(level, "medium"),
                "name": py.stem[3:].replace("_", " "),
                "path": py,
            })
    return tests


def _load(path: Path):
    """Import a test module by path."""
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _normalize(result) -> list[dict]:
    if isinstance(result, dict):
        return [result]
    if isinstance(result, (list, tuple)):
        return list(result)
    return [{"name": "test", "status": "fail", "detail": f"bad return: {result!r}"}]


# -- The doctor's ARTIFACT directory (the Operator's spec) -------------------
# The doctor's findings are FACTS, not sessions. They persist to the
# nurse's domain: profiles/.nurse/doctor/ — the nurse reads them there
# to diagnose. Test artifacts are contained HERE, never scattered across
# the system's session dirs.
DOCTOR_ARTIFACTS_SUBDIR = "doctor"


def artifacts_dir() -> Path:
    """profiles/.nurse/doctor/ — where the doctor's facts live."""
    from core.config import ATHENA_ROOT
    p = ATHENA_ROOT / "profiles" / ".nurse" / DOCTOR_ARTIFACTS_SUBDIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_info_dir() -> Path:
    """profiles/.nurse/doctor/test/ — where the doctor's TEST INFORMATION
    lives (the Operator's spec: the test data, reports, and per-run outputs
    are stored here, separate from the single latest diagnosis the
    nurse reads). The test SCRIPTS stay in athena-system/doctor/ (they
    are code); the information they produce lives here.
    """
    p = artifacts_dir() / "test"
    p.mkdir(parents=True, exist_ok=True)
    return p


def persist_test_info(report: dict) -> Path:
    """Write a timestamped test report + the rolling test-latest.json.

    The doctor's test INFORMATION (the Operator's spec): every run's full
    report is stored in .nurse/doctor/test/ — timestamped for history,
    plus test-latest.json for the current state. The nurse reads the
    latest diagnosis (a fact); the full test info is kept for review.
    """
    import json as _json
    from datetime import datetime as _dt
    d = test_info_dir()
    ts = _dt.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "at": _dt.now().isoformat(timespec="seconds"),
        "summary": report.get("summary", {}),
        "tests": report.get("tests", []),
    }
    hist = d / f"test-report-{ts}.json"
    hist.write_text(_json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    latest = d / "test-latest.json"
    latest.write_text(_json.dumps(payload, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    # Rolling: keep the newest 20 test reports, evict older.
    try:
        reports = sorted(d.glob("test-report-*.json"), reverse=True)
        for old in reports[20:]:
            old.unlink()
    except Exception:
        pass
    return hist


def persist_report(report: dict) -> Path:
    """Write the diagnosis as a JSON fact file. Returns its path."""
    import json as _json
    from datetime import datetime as _dt
    p = artifacts_dir() / "latest-diagnosis.json"
    payload = {
        "at": _dt.now().isoformat(timespec="seconds"),
        "summary": report.get("summary", {}),
        "tests": report.get("tests", []),
    }
    p.write_text(_json.dumps(payload, indent=2, ensure_ascii=False),
                 encoding="utf-8")
    return p


def latest_diagnosis() -> dict | None:
    """The most recent persisted diagnosis (the nurse's factual input)."""
    import json as _json
    p = artifacts_dir() / "latest-diagnosis.json"
    try:
        if p.exists():
            return _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def run_all(category: str | None = None, _depth: int = 0, fix: bool = False,
            live: bool = False) -> dict:
    """Run every discovered test (optionally one category). Returns a report.

    _depth: recursion guard — the doctor self-test calls run_all() ONCE with
    a category; calling again from inside a test is refused (a diagnostic
    must never recurse forever).

    fix: when True, failed checks with a test-level fix() get repaired and
    the check is re-run once (the doctor --fix behavior). Each fix
    runs with the test's own scope; a repair failure is reported as a
    "fix-failed" entry, never a crash.

    live: when True (the service's boot/hourly/nurse paths), the
    STATE_MUTATING tests are SKIPPED — a diagnostic running inside the
    live process must never create profiles, snapshots, or tempdirs.
    They still run in the isolated subprocess (athena doctor).
    """
    if _depth >= 2:
        return {"summary": {"ok": 0, "warn": 0, "fail": 0, "info": 0, "total": 0},
                "tests": []}
    tests = _discover()
    if category:
        tests = [t for t in tests if t["category"] == category]
    if live:
        tests = [t for t in tests
                 if str(t["path"].relative_to(DOCTOR_DIR.parent))
                 .replace("\\", "/") not in STATE_MUTATING]
    # THE OPERATOR-ONLY WIPE TEST (the Operator's 08-12 spec): when the
    # ATHENA_WIPE_APPROVED token is absent, the wipe test is EXCLUDED
    # from the run entirely — agents (nurse, scheduler, any runtime)
    # must never even attempt it. Only the operator's CLI path sets the
    # token (a process-scoped approval); the subprocess inherits it via
    # the environment.
    if _os.environ.get("ATHENA_WIPE_APPROVED") != "1":
        tests = [t for t in tests
                 if "25_wipe_test" not in t["path"].name]

    results = []
    # THE 08-15 TEST-MODE: the whole doctor run is a test context — the
    # mocked-loop tests legitimately trip the guardrails; their L3 log
    # noise is suppressed (the blocks still return decisions). Restored
    # in the finally so the live service's guardrails are never affected.
    try:
        import security.loop_guardrails as _lg_mod
        _lg_mod.DOCTOR_TEST_MODE = True
    except Exception:
        pass
    try:
        for test in tests:
            # ISOLATION (the doctor's rule): snapshot shared module state
            # before each test, restore in finally — a test that raises
            # mid-patch can never poison the next test.
            snap = _module_snapshot()
            try:
                mod = _load(test["path"])
                if not hasattr(mod, "run"):
                    results.append({**test, "status": "fail",
                                    "detail": "module has no run()"})
                    continue
                checks = _normalize(mod.run())
                for check in checks:
                    entry = {
                        "category": test["category"],
                        "priority": test["priority"],
                        "name": check.get("name", test["name"]),
                        "status": check.get("status", "fail"),
                        "detail": check.get("detail", ""),
                    }
                    # --fix: repair failed checks when the test provides fix().
                    if entry["status"] == "fail" and fix and callable(getattr(mod, "fix", None)):
                        try:
                            mod.fix()
                            # re-run the whole module once after repair
                            rechecks = _normalize(mod.run())
                            repaired = next(
                                (c for c in rechecks
                                 if c.get("name", test["name"]) == entry["name"]),
                                None,
                            )
                            if repaired and repaired.get("status") == "ok":
                                entry["status"] = "ok"
                                entry["detail"] = (entry.get("detail") or "") + " [fixed]"
                            else:
                                entry["detail"] = (entry.get("detail") or "") + " [fix attempted]"
                        except Exception as exc:
                            entry["detail"] = (entry.get("detail") or "") + \
                                f" [fix failed: {type(exc).__name__}]"
                    results.append(entry)
            except Exception as exc:  # a diagnostic must never crash the run
                results.append({**test, "status": "fail",
                                "detail": f"{type(exc).__name__}: {exc}"})
            finally:
                _module_restore(snap)
    finally:
        # THE 08-15 TEST-MODE RESTORE: never leak the flag into the live
        # service (a real turn's guardrails must log at their real level).
        try:
            import security.loop_guardrails as _lg_mod
            _lg_mod.DOCTOR_TEST_MODE = False
        except Exception:
            pass

    summary = {"ok": 0, "warn": 0, "fail": 0, "info": 0, "total": len(results)}
    for r in results:
        status = r["status"]
        if status in summary:
            summary[status] += 1
    report = {"summary": summary, "tests": results}
    # SESSION HYGIENE (the Operator's 08-12 spec): after ANY doctor run,
    # remove test-debris session files so sessions/ holds ONLY real
    # UUID conversations. The doctor's own tests create pinned test
    # sessions (s1, toolcols, roles, db-test) + empty hello files —
    # those must never linger. Real UUID sessions are always kept.
    try:
        _sweep_test_sessions()
    except Exception:
        pass
    # Persist the diagnosis as a FACT (the nurse's domain) — only at the
    # TOP level (not from inside a doctor self-test), so the artifact is
    # always a complete, current diagnosis. Artifacts live in .nurse/doctor/.
    if _depth == 0:
        # THE METRICS STREAM (the Operator's 08-12 spec): the doctor's
        # run IS logged in the ONE metrics stream — its findings are
        # cross-diagnosable with everything else (a failing check shows
        # L4 with the code/reason the listener extracts; a green pass is
        # L2). No separate logging system — the metrics log is THE log.
        try:
            _s = report.get("summary", {})
            _fails = _s.get("fail", 0)
            _level = 4 if _fails else 2
            from core.logging import log_event
            log_event(
                _level,
                (f"doctor: {_s.get('ok', 0)} ok, {_fails} fail, "
                 f"{_s.get('warn', 0)} warn of {_s.get('total', 0)} checks"
                 + (f" — failing: {_fails}" if _fails else "")),
                source="doctor", tool="doctor", action="run_all",
                target="", profile=".nurse")
        except Exception:
            pass
        try:
            persist_report(report)
        except Exception:
            pass
        # TEST INFORMATION (the Operator's spec): the full report is ALSO
        # stored in .nurse/doctor/test/ — timestamped + rolling latest —
        # the doctor's test data, separate from the working diagnosis.
        try:
            persist_test_info(report)
        except Exception:
            pass
    return report


def _sweep_test_sessions() -> None:
    """Remove test-debris session files (the Operator's 08-12 hygiene).

    The doctor's tests create pinned test sessions (s1, toolcols, roles,
    db-test) and empty hello files. After a run, the session folders
    must hold ONLY real session-{UUID}.db conversations. The sweep:
      - OPERATOR dirs: NEVER touches real UUID conversations (the home
        rule — sessions/ holds the operator's real chats). The STRICT
        session-{UUID}.db rule makes a NON-UUID name debris by
        construction (toolcols/roles/s1 can never be a real chat), so
        those are removed from the operator dirs too.
      - The AGENTS' OWN folder — profiles/<p>/agent/sessions/ — is the
        agents' disposable space: non-UUID names and empty UUID files
        are swept freely there.
      - In the doctor's own working dirs: nothing (tests use tempdirs).
    """
    import re as _re
    from core import db as db_layer
    _uuid = _re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        _re.I)
    # OPERATOR dirs: only NON-UUID names are debris (the strict rule).
    for profile in ("", ".default"):
        sdir = db_layer.sessions_dir(profile)
        if not sdir.exists():
            continue
        for path in sdir.glob("session-*.db"):
            sid = path.stem[len("session-"):]
            if _uuid.match(sid):
                continue  # a real operator chat — never touched
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
    # AGENT dirs: non-UUID + empty UUID files are the agents' debris.
    for profile in (".nurse", ".janitor"):
        sdir = db_layer.sessions_dir(profile, kind="agent")
        if not sdir.exists():
            continue
        for path in sdir.glob("session-*.db"):
            sid = path.stem[len("session-"):]
            if not _uuid.match(sid):
                # A non-UUID name is agent/test debris by construction.
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            # UUID-named but empty (never written) — debris.
            try:
                import sqlite3 as _sq
                conn = _sq.connect(str(path))
                n = conn.execute(
                    "SELECT COUNT(*) FROM messages").fetchone()[0]
                conn.close()
                if n == 0:
                    path.unlink(missing_ok=True)
            except Exception:
                pass


def run_isolated(category: str | None = None, fix: bool = False,
                 timeout: float = 240.0) -> dict:
    """Run the FULL doctor in a SUBPROCESS (the deep audit).

    THE SYSTEMIC FIX (the Operator's 08-12 directive): a diagnostic must
    never touch the LIVE tree's module globals — the state-mutating
    tests (create profiles, snapshots, tempdirs) leaked state into the
    long-lived service process (the
    'loop-test' re-enable loop, tempdir floods, profile leaks). Running
    the doctor in a FRESH PROCESS means:

      • NO live module state is ever patched (the subprocess has its own)
      • the STATE_MUTATING tests run here (live=False) — in a fresh
        process their tempdirs are their own, and they self-clean
      • the live service only reads the returned report

    This is the deep-audit path: the scheduler's hourly doctor job and
    the nurse's diagnosis/verify use it. The service's own in-process
    pass uses run_all(live=True) — read-only checks only.

    Returns the same report dict as run_all(). Falls back to an
    in-process run if the subprocess cannot start.
    """
    import json as _json
    import subprocess as _subprocess
    import sys as _sys
    from pathlib import Path as _Path

    _root = _Path(__file__).resolve().parent.parent  # athena-system/

    code = (
        "import sys, json; sys.path.insert(0, r'%s'); "
        "from doctor.run import run_all; "
        "r = run_all(category=%r, fix=%r, live=False); "
        "print('__ATHENA_DOCTOR_REPORT__' + "
        "json.dumps(r, default=lambda o: str(o)))"
    ) % (_root, category or "", bool(fix))
    try:
        proc = _subprocess.run(
            [_sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(_root),
        )
        out = proc.stdout or ""
        marker = "__ATHENA_DOCTOR_REPORT__"
        if marker in out:
            payload = out.split(marker, 1)[1].strip()
            return _json.loads(payload)
        # No report on stdout — return an honest failure artifact.
        return {"summary": {"ok": 0, "warn": 0, "fail": 1, "info": 0,
                            "total": 1},
                "tests": [{"category": "run", "priority": "high",
                           "name": "isolated doctor subprocess",
                           "status": "fail",
                           "detail": (proc.stderr or "")[:300]}]}
    except Exception as exc:
        # The subprocess could not run — fall back to in-process (same
        # report shape); the caller decides whether that is acceptable.
        try:
            return run_all(category=category, fix=fix, live=False)
        except Exception as exc2:
            return {"summary": {"ok": 0, "warn": 0, "fail": 1, "info": 0,
                                "total": 1},
                    "tests": [{"category": "run", "priority": "high",
                               "name": "isolated doctor subprocess",
                               "status": "fail",
                               "detail": f"{type(exc).__name__}: {exc} "
                                         f"(fallback: {exc2})"}]}


def report(report: dict, *, colored: bool = False) -> str:
    """Render a report as text (CLI). The GUI renders the same dict."""
    lines = []
    by_cat: dict[str, list] = {}
    for r in report["tests"]:
        by_cat.setdefault(r["category"], []).append(r)

    for category in sorted(by_cat):
        lines.append(f"[{category}]")
        for r in by_cat[category]:
            mark = {"ok": "✓", "warn": "!", "fail": "✗", "info": "·"}.get(r["status"], "?")
            detail = f" — {r['detail']}" if r.get("detail") else ""
            lines.append(f"  {mark} [{r['priority']}] {r['name']}{detail}")
    s = report["summary"]
    lines.append(f"=== {s['ok']} ok, {s['warn']} warn, {s['fail']} fail, "
                 f"{s['info']} info (total {s['total']}) ===")
    return "\n".join(lines)

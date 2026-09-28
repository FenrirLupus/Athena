"""System-wise batch test — the Operator's specs.

1. Restart loop protection — 3 restarts/5s → disabled; nurse re-enables.
2. .secret store — protected credentials, nested keys, 0600.
3. Lifecycle/readiness — starting/ready/shutting_down states + the
   supervisor skips intentional shutdowns.
4. Resource monitor generalized — CPU + VRAM sampled alongside RAM/disk.
5. Version registry — profiles register; older profiles refuse to start;
   update-available when auto-update is on (default off).
6. Janitor — the hygiene sweep (dry-run default; system = report-only).
7. CLI runtime footer — the hotbar status line.
"""
from __future__ import annotations


def run() -> list[dict]:
    import tempfile
    from pathlib import Path
    import core.supervisor as sup
    import core.readiness as rd
    import core.resource_manager as rm
    import core.version_registry as vr
    import core.janitor as jn
    import core.secret_store as ss

    checks = []
    # TEMPDIR HYGIENE (the 08-12 fix): every mkdtemp dir this test creates
    # is tracked and removed on exit — a test that creates temp state
    # must clean it (the leaked /tmp/tmp* dirs were the loop-test fuel).
    import shutil as _shutil
    _leaked = []

    def _mkdtemp(*a, **k):
        d = tempfile.mkdtemp(*a, **k)
        _leaked.append(d)
        return d

    # 1. Restart loop protection (temp state, no real spawns).
    # The test patches the _state_path SEAM (the function both reads and
    # writes use) with a tempdir — fully isolated, restored on ANY exit.
    # Patching RUNTIMES_STATE directly is the 08-12 leak that made the
    # live supervisor re-enable 'loop-test' forever; the seam is the
    # single correct injection point. start_runtime is ALSO mocked — a
    # test must never spawn a real child runtime (the 08-12 CPU burn).
    import unittest.mock as _mock
    import tempfile as _tmpf
    from pathlib import Path as _Path
    _tmp_state = _Path(_mkdtemp(prefix="athena-test-")) / "runtimes.json"
    with _mock.patch.object(sup, "_state_path", return_value=_tmp_state), \
            _mock.patch.object(sup, "subprocess", create=True) as _sub:
        r1 = sup.record_restart("loop-test")
        r2 = sup.record_restart("loop-test")
        r3 = sup.record_restart("loop-test")
        disabled = sup.runtime_status("loop-test").get("disabled")
        # start_runtime must REFUSE (disabled) — and never reach Popen.
        refused = sup.start_runtime("loop-test")
        enabled = sup.enable_runtime("loop-test")
        # An actual spawn would show as subprocess.Popen being called.
        spawned = _sub.Popen.called
        checks.append({
            "name": "restart loop guard: 3/5s disables + nurse re-enables",
            "status": "ok" if not r1["disabled"] and not r2["disabled"]
            and r3["disabled"] and disabled and not refused["ok"]
            and enabled["ok"] and not spawned else "fail",
            "detail": f"3rd={r3['disabled']} disabled={disabled} "
                      f"refused={not refused['ok']} reenabled={enabled['ok']} "
                      f"spawned={spawned}",
        })
    # The tempdir state is deleted when the process exits; the seam was
    # restored by the patch context manager.

    # 2. Secret store: protected + nested get/set. Isolated in a
    #    TemporaryDirectory (auto-cleaning — no /tmp leak).
    with _tmpf.TemporaryDirectory() as _sec_td:
        orig_secret = ss.SECRET_FILE
        ss.SECRET_FILE = Path(_sec_td) / ".secret"
        try:
            ss.set("providers.test.api_key", "sk-x")
            got = ss.get("providers.test.api_key")
            prot = ss.is_protected()
            checks.append({
                "name": "secret store: nested keys + 0600 protection",
                "status": "ok" if got == "sk-x" and prot else "fail",
                "detail": f"got={got!r} protected={prot}",
            })
        finally:
            ss.SECRET_FILE = orig_secret

    # 3. SNAPSHOT TREE (the Operator's spec): snapshots/ keeps the backups/
    #    and patches/ subfolders TOGETHER — the snapshot systems grouped.
    import data.snapshots as _snap
    checks.append({
        "name": "snapshots/ holds backups/ + patches/ (kept together)",
        "status": "ok" if _snap.SNAPSHOT_DIR.is_dir()
        and _snap.BACKUPS_SUBDIR.is_dir()
        and _snap.PATCHES_SUBDIR.is_dir() else "fail",
        "detail": f"{_snap.SNAPSHOT_DIR.name}/ "
                  f"{_snap.BACKUPS_SUBDIR.name}/ + {_snap.PATCHES_SUBDIR.name}/",
    })
    import data.backup as _bkp
    checks.append({
        "name": "backups write into snapshots/backups/",
        "status": "ok" if "snapshots" in str(_bkp.BACKUP_DIR)
        and _bkp.BACKUP_DIR.name == "backups" else "fail",
        "detail": str(_bkp.BACKUP_DIR),
    })

    # 4. SESSION STATE: ready + shutting_down states (the Operator's flow).
    rd.set_state("runtime:rd-test", rd.READY, "up")
    rd.set_state("runtime:rd-stop", rd.SHUTTING_DOWN, "stopping")
    checks.append({
        "name": "readiness: ready + shutting_down states",
        "status": "ok" if rd.is_ready("runtime:rd-test")
        and rd.is_shutting_down("runtime:rd-stop") else "fail",
        "detail": f"ready={rd.is_ready('runtime:rd-test')} "
                  f"shutting={rd.is_shutting_down('runtime:rd-stop')}",
    })

    # 4b. The doctor's TEST INFORMATION lives at .nurse/doctor/test/
    #     (the Operator's spec): the test data/reports, separate from the
    #     single latest diagnosis. The test SCRIPTS stay in athena-system
    #     (code); the information they produce lives with the nurse.
    from doctor.run import test_info_dir
    tid = test_info_dir()
    checks.append({
        "name": "doctor test info at .nurse/doctor/test/",
        "status": "ok" if tid.is_dir()
        and ".nurse" in str(tid) and "doctor" in str(tid)
        and tid.name == "test" else "fail",
        "detail": str(tid),
    })

    # 4c. THE USAGE METER (the Operator's spec): tokens since the last
    #     compression — compress at 80%, the meter resets toward 0.
    #     All-time vault usage (which can exceed 100%) is NOT the meter.
    from context.compression import vault_usage_total, mark_usage_baseline, \
        usage_since_baseline
    _total = vault_usage_total("")
    mark_usage_baseline("")
    _since = usage_since_baseline("")
    checks.append({
        "name": "usage meter: since-baseline, not all-time",
        "status": "ok" if _since == 0 else "fail",
        "detail": f"alltime={_total} since={_since}",
    })

    # 4. Resource monitor: the generalized six (cpu + vram present).
    snap = rm.sample()
    checks.append({
        "name": "resource monitor generalized (cpu+vram+ram+disk+ctx+subs)",
        "status": "ok" if {"memory", "cpu", "vram", "disk", "context",
                           "subagents"} <= set(snap.keys()) else "fail",
        "detail": f"keys={sorted(snap.keys())}",
    })

    # 5. Version registry: older profile refuses; auto-update default off.
    orig_ver = vr.VERSIONS_STATE
    vr.VERSIONS_STATE = Path(_mkdtemp()) / "versions.json"
    try:
        vr.register("new-probe")
        vr._save({"profiles": {"old-probe":
                                {"version": "0.0.1",
                                 "athena_version": vr.ATHENA_VERSION}}})
        ok_new = vr.check("new-probe")["ok"]
        refuse_old = not vr.check("old-probe")["ok"]
        auto_off = vr.auto_update_enabled() is False
        checks.append({
            "name": "version registry: new ok, old refused, auto-update off",
            "status": "ok" if ok_new and refuse_old and auto_off else "fail",
            "detail": f"new={ok_new} old_refused={refuse_old} "
                      f"auto_off={auto_off}",
        })
    finally:
        vr.VERSIONS_STATE = orig_ver

    # 6. Janitor: the dry-run sweep + report-only system pass.
    orig_jan = jn.STATE_FILE
    jn.STATE_FILE = Path(_mkdtemp()) / "janitor.json"
    try:
        r = jn.run_sweep(dry_run=True)
        checks.append({
            "name": "janitor sweep (dry-run default, system report-only)",
            "status": "ok" if r["dry_run"] and isinstance(r["workspace"], list)
            and isinstance(r["system_reports"], list) else "fail",
            "detail": f"reports={r['report_count']}",
        })
        # The janitor's own system profile exists (profiles/.janitor/).
        st = jn.status()
        checks.append({
            "name": "janitor profile exists (.janitor)",
            "status": "ok" if st.get("profile_exists") else "fail",
            "detail": "profiles/.janitor/",
        })
    finally:
        jn.STATE_FILE = orig_jan

    # 6b. SYSTEM PROFILES auto-create at startup (the Operator's spec): if
    #     .nurse / .janitor are missing, ensure_all rebuilds them with
    #     the default files (assistant/user/sessions + nurse's doctor).
    from core.system_profiles import ensure_all
    from intelligence.profiles import get_profile, PROFILES_DIR
    import core.config as _cfg
    import core.system_profiles as _sp
    import tempfile as _tf
    _saved_root = _cfg.ATHENA_ROOT
    _saved_sp = _sp.ATHENA_ROOT
    _saved_profiles_dir = PROFILES_DIR
    with _tf.TemporaryDirectory() as _td:
        _cfg.ATHENA_ROOT = Path(_td)
        _sp.ATHENA_ROOT = Path(_td)
        import intelligence.profiles as _ip
        _ip.PROFILES_DIR = Path(_td) / "profiles"
        created = ensure_all()
        nurse = get_profile(".nurse")
        jan = get_profile(".janitor")
        ok_nurse = ".nurse" in created and nurse is not None \
            and (nurse.root / "sessions").is_dir() \
            and (nurse.root / "assistant" / "ASSISTANT.md").exists()
        ok_jan = ".janitor" in created and jan is not None \
            and (jan.root / "assistant" / "ASSISTANT.md").exists()
        # The CUSTODIAN tier (the Operator's spec): the janitor's FREE scan
        # lives INSIDE the .janitor profile (mirroring .nurse/doctor/),
        # not as a separate agent.
        cust_dir = jan.root / "custodian"
        ok_cust = cust_dir.is_dir()
        # The 5-SECTION instruction bodies (the Operator's spec): nurse handles
        # Athena's systems (health: diagnosis+repair), janitor the
        # architecture (performance: cleanup+optimization), with its FREE
        # custodian tier documented inside. Plus the DOCTRINE section
        # (08-12): the wiki is the known-good — each profile carries it
        # as section 2b, so the expected section count is 6.
        nurse_text = (nurse.root / "assistant" / "ASSISTANT.md").read_text()
        jan_text = (jan.root / "assistant" / "ASSISTANT.md").read_text()
        n5 = [l for l in nurse_text.splitlines() if l.startswith("## ")]
        j5 = [l for l in jan_text.splitlines() if l.startswith("## ")]
        lane_ok = "DIAGNOSIS" in nurse_text and "HEALTH" in nurse_text \
            and "CLEANUP" in jan_text and "PERFORMANCE" in jan_text \
            and "custodian" in jan_text.lower() \
            and "wiki" in nurse_text.lower() and "wiki" in jan_text.lower()
        checks.append({
            "name": "system profiles auto-create at startup",
            "status": "ok" if ok_nurse and ok_jan and ok_cust
            and len(n5) == 6 and len(j5) == 6
            and lane_ok else "fail",
            "detail": f"nurse={ok_nurse} janitor={ok_jan} custodian_dir={ok_cust} "
                      f"n5={len(n5)} j5={len(j5)} lane={lane_ok}",
        })
        _cfg.ATHENA_ROOT = _saved_root
        _sp.ATHENA_ROOT = _saved_sp
        _ip.PROFILES_DIR = _saved_profiles_dir

    # 7. CLI runtime footer: the hotbar status line.
    from cli.banner import runtime_footer
    f = runtime_footer("default")
    checks.append({
        "name": "CLI runtime footer (hotbar status)",
        "status": "ok" if "profile:" in f and "tokens:" in f else "fail",
        "detail": f[:70],
    })

    # 8. The FREE tiers run hourly (doctor + custodian); the janitor's
    #    weekly hygiene pass runs AFTER the daily/weekly repair settles
    #    (the Operator's 08-12 ordering: doctor diagnoses + nurse repairs,
    #    then the janitor optimizes).
    from autonomy.scheduler import list_jobs
    _jobs = {j["name"]: j["schedule"] for j in list_jobs()}
    doc_s = _jobs.get("doctor", "")
    cust_s = _jobs.get("custodian", "")
    jan_s = _jobs.get("janitor", "")
    hourly = doc_s == "17 * * * *" and cust_s == "27 * * * *"
    checks.append({
        "name": "doctor + custodian run hourly (free tiers)",
        "status": "ok" if hourly else "fail",
        "detail": f"doctor={doc_s} custodian={cust_s}",
    })

    # 9. The CUSTODIAN tier (the Operator's performance split): the FREE scan
    #    (zero provider) feeds the janitor's optimization pass.
    import core.custodian as cust
    orig_cust_state = cust.STATE_FILE
    cust.STATE_FILE = Path(_mkdtemp()) / "custodian.json"
    try:
        r = cust.scan()
        checks.append({
            "name": "custodian FREE scan (artifacts + dead-code)",
            "status": "ok" if isinstance(r.get("artifacts"), list)
            and isinstance(r.get("dead_code"), list) else "fail",
            "detail": f"artifacts={len(r.get('artifacts', []))} "
                      f"dead={len(r.get('dead_code', []))}",
        })
    finally:
        cust.STATE_FILE = orig_cust_state

    import core.janitor as jan_mod
    orig_jan2 = jan_mod.STATE_FILE
    jan_mod.STATE_FILE = Path(_mkdtemp()) / "janitor.json"
    try:
        j = jan_mod.run_sweep(dry_run=True)
        checks.append({
            "name": "janitor consumes custodian findings",
            "status": "ok" if "custodian_findings" in j else "fail",
            "detail": "optimization pass works from the free scan",
        })
    finally:
        jan_mod.STATE_FILE = orig_jan2
    # TEMPDIR HYGIENE: remove every tempdir this test created.
    for _d in _leaked:
        _shutil.rmtree(_d, ignore_errors=True)
    return checks

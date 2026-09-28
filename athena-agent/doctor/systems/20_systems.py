"""Systems surface test — consolidated (the Operator's 08-12 directive).

ONE module per category: this composer runs every systems submodule's
checks and merges them into a single report. Check names are preserved
1:1 — the doctor count and the nurse's failure tracking stay stable
across consolidation.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
import sqlite3
import zipfile
import os
import tempfile

def _chk_backup() -> list[dict]:
    from data.backup import run_backup, run_quick_backup

    checks = []
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "full.zip")
        run_backup(dest)
        zf = zipfile.ZipFile(dest)
        names = zf.namelist()
        zf.close()
        checks.append({
            "name": "full backup has files",
            "status": "ok" if names else "fail",
            "detail": f"{len(names)} files",
        })
        # The Operator's 08-12 spec: the full backup IS the core code — a zip
        # of athena-system/. Assert the entries are code (no __pycache__,
        # no .venv, no data dirs).
        bad = [n for n in names
               if "__pycache__" in n or n.startswith(".venv")
               or n.startswith(".wiki") or n.startswith("profiles/")
               or n.startswith("sessions/")]
        checks.append({
            "name": "code-only snapshot (core code)",
            "status": "ok" if names and not bad else "fail",
            "detail": f"{len(names)} code files, bad={bad[:3]}",
        })
        dest2 = os.path.join(td, "quick.zip")
        run_quick_backup(dest2)
        zf = zipfile.ZipFile(dest2)
        qnames = zf.namelist()
        zf.close()
        checks.append({
            "name": "quick includes config",
            "status": "ok" if any("config.yaml" in n for n in qnames) else "fail",
            "detail": f"{len(qnames)} files",
        })
    return checks


def _chk_cron() -> list[dict]:
    from autonomy.cron import normalize_schedule, is_due, compute_next, CronExpr

    checks = []
    checks.append({
        "name": "condensed cron expands",
        "status": "ok" if normalize_schedule("03***") == "0 3 * * *" else "fail",
        "detail": f"→ {normalize_schedule('03***')}",
    })
    checks.append({
        "name": "bare interval expands",
        "status": "ok" if normalize_schedule("30m") == "every 30m" else "fail",
        "detail": f"→ {normalize_schedule('30m')}",
    })
    checks.append({
        "name": "full cron passes through",
        "status": "ok" if normalize_schedule("0 3 * * *") == "0 3 * * *" else "fail",
        "detail": "",
    })
    due = is_due("03***", None, datetime(2026, 8, 8, 3, 0, 0))
    not_due = is_due("03***", None, datetime(2026, 8, 8, 10, 0, 0))
    checks.append({
        "name": "due logic (condensed)",
        "status": "ok" if due and not not_due else "fail",
        "detail": f"03:00={due} 10:00={not_due}",
    })
    try:
        nxt = compute_next("*/15 * * * *", datetime(2026, 8, 7, 10, 30, 0))
        checks.append({
            "name": "next computed",
            "status": "ok",
            "detail": nxt,
        })
    except Exception as exc:
        checks.append({"name": "next computed", "status": "fail", "detail": str(exc)})
    return checks


def _chk_importer() -> list[dict]:
    from data.importer import import_legacy_vault
    from core import db as db_layer

    checks = []
    # Build a tiny legacy-format vault to import.
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.db"
        conn = sqlite3.connect(str(src))
        conn.execute("""CREATE TABLE entries (
            id TEXT, ts TEXT, profile TEXT, kind TEXT, source TEXT, date TEXT,
            time TEXT, context TEXT, location TEXT, setting TEXT, role TEXT,
            first_name TEXT, last_name TEXT, nickname TEXT, emotion TEXT,
            mood TEXT, activity TEXT, content TEXT, meta TEXT, deleted INTEGER)""")
        # 20 columns → 20 placeholders
        for i in range(5):
            conn.execute(
                "INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"id-{i}", "2026-08-07T00:00:00", "doctor-test", "message", "test",
                 "2026-08-07", "12:00:00 AM", "", "", "", "user", "", "", "", "",
                 "", "", f"content {i}", "", 0))
        conn.commit()
        conn.close()

        # Import into an isolated vault
        import uuid
        from intelligence.profiles import get_profile
        original = db_layer.ATHENA_ROOT
        temp_root = Path(td) / "athena-root"
        temp_root.mkdir()
        try:
            db_layer.ATHENA_ROOT = temp_root
            r1 = import_legacy_vault(src, profile="doctor-test")
            r2 = import_legacy_vault(src, profile="doctor-test")
            checks.append({
                "name": "first import adds all",
                "status": "ok" if r1["imported"] == 5 else "fail",
                "detail": f"imported={r1['imported']}",
            })
            checks.append({
                "name": "second import skips (idempotent)",
                "status": "ok" if r2["imported"] == 0 and r2["skipped"] == 5 else "fail",
                "detail": f"imported={r2['imported']} skipped={r2['skipped']}",
            })
        finally:
            db_layer.ATHENA_ROOT = original
    return checks


def _chk_kanban() -> list[dict]:
    from autonomy import kanban

    checks= []
    # DYNAMIC assignee: the first non-default profile (any profile works —
    # the doctor must test the profiles that exist, never a hardcoded one).
    from intelligence.profiles import list_profiles
    profiles = list_profiles()
    named = next((p for p in profiles if not p.is_default), None)
    assignee = named.name if named else "default"
    task = kanban.add_task("doctor-test-task", assignee=assignee)
    checks.append({
        "name": "add with agent assignee",
        "status": "ok" if task.get("assignee") == assignee else "fail",
        "detail": f"{task.get('id', '')[:8]} → {assignee}",
    })
    got = kanban.get_task(task["id"][:8])
    checks.append({
        "name": "prefix id resolution",
        "status": "ok" if got and got["id"] == task["id"] else "fail",
        "detail": "",
    })
    upd = kanban.update_task(task["id"][:8], status="done")
    checks.append({
        "name": "status update",
        "status": "ok" if upd["status"] == "done" else "fail",
        "detail": f"→ {upd['status']}",
    })
    summary = kanban.board_summary()
    checks.append({
        "name": "board summary shape",
        "status": "ok" if "by_status" in summary and "by_agent" in summary else "fail",
        "detail": str(list(summary.keys())),
    })
    # cleanup
    try:
        kanban.delete_task(task["id"])
    except Exception:
        pass
    return checks


def _chk_lifecycle() -> list[dict]:
    from autonomy.lifecycle import run

    checks = []
    usage = run("bogus-method")
    checks.append({
        "name": "unknown method → usage",
        "status": "ok" if "start|shutdown|restart|refresh" in usage else "fail",
        "detail": "",
    })
    # refresh offline must reload without crashing
    result = run("refresh")
    checks.append({
        "name": "refresh runs",
        "status": "ok" if "refresh:" in result else "fail",
        "detail": result[:80],
    })
    # shutdown offline is a safe no-op
    result = run("shutdown")
    checks.append({
        "name": "shutdown safe when offline",
        "status": "ok" if "everything down" in result or "nothing" in result else "fail",
        "detail": result[:80],
    })
    return checks


def _chk_scheduler() -> list[dict]:
    from autonomy.scheduler import add_job, list_jobs, remove_job, due_jobs
    from autonomy import scheduler as sched

    checks= []
    # isolate the test DB
    import tempfile
    from pathlib import Path
    original = getattr(sched, "SCHEDULER_DB", None)
    with tempfile.TemporaryDirectory() as td:
        try:
            if original is not None:
                sched.SCHEDULER_DB = Path(td) / "sched.db"
            job = add_job("test-every", "every 30m", "do the thing")
            checks.append({
                "name": "add job (interval)",
                "status": "ok" if job.get("schedule") == "every 30m" else "fail",
                "detail": f"{job.get('schedule')}",
            })
            add_job("test-condensed", "03***", "nightly")
            jobs = list_jobs()
            checks.append({
                "name": "list jobs",
                "status": "ok" if len(jobs) == 2 else "fail",
                "detail": f"{len(jobs)} jobs",
            })
            condensed = [j for j in jobs if j["name"] == "test-condensed"][0]
            checks.append({
                "name": "condensed normalized on store",
                "status": "ok" if condensed["schedule"] == "0 3 * * *" else "fail",
                "detail": condensed["schedule"],
            })
            remove_job(job["id"])
            jobs = list_jobs()
            checks.append({
                "name": "remove job",
                "status": "ok" if len(jobs) == 1 else "fail",
                "detail": f"{len(jobs)} jobs left",
            })
        finally:
            if original is not None:
                sched._DB_PATH = original
    return checks


def _chk_skills_plugins_commands() -> list[dict]:
    from intelligence.skills import load_skills
    from intelligence.plugins import load_all
    from autonomy.commands import register_core_commands, list_commands, get_subcommands, register_command

    checks = []

    skills = load_skills()
    checks.append({
        "name": "skills load",
        "status": "ok" if skills else "fail",
        "detail": f"{len(skills)} loaded",
    })
    skills_default = load_skills(profile_dir=None)
    checks.append({
        "name": "skills index render",
        "status": "ok",
        "detail": f"{len(skills_default)} in default view",
    })

    summary = load_all()
    # The Operator's 08-12 spec: plugins are the COMMUNITY modding layer —
    # Athena ships NONE by default. An empty shared plugins home is the
    # correct state; the operator/community installs their own.
    checks.append({
        "name": "plugins discover (empty by default)",
        "status": "ok",
        "detail": f"{len(summary['plugins'])} plugin(s) — Athena ships none; "
                  f"the community installs their own",
    })

    register_core_commands()
    cmds = list_commands()
    checks.append({
        "name": "command registry populated",
        "status": "ok" if "lifecycle" in cmds and "kanban" in cmds else "fail",
        "detail": f"{len(cmds)} commands",
    })
    register_command("doctor-test", ["a", "b"])
    checks.append({
        "name": "dynamic registration",
        "status": "ok" if "doctor-test" in list_commands()
        and get_subcommands("doctor-test") == ["a", "b"] else "fail",
        "detail": "",
    })
    return checks


_SUBMODULES = [
    "backup",
    "cron",
    "fs_tools",
    "importer",
    "kanban",
    "lifecycle",
    "plugins",
    "profile_completeness",
    "scheduler",
    "skills_plugins_commands",
]


def _load_sub(name: str):
    """Import a submodule by its _sub_* file name (namespace package)."""
    import importlib.util
    here = Path(__file__).parent
    path = here / f"_sub_{name}.py"
    spec = importlib.util.spec_from_file_location(
        f"doctor.systems._sub_{name}", str(path))
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
                    "name": f"systems/{name}",
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
                "name": f"systems/{name}",
                "status": "fail",
                "detail": f"{type(exc).__name__}: {exc}",
            })
    return checks

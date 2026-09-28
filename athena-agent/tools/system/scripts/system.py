"""Built-in system family — machine health + the domain's system tools.

The Operator's 08-12 spec: a `system` tool BUNDLED with the doctor,
custodian, nurse, and janitor tools — they are the DOMAIN's system
tools. The family reports machine health (CPU/memory/disk/processes) and
the status of each domain subsystem.

The status actions are read-only: they report what the subsystem
WOULD do / has done, without running a full pass (the doctor/custodian
run on their own schedules; this is the agent's at-a-glance view).
"""

import json
import os
import shutil
from pathlib import Path


def _system_info(args: dict, timeout: float = 10.0) -> str:
    import platform
    uname = os.uname()
    mem_total = 0
    mem_used = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) // 1024  # kB → MB
                elif line.startswith("MemAvailable:"):
                    mem_used = mem_total - int(line.split()[1]) // 1024
    except Exception:
        pass
    return json.dumps({
        "ok": True,
        "os": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "hostname": uname.nodename,
        "cores": os.cpu_count(),
        "memory_mb_total": mem_total,
        "memory_mb_used": mem_used,
        "python": platform.python_version(),
    }, ensure_ascii=False)


def _process_list(args: dict, timeout: float = 10.0) -> str:
    limit = int(args.get("limit", 20))
    try:
        procs = []
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/comm") as f:
                    name = f.read().strip()
                with open(f"/proc/{pid}/status") as f:
                    state = ""
                    for line in f:
                        if line.startswith("State:"):
                            state = line.split()[1]
                            break
                procs.append({"pid": int(pid), "name": name, "state": state})
            except Exception:
                continue
        procs.sort(key=lambda p: p["pid"])
        return json.dumps({"ok": True, "processes": procs[:limit],
                           "total": len(procs)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False)


def _disk_usage(args: dict, timeout: float = 10.0) -> str:
    path = str(args.get("path", "/"))
    try:
        usage = shutil.disk_usage(path)
        return json.dumps({
            "ok": True,
            "path": path,
            "total_gb": round(usage.total / 1e9, 1),
            "used_gb": round(usage.used / 1e9, 1),
            "free_gb": round(usage.free / 1e9, 1),
            "percent_used": round(usage.used / usage.total * 100, 1),
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False)


def _subsystem_status(name: str) -> str:
    """A read-only status probe for a domain subsystem."""
    from core.config import ATHENA_ROOT
    if name == "doctor":
        # The doctor's last report + the boot pass result.
        detail = "doctor: schedule hourly (17 * * * *); boot pass at startup"
        try:
            logs = sorted((ATHENA_ROOT / "profiles" / ".default" / "logs").glob("*.log"))
            if logs:
                detail += f"; {len(logs)} log file(s)"
        except Exception:
            pass
        return json.dumps({"ok": True, "system": "doctor", "detail": detail},
                          ensure_ascii=False)
    if name == "custodian":
        return json.dumps({"ok": True, "system": "custodian",
                           "detail": "custodian: schedule hourly (27 * * * *); "
                                     "scans for artifacts/dead code"},
                          ensure_ascii=False)
    if name == "nurse":
        return json.dumps({"ok": True, "system": "nurse",
                           "detail": "nurse: the repair agent; consults the "
                                     "doctor's findings, diagnoses + repairs"},
                          ensure_ascii=False)
    if name == "janitor":
        return json.dumps({"ok": True, "system": "janitor",
                           "detail": "janitor: the maintenance agent; cleans "
                                     "logs/state per the doctrine"},
                          ensure_ascii=False)
    return json.dumps({"ok": False, "detail": f"unknown subsystem: {name}"},
                      ensure_ascii=False)


# ── CORE-SYSTEM actions (memory / emotion / vault / session) ─────────
def _memory(args: dict, timeout: float = 10.0) -> str:
    from intelligence.memory import read_entries, add_entry, clear
    op = str(args.get("op", "read")).strip()
    side = str(args.get("side", "assistant")).strip()
    if op == "read":
        entries = read_entries(side, str(args.get("profile", "")))
        return json.dumps({"ok": True, "side": side, "entries": entries[:20],
                           "total": len(entries)}, ensure_ascii=False)
    if op == "add":
        content = str(args.get("content", "")).strip()
        if not content:
            return json.dumps({"ok": False, "detail": "content required"},
                              ensure_ascii=False)
        add_entry(side, content, str(args.get("profile", "")))
        return json.dumps({"ok": True, "side": side, "added": content[:80]},
                          ensure_ascii=False)
    if op == "clear":
        clear(side, str(args.get("profile", "")))
        return json.dumps({"ok": True, "side": side, "cleared": True},
                          ensure_ascii=False)
    return json.dumps({"ok": False, "detail": f"unknown memory op: {op}"},
                      ensure_ascii=False)


def _emotion(args: dict, timeout: float = 10.0) -> str:
    from core import emotion as emo
    op = str(args.get("op", "table")).strip()
    if op == "table":
        grid = emo.table_grid()
        return json.dumps({"ok": True, "table": grid}, ensure_ascii=False)
    if op == "highlight":
        vector = args.get("vector") or {}
        cells = emo.highlight_cells(vector)
        return json.dumps({"ok": True, "vector": vector, "cells": cells},
                          ensure_ascii=False)
    if op == "name":
        axis = str(args.get("axis", "")).strip()
        value = float(args.get("value", 0))
        name = emo.emotion_name(axis, value)
        return json.dumps({"ok": True, "axis": axis, "value": value,
                           "name": name}, ensure_ascii=False)
    return json.dumps({"ok": False, "detail": f"unknown emotion op: {op}"},
                      ensure_ascii=False)


def _vault(args: dict, timeout: float = 10.0) -> str:
    from core.db import record_vault_entry, vault_path, connect_vault
    # The vault's default profile is "default" (not "").
    profile = str(args.get("profile", "")).strip() or "default"
    op = str(args.get("op", "query")).strip()
    if op == "record":
        kind = str(args.get("kind", "")).strip()
        content = str(args.get("content", "")).strip()
        if not content:
            return json.dumps({"ok": False, "detail": "content required"},
                              ensure_ascii=False)
        # kind/type both map to the entry's `type` column.
        record_vault_entry(kind or None, content, profile=profile,
                           type=kind or None)
        return json.dumps({"ok": True, "recorded": content[:80]},
                          ensure_ascii=False)
    if op == "query":
        # Read the RAW entries table (the agent's own records), not the
        # curator-built TOC (index.db) — that is the curator's summary.
        try:
            conn = connect_vault(profile)
            sql = ("SELECT id, type, content, date, time, deleted "
                   "FROM entries")
            params: list = []
            cat = str(args.get("category", "")).strip()
            if cat:
                sql += " WHERE type=?"
                params.append(cat)
            sql += " ORDER BY rowid DESC LIMIT ?"
            params.append(int(args.get("limit", 20)))
            rows = conn.execute(sql, params).fetchall()
            conn.close()
            return json.dumps({
                "ok": True, "profile": profile,
                "rows": [{"id": r[0], "type": r[1],
                          "content": (r[2] or "")[:200],
                          "date": r[3], "time": r[4]}
                         for r in rows if not r[5]],
            }, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "detail": f"vault query error: {exc}"},
                              ensure_ascii=False)
    return json.dumps({"ok": False, "detail": f"unknown vault op: {op}"},
                      ensure_ascii=False)


def _session(args: dict, timeout: float = 10.0) -> str:
    from core.session_state import get_state, active_sessions, drop_session
    op = str(args.get("op", "list")).strip()
    sid = str(args.get("session_id", "")).strip()
    if op == "list":
        return json.dumps({"ok": True, "active": active_sessions()},
                          ensure_ascii=False)
    if op == "state":
        if not sid:
            return json.dumps({"ok": False, "detail": "session_id required"},
                              ensure_ascii=False)
        state = get_state(sid)
        return json.dumps({"ok": True, "session_id": sid, "state": str(state)},
                          ensure_ascii=False)
    if op == "drop":
        if not sid:
            return json.dumps({"ok": False, "detail": "session_id required"},
                              ensure_ascii=False)
        drop_session(sid)
        return json.dumps({"ok": True, "dropped": sid}, ensure_ascii=False)
    return json.dumps({"ok": False, "detail": f"unknown session op: {op}"},
                      ensure_ascii=False)


def _kanban(args: dict, timeout: float = 10.0) -> str:
    """The Queen/Worker/Drone board (the Operator's agent-spawn spec):
    delegate tasks, spawn subagents, list/update tasks."""
    from autonomy.kanban import (add_task, list_tasks, update_task,
                                 spawn_subagent, delegate)
    op = str(args.get("op", "list")).strip()
    if op == "list":
        tasks = list_tasks(assignee=str(args.get("assignee", "")),
                           status=str(args.get("status", "")),
                           limit=int(args.get("limit", 50)))
        return json.dumps({"ok": True, "tasks": tasks[:20]}, ensure_ascii=False)
    if op == "add":
        title = str(args.get("title", "")).strip()
        if not title:
            return json.dumps({"ok": False, "detail": "title required"},
                              ensure_ascii=False)
        task = add_task(title, body=str(args.get("body", "")),
                        assignee=str(args.get("assignee", "")))
        return json.dumps({"ok": True, "task": str(task)[:200]},
                          ensure_ascii=False)
    if op == "update":
        tid = str(args.get("task_id", "")).strip()
        if not tid:
            return json.dumps({"ok": False, "detail": "task_id required"},
                              ensure_ascii=False)
        update_task(tid, title=str(args.get("title", "")) or None,
                    status=str(args.get("status", "")) or None)
        return json.dumps({"ok": True, "updated": tid}, ensure_ascii=False)
    if op == "delegate":
        title = str(args.get("title", "")).strip()
        assignee = str(args.get("assignee", "")).strip()
        if not title or not assignee:
            return json.dumps({"ok": False,
                               "detail": "title and assignee required"},
                              ensure_ascii=False)
        r = delegate(title, assignee, created_by="athena")
        return json.dumps({"ok": True, "delegated": title,
                           "to": assignee, "detail": str(r)[:200]},
                          ensure_ascii=False)
    if op == "spawn":
        title = str(args.get("title", "")).strip()
        body = str(args.get("body", "")).strip()
        if not title:
            return json.dumps({"ok": False, "detail": "title required"},
                              ensure_ascii=False)
        r = spawn_subagent("athena", title, body)
        return json.dumps({"ok": True, "spawned": title,
                           "detail": str(r)[:200]}, ensure_ascii=False)
    return json.dumps({"ok": False, "detail": f"unknown kanban op: {op}"},
                      ensure_ascii=False)


def _system(args: dict, timeout: float = 10.0) -> str:
    action = str(args.get("action", "")).strip()
    if action == "system_info":
        return _system_info(args, timeout)
    if action == "process_list":
        return _process_list(args, timeout)
    if action == "disk_usage":
        return _disk_usage(args, timeout)
    if action in ("doctor", "custodian", "nurse", "janitor"):
        return _subsystem_status(action)
    if action == "memory":
        return _memory(args, timeout)
    if action == "emotion":
        return _emotion(args, timeout)
    if action == "vault":
        return _vault(args, timeout)
    if action == "session":
        return _session(args, timeout)
    if action == "kanban":
        return _kanban(args, timeout)
    return json.dumps({"ok": False, "detail": f"unknown action: {action}"},
                      ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="system",
        description="System tools BUNDLE (the Operator's 08-12 spec): machine "
                    "health (system_info, process_list, disk_usage), the "
                    "domain subsystem status (doctor, custodian, nurse, "
                    "janitor — read-only probes), and the CORE systems "
                    "(memory read/add/clear, emotion table/highlight/name, "
                    "vault record/query, session list/state/drop, "
                    "kanban list/add/delegate/spawn).",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["system_info", "process_list",
                                    "disk_usage", "doctor", "custodian",
                                    "nurse", "janitor",
                                    "memory", "emotion", "vault", "session",
                                    "kanban"]},
                "path": {"type": "string", "description": "Path for disk_usage"},
                "limit": {"type": "integer", "description": "Process limit"},
                "op": {"type": "string", "description": "Subsystem operation"},
                "side": {"type": "string",
                         "enum": ["assistant", "user"],
                         "description": "Memory side"},
                "content": {"type": "string", "description": "Content to store"},
                "profile": {"type": "string", "description": "Profile name"},
                "kind": {"type": "string", "description": "Vault entry kind"},
                "category": {"type": "string", "description": "Vault category"},
                "axis": {"type": "string", "description": "Emotion axis"},
                "value": {"type": "number", "description": "Emotion value"},
                "vector": {"type": "object", "description": "Emotion vector"},
                "session_id": {"type": "string"},
                "assignee": {"type": "string", "description": "Kanban assignee"},
                "title": {"type": "string", "description": "Kanban task title"},
                "body": {"type": "string", "description": "Kanban task body"},
                "task_id": {"type": "string", "description": "Kanban task id"},
                "status": {"type": "string", "description": "Kanban status"},
            },
            "required": ["action"],
        },
        fn=_system,
    ))
    return ["system"]

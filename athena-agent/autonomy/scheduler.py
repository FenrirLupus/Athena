"""Scheduler — the autonomy engine (the "midnight watch").

A cron table of recurring jobs. Each tick (the server loop already ticks
every N seconds) the scheduler checks which jobs are due and fires them as
SYSTEM-channel thoughts into the ConversationLoop's think queue.

Job table (sessions/scheduler.db):

    jobs:
        id          TEXT PRIMARY KEY (UUID)
        name        TEXT
        schedule    TEXT   — cron expr "0 9 * * *" | "every 1h" | ISO one-shot
        prompt      TEXT   — the thought content to fire
        enabled     INTEGER DEFAULT 1
        last_run_at TEXT
        next_run_at TEXT

The scheduler never runs an LLM itself — it only FEEDS the think gate.
The thinking budget decides whether the thought actually spends.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from core.config import ATHENA_ROOT, DEFAULT_PROFILE_ROOT
from autonomy.cron import compute_next, is_due

# The standing services the scheduler runs. STATE (not conversation):
# the operations/ directory in the profile root holds the machinery
# state — sessions/ stays pure conversation (session files + vault).
SCHEDULER_DB = DEFAULT_PROFILE_ROOT / "operations" / "scheduler.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'custom',
    schedule    TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    script      TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    next_run_at TEXT
);
"""


def _conn() -> sqlite3.Connection:
    SCHEDULER_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SCHEDULER_DB))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Migration: older DBs lack the type column — add it if missing.
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)")]
        if "type" not in cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN type TEXT NOT NULL DEFAULT 'custom'")
            conn.commit()
        if "script" not in cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN script TEXT")
            conn.commit()
    except Exception:
        pass
    conn.commit()
    return conn


def add_job(name: str, schedule: str, prompt: str, *, job_type: str = "custom",
            script: str = "") -> dict:
    """Register a recurring job.

    schedule: cron expr "0 9 * * *" | interval "every 30m" | ISO one-shot.
    job_type: the cadence label — hourly | daily | weekly | monthly |
              yearly | custom (custom = explicit H/M/S interval).
    script (optional): a MECHANICAL job — run this Python expression /
    command directly (no LLM thought, zero tokens). The script-only path
    exists so purely mechanical services (restart, backup) don't spend
    the reasoning budget on a thought that just calls a function.
    Short forms are normalized: "03***" → "0 3 * * *", "30m" → "every 30m".
    """
    from autonomy.cron import normalize_schedule

    job_id = str(uuid.uuid4())
    now = datetime.now()
    sched = normalize_schedule(schedule)
    if job_type not in ("hourly", "daily", "weekly", "monthly", "yearly", "custom"):
        job_type = "custom"
    with _conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id, name, type, schedule, prompt, script, enabled, next_run_at)"
            " VALUES (?,?,?,?,?,?,1,?)",
            (job_id, name, job_type, sched, prompt, script or None,
             compute_next(sched, now)),
        )
    return {"id": job_id, "name": name, "type": job_type, "schedule": sched}


def list_jobs() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY enabled DESC, name").fetchall()
        return [dict(r) for r in rows]


def remove_job(job_id: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))


# The standing services the RUNTIME guarantees on every boot. The NAME is
# the service (doctor, curator, restart); the TYPE is the cadence:
#   hourly | daily | weekly | monthly | yearly | custom (explicit H/M/S)
# Tuple: (name, type, schedule, prompt, script="") — script makes the job
# MECHANICAL (run directly, no LLM thought, zero tokens).
SERVICES: list[tuple[str, str, str, str, str]] = [
    ("doctor", "hourly", "17 * * * *",
     "Run the full doctor diagnosis of the Athena system (the Operator's "
     "08-12 spec: the FREE diagnostic tier runs at boot + every hour — "
     "zero provider calls, pure Python checks). Call the doctor runner "
     "and print the COMPACT SUMMARY ONLY (the full report is 40KB+ and "
     "overflowing the service's stdout pipe caused broken-pipe failures). "
     "If there are failures, note which checks failed and their severity "
     "levels.",
     # THE READ-ONLY HOURLY PASS (the Operator's 08-12 deletion fix): the
     # unattended hourly job runs run_all(live=True) — the state-mutating
     # tests (snapshots, profile switches, wipe) are SKIPPED so an hourly
     # audit can never touch the live tree. The FULL isolated suite
     # (run_isolated) is the OPERATOR's manual deep audit (`athena
     # doctor`), never an unattended cron.
     "from doctor.run import run_all; _r=run_all(live=True); _s=_r.get('summary',{}); "
     "print(f\"doctor: {_s.get('ok',0)} ok, {_s.get('warn',0)} warn, {_s.get('fail',0)} fail\")"),
    ("custodian", "hourly", "27 * * * *",
     "Run the CUSTODIAN's free performance scan (the Operator's 08-12 spec: "
     "the zero-provider scan of disposable artifacts + dead-code "
     "candidates, feeding the janitor). From core.custodian import scan; "
     "call scan() and report the artifact/dead-code counts.",
     "from core.custodian import scan as _s; _f=_s(); _a=len(_f.get('artifacts') or []); _d=len(_f.get('dead_code') or []); print(f'scan: {_a} artifacts, {_d} dead-code');"),
    ("curator", "daily", "0 3 * * *",
     "Run the curator's learn-by-doing pass. From intelligence.curator "
     "import review; call review(dry_run=False). It scans the session.db and "
     "events logs, creates skills from proven tool use, merges duplicate "
     "sections, archives stale skills. Report what was created, merged, or "
     "archived — or that nothing was needed."),
    ("restart", "daily", "5 0 * * *",
     "Restart the Athena server: the 24h session rotation. A fresh session "
     "begins, a new metric log file and event log file start. Use the "
     "lifecycle restart method.",
     "from autonomy.lifecycle import restart; print(restart())"),
    ("backup", "daily", "30 1 * * *",
     "Run the daily backup: from data.backup import run_backup; call "
     "run_backup() (default output snapshots/backups/). Report the backup "
     "path and file count. The backups/ scheme must never sit empty.",
     "from data.backup import run_backup; print(run_backup())"),
    ("wiki", "daily", "45 1 * * *",
     "Sync the local wiki mirror (the Operator's 08-12 spec): the local "
     ".athena/.wiki/ must ALWAYS be the exact 1:1 copy of the remote "
     "wiki. Use core.wiki.sync_wiki() — a fresh clone with an ATOMIC "
     "SWAP (clone to .wiki.new, swap in, delete old) so the mirror is "
     "never missing and no stale/deleted page can survive. Requires an "
     "internet connection; on failure the previous copy stays usable.",
     "from core.wiki import sync_wiki; _r=sync_wiki(); print(f'wiki: {\"synced\" if _r.get(\"ok\") else \"FAILED\"} ({_r.get(\"pages\",0)} pages)')"),
    ("enrich", "hourly", "0 * * * *",
     "The knowledge ENRICHMENT pass (the Operator's spec): FIRST run the free "
     "change-detecting gate by executing the script knowledge/enrich_gate.py. "
     "If it prints 'changed:true' (the vault was modified in the last hour), "
     "then run the enrichment sweep: from knowledge.enrich import run_once; "
     "call run_once(profile='') which fills each incomplete row one by one, "
     "using the +/-3 sliding window (previous + next history) to fill "
     "context/setting/location/emotion/mood/activity ONLY where applicable "
     "from the content. If the gate prints nothing, report 'no changes - "
     "nothing to enrich'."),
    ("janitor", "weekly", "0 6 * * 1",
     "The JANITOR hygiene pass (the Operator's spec): run the cleaning sweep. "
     "Runs Monday 06:00 — four hours AFTER the doctor's weekly pass "
     "(Monday 02:00), so repairs settle first, then the architecture is "
     "optimized and cleaned. From core.janitor import run_sweep; call "
     "run_sweep(dry_run=True) — it scans disposable artifacts outside "
     "athena-system (workspace scratch, stale temp) and REPORTS dead-code "
     "candidates inside the system. Report what was found; never delete "
     "code, never touch the vault."),
]


def ensure_services() -> list[str]:
    """Idempotently register the standing services (called at server boot).

    Adds any service that is missing; never duplicates. When a service's
    DEFINITION changed (schedule, prompt, or script), the stored job is
    UPDATED to match — the Operator's doctrine: every fix ships domain-wide,
    never a one-off drift between code and the scheduler DB.
    Returns the names registered/updated this boot.
    """
    existing = {j["name"]: j for j in list_jobs()}
    added = []
    with _conn() as conn:
        for entry in SERVICES:
            name, job_type, schedule, prompt = entry[0], entry[1], entry[2], entry[3]
            script = entry[4] if len(entry) > 4 else ""
            if name not in existing:
                add_job(name, schedule, prompt, job_type=job_type, script=script)
                added.append(f"{name}:{job_type}" + ("(script)" if script else ""))
                continue
            cur = existing[name]
            # Normalize: the DB stores script=None for empty; compare
            # treating None and "" as equal (a scriptless service stays
            # scriptless across boots — no false drift).
            cur_script = cur.get("script") or ""
            new_script = script or ""
            if (cur.get("schedule") != schedule
                    or (cur.get("prompt") or "") != (prompt or "")
                    or cur_script != new_script):
                conn.execute(
                    "UPDATE jobs SET schedule=?, prompt=?, script=?, type=? "
                    "WHERE name=?", (schedule, prompt, new_script or None,
                                     job_type, name))
                added.append(f"{name}:updated")
    return added


def due_jobs(now: datetime | None = None) -> list[dict]:
    """Enabled jobs that are due at this moment (per their schedule form)."""
    now = now or datetime.now()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE enabled=1"
        ).fetchall()
        return [dict(r) for r in rows if is_due(r["schedule"], r["last_run_at"], now)]


def mark_run(job_id: str, now: datetime | None = None) -> None:
    """Update last_run_at and advance next_run_at."""
    now = now or datetime.now()
    now_iso = now.isoformat(timespec="seconds")
    with _conn() as conn:
        row = conn.execute("SELECT schedule FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return
        conn.execute(
            "UPDATE jobs SET last_run_at=?, next_run_at=? WHERE id=?",
            (now_iso, compute_next(row["schedule"], now), job_id),
        )


def tick(conversation, now: datetime | None = None) -> list[str]:
    """The scheduler's tick: fire due jobs as system-channel thoughts.

    Called from the ServerLoop's tick. Returns the fired job names.
    Also feeds the kanban board: each agent (profile) with open work gets
    a thought to pick up their queue (Layer 5 — profile-as-agent).
    """
    fired = []
    for job in due_jobs(now):
        # SCRIPT-ONLY job (the Operator's efficiency): a mechanical service
        # (restart, backup) runs directly — zero LLM tokens. The prompt
        # is only the fallback description for LLM-driven jobs.
        if job.get("script"):
            try:
                exec(job["script"], {"__name__": "__scheduler__"})
                from core.logging import log_event
                log_event(2, f"scheduled script job ran: {job['name']}",
                          source="autonomy", action="scheduler_script")
            except Exception as exc:
                from core.logging import log_event
                log_event(4, f"scheduled script job failed: {job['name']}: {exc}",
                          source="autonomy", action="scheduler_script")
        else:
            conversation.handle_thought(job["prompt"], priority=0.6)
        mark_run(job["id"], now)
        fired.append(job["name"])

    # Kanban feeder: agents with open work get a work thought. The NURSE
    # gets a consultation thought instead — her work IS diagnosis+repair,
    # handled by doctor/nurse.consult (managed, never arbitrary).
    #
    # ROUTING FIX: this scheduler tick runs inside ONE runtime (the active
    # profile's conversation). Only tasks assigned to THAT profile are
    # fired here; other agents' tasks are logged as pending (they are
    # picked up when their own profile's server runs) — never misrouted
    # into the wrong agent's queue.
    #
    # THE BOOT READINESS GATE (the Operator's 08-12 start-fix): at the
    # very first tick the agent/ dirs + kanban DBs may not exist yet
    # (ensure_all creates them during boot). If the boards aren't there,
    # DEFER to the next tick — never crash the boot with "unable to
    # open database file" (the failed-start race the Operator hit).
    try:
        from autonomy.kanban import _boards_ready
        if not _boards_ready():
            return fired
    except Exception:
        pass
    try:
        from autonomy.kanban import board_summary, open_work_for
        from doctor.nurse import NURSE_AGENT, consult
        from core.logging import log_event

        own = getattr(getattr(conversation, "profile", None), "name", "default")
        summary = board_summary()
        for agent, count in summary.get("by_agent", {}).items():
            work = open_work_for(agent)
            if not work:
                continue
            first = work[0]
            if agent == NURSE_AGENT:
                # The nurse's queue: run the consultation for the next task.
                # AUTONOMOUS GATE (the Operator's 08-12 session fix): the
                # unattended scheduler fires a DIAGNOSIS-ONLY consult —
                # never the repair+restore loop that rewrites code and
                # restarts (which wiped operator sessions). Repair runs
                # only on the operator's explicit command.
                try:
                    result = consult(first["id"], autonomous=True)
                    # The nurse's ack goes into HER OWN session (.nurse),
                    # never the .default conversation (the Operator's
                    # home rule: agent work lives in the agent's home).
                    from doctor.nurse import nurse_talk
                    nurse_talk(
                        f"[nurse] consultation complete on '{first['title']}': "
                        f"{result.get('still_failing', '?')} still failing. "
                        f"See the task body for the report.",
                        side="assistant",
                    )
                    fired.append(f"nurse:{first['id'][:8]}")
                except Exception as exc:
                    from core.logging import log_event
                    log_event(4, f"nurse consultation failed: {exc}",
                              source="autonomy", action="nurse_consult")
                    from doctor.nurse import nurse_talk
                    nurse_talk(
                        f"[nurse] consultation failed on '{first['title']}': {exc}",
                        side="assistant",
                    )
                    fired.append(f"nurse-error:{first['id'][:8]}")
                continue
            if agent != own:
                # NOT this runtime's profile — log it as pending for the
                # right agent; do NOT push it into this queue.
                log_event(2, f"kanban task '{first['title']}' pending for {agent} "
                          f"(id {first['id'][:8]}) — this runtime is {own}",
                          source="autonomy", action="kanban_pending")
                continue
            conversation.handle_thought(
                f"Your agent queue has {count} open task(s). "
                f"Next: [{first['status']}] {first['title']} "
                f"(id {first['id'][:8]}, priority {first.get('priority', 0)}). "
                f"Pick up your work."
                + (_delegation_hint(first)),
                priority=0.5,
            )
            fired.append(f"kanban:{agent}")
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"kanban feeder failed: {exc}", source="autonomy",
                  action="kanban_feed")

    # Subagent LIFECYCLE (the Operator's spec): reap stale runners + cleanup
    # old finished workers FIRST, then run one queued subagent. Scoped
    # to THIS runtime's profile board — a bee runs its OWN drones.
    try:
        from autonomy.kanban import (reap_stale, cleanup_done,
                                     next_subagent, complete_subagent)
        reaped = reap_stale(profile=own)
        for sid in reaped:
            from core.logging import log_event
            log_event(3, f"subagent {sid[:8]} reaped (stale)",
                      source="autonomy", action="subagent_reap")
        cleanup_done(keep=50, profile=own)
        sub = next_subagent(profile=own)
        if sub is not None:
            try:
                result = _run_subagent(sub)
                complete_subagent(sub["id"], result)
                fired.append(f"subagent:{sub['id'][:8]}:done")
            except Exception as exc:
                from core.logging import log_event
                log_event(4, f"subagent {sub['id'][:8]} failed: {exc}",
                          source="autonomy", action="subagent_run")
                complete_subagent(sub["id"], str(exc), failed=True)
                fired.append(f"subagent:{sub['id'][:8]}:failed")
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"subagent pool failed: {exc}", source="autonomy",
                  action="subagent_pool")

    return fired


def _run_subagent(sub: dict, max_tokens_hint: int = 0) -> str:
    """Run one subagent through the message loop. Returns its reply.

    The subagent is a bounded turn: the task body is the instruction, the
    reply is the result that returns to the parent. Uses the provider
    chain; a failure surfaces as text (never crashes the tick).
    """
    from core.message_loop import MessageLoop
    from providers.provider import ProviderChain
    from core.config import load_config

    cfg = load_config()
    iter_cfg = cfg.get("iteration_budget", {})
    main_iter = int(iter_cfg.get("main_iterations", 100))
    sub_iter = int(iter_cfg.get("subagent_iterations", max(1, main_iter // 2)))
    # Token caps (the Operator's spec): main 5120, subagents 2560 (50%).
    sub_max_tokens = iter_cfg.get("subagent_max_tokens")
    if sub_max_tokens:
        sub_max_tokens = int(sub_max_tokens)
    else:
        main_max_tokens = cfg.get("message_loop", {}).get("max_tokens")
        sub_max_tokens = int(main_max_tokens) // 2 if main_max_tokens else None

    providers = ProviderChain(cfg)
    loop = MessageLoop(providers=providers,
                       system_prompt=(
                           "You are a subagent worker in the Athena hive. "
                           "Complete the assigned task, use tools as needed, "
                           "and reply with ONLY the result the parent needs. "
                           "Be direct and factual.\n"
                           # THE EMPTY-SANDBOX SEED (the Operator's 08-12 fix):
                           # a subagent's sandbox starts EMPTY — the model
                           # used to explore it (ls → identical reads) and
                           # the loop-guard blocked it as a read-loop. The
                           # seed tells the worker NOT to explore; it only
                           # reads/writes files it was explicitly given.
                           "Your sandbox starts EMPTY — do not list or read "
                           "it to 'explore'; there is nothing there unless "
                           "the task gave you a file path. Use tools for the "
                           "task itself, not for exploration.\n"
                           # THE STATUS-SEED (the Operator's 08-12 fix): a
                           # status/health question does NOT need filesystem
                           # exploration — answer from your knowledge + the
                           # environment you already have. Never ls/read to
                           # show system info; the empty sandbox has
                           # nothing informative.
                           "Answer status/health questions from your "
                           "knowledge + environment — never explore the "
                           "filesystem for them."),
                       max_iterations=sub_iter,
                       max_tokens=sub_max_tokens,
                       subagent=True)
    turn = loop.run_turn(str(sub.get("body", sub.get("title", ""))))
    # THE SESSION PERSISTENCE (the Operator's 08-12 fix): the subagent's
    # turn is recorded as an AGENT session (the profile's agent/sessions/)
    # — the caller gets the stateless reply, but the conversation persists
    # for cross-diagnosis (the MCP chat gap the Operator caught).
    try:
        import uuid as _uuid
        from core.db import record_session_message
        sid = f"{_uuid.uuid4()}"
        record_session_message(sid, "user", str(sub.get("body", "")),
                               profile="default", kind="agent")
        record_session_message(sid, "assistant", turn.reply.strip(),
                               profile="default", kind="agent")
    except Exception:
        pass
    return turn.reply.strip() or "(no output)"


def _delegation_hint(task: dict) -> str:
    """How the agent should read a task's origin (queen vs peer).

    The hive model: Athena (the default profile) is the queen — her
    delegated tasks are the highest authority. Profile-to-profile help is
    lower priority; the nurse is a special lane.
    """
    created_by = task.get("created_by", "")
    priority = int(task.get("priority", 0) or 0)
    if created_by in ("athena", "default", "system") and priority >= 10:
        return " — Athena (the administrator) delegated this; treat it as top priority."
    if created_by and priority >= 5:
        return f" — a fellow agent ({created_by}) asked for help; help if you can."
    return ""

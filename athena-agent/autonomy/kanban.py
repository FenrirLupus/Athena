"""Kanban — the work layer (Layer 5).

Each PROFILE is an agent. The hive model (the Operator's spec):

    Queen Bee  = .default (Athena)  — the administrator; delegates work
    Worker Bee = .nurse/.janitor/any profile — perform the queen's work
    Drone Bee  = subagents — spawned by ANY agent to aid their task

Every agent owns its OWN board, stored in its profile's agent/
directory (mirroring the boards pattern: one kanban root, one
board per agent). The queen's board is the admin board; task CRUD is
hive-aware (a task is found wherever it lives); delegation writes the
task INTO the assignee's board so the worker sees it in their queue.

Board layout:
    profiles/.default/agent/kanban.db   ← the queen's (admin) board
    profiles/<name>/agent/kanban.db      ← each worker bee's own board

    tasks:
        id          TEXT PRIMARY KEY (UUID)
        title       TEXT NOT NULL
        body        TEXT
        assignee    TEXT           — the PROFILE (agent) working it
        status      TEXT           — todo | in_progress | done | blocked
        priority    INTEGER
        created_by  TEXT
        created_at  TEXT
        started_at  TEXT
        completed_at TEXT
    task_links:
        parent_id   TEXT           — goal → subtask decomposition
        child_id    TEXT
    task_comments:
        id, task_id, author, body, created_at
    subagents (Drones):
        id, parent, title, body, status, result, timestamps
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from core.config import ATHENA_ROOT, DEFAULT_PROFILE_ROOT
from intelligence.profiles import PROFILES_DIR

# The QUEEN's board — the default profile's agent dir. Kept as a module
# constant for back-compat and doctor assertions (parent dir == "agent").
# Every profile's board resolves through board_path(); doctor tests patch
# BOARDS_ROOT for isolation (never the real profile dirs).
KANBAN_DB = DEFAULT_PROFILE_ROOT / "agent" / "kanban.db"
BOARDS_ROOT = PROFILES_DIR

STATUSES = ("todo", "in_progress", "done", "blocked")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    body         TEXT,
    assignee     TEXT,             -- the PROFILE (agent) working this task
    status       TEXT NOT NULL DEFAULT 'todo',
    priority     INTEGER NOT NULL DEFAULT 0,
    created_by   TEXT,
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS task_links (
    parent_id TEXT NOT NULL,
    child_id  TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id)
);
CREATE TABLE IF NOT EXISTS task_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    author     TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subagents (
    id          TEXT PRIMARY KEY,
    parent      TEXT NOT NULL,       -- the agent that spawned it (athena | profile)
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,       -- the task: what to do
    status      TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|failed
    result      TEXT,                -- the worker's return value
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee, status);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


def board_path(profile: str = "") -> Path:
    """The board DB for a profile (agent).

    Queen/empty/.default → <BOARDS_ROOT>/.default/agent/kanban.db
    Named profile       → <BOARDS_ROOT>/<name>/agent/kanban.db
    Unknown agent       → the queen's board (a task always has a home).

    BOARDS_ROOT defaults to the profiles home; doctor tests patch it to
    a tempdir for isolation (never touching the real profile dirs).
    """
    name = (profile or "").strip()
    if not name or name in ("default", ".default", "athena"):
        name = ".default"
    root = Path(BOARDS_ROOT) / name
    if not root.is_dir():
        # Unknown agent or missing layout — fall back to the queen's board.
        root = Path(BOARDS_ROOT) / ".default"
    return root / "agent" / "kanban.db"


def _boards_ready() -> bool:
    """True when the profile board dirs exist (the boot readiness gate).

    At the very first scheduler tick the agent/ dirs may not exist yet
    (ensure_all creates them during boot). The feeder defers instead of
    crashing on "unable to open database file" (the 08-12 start-fix).
    """
    from pathlib import Path
    for name in (".default", ".nurse", ".janitor"):
        if not (Path(BOARDS_ROOT) / name / "agent").is_dir():
            return False
    return True


def _conn(profile: str = "") -> sqlite3.Connection:
    path = board_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _all_boards() -> list[str]:
    """Every agent's profile name that has (or should have) a board."""
    names = ["default"]
    root = Path(BOARDS_ROOT)
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir() and d.name != ".default":
                names.append(d.name)
    return names


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def add_task(title: str, *, body: str = "", assignee: str = "",
             priority: int = 0, created_by: str = "system",
             parent_id: str | None = None, profile: str = "") -> dict:
    """Create a task. assignee is the PROFILE (agent) that owns it — the
    task is written INTO that agent's board (the worker's queue). With
    no assignee it lands in the caller's board (profile or the queen's).
    """
    task_id = str(uuid.uuid4())
    now = _now()
    board = board_path(assignee) if assignee else board_path(profile)
    with _conn_by_path(board) as conn:
        conn.execute(
            "INSERT INTO tasks (id, title, body, assignee, status, priority, created_by, created_at)"
            " VALUES (?,?,?,?, 'todo', ?, ?, ?)",
            (task_id, title, body, assignee or None, priority, created_by, now),
        )
        if parent_id:
            conn.execute(
                "INSERT OR IGNORE INTO task_links (parent_id, child_id) VALUES (?,?)",
                (parent_id, task_id),
            )
    return {"id": task_id, "title": title, "assignee": assignee, "status": "todo"}


def _conn_by_path(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def get_task(task_id: str) -> dict | None:
    """Fetch a task by full id OR a unique prefix — searched hive-wide
    (any board). The board is a storage detail; tasks are found wherever
    they live."""
    found = _find_task(task_id)
    return dict(found) if found is not None else None


def _find_task(task_id: str) -> sqlite3.Row | None:
    for name in _all_boards():
        try:
            with _conn(name) as conn:
                row = conn.execute("SELECT * FROM tasks WHERE id=?",
                                   (task_id,)).fetchone()
                if row:
                    return row
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE id LIKE ? LIMIT 2",
                    (f"{task_id}%",)).fetchall()
                if len(rows) == 1:
                    return rows[0]
        except Exception:
            continue
    return None


def list_tasks(assignee: str = "", status: str = "", limit: int = 50,
               profile: str = "") -> list[dict]:
    """Tasks, optionally filtered by agent (profile) and/or status.

    With an assignee, reads THAT agent's board directly (fast path).
    Without one, aggregates every board (the hive view).
    """
    sql = "SELECT * FROM tasks WHERE 1=1"
    params: list = []
    if assignee:
        sql += " AND assignee=?"
        params.append(assignee)
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY priority DESC, created_at DESC LIMIT ?"
    params.append(limit)
    boards = [board_path(assignee)] if assignee else \
        [board_path(n) for n in _all_boards()]
    rows: list[dict] = []
    for b in boards:
        try:
            with _conn_by_path(b) as conn:
                rows.extend(dict(r) for r in conn.execute(sql, params).fetchall())
        except Exception:
            continue
    rows.sort(key=lambda t: (t.get("priority") or 0, t.get("created_at") or ""),
              reverse=True)
    return rows[:limit]


def update_task(task_id: str, *, title: str | None = None, body: str | None = None,
                assignee: str | None = None, priority: int | None = None,
                status: str | None = None) -> dict | None:
    """Update a task's fields. Status transitions set started/completed.
    The task is found hive-wide, then updated in its own board."""
    task = _find_task(task_id)
    if task is None:
        return None
    now = _now()
    fields: list[str] = []
    params: list = []
    if title is not None:
        fields.append("title=?"); params.append(title)
    if body is not None:
        fields.append("body=?"); params.append(body)
    if assignee is not None:
        fields.append("assignee=?"); params.append(assignee or None)
    if priority is not None:
        fields.append("priority=?"); params.append(int(priority))
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        fields.append("status=?")
        params.append(status)
        if status == "in_progress" and not task["started_at"]:
            fields.append("started_at=?"); params.append(now)
        if status == "done" and not task["completed_at"]:
            fields.append("completed_at=?"); params.append(now)
    if not fields:
        return dict(task)
    params.append(task["id"])  # the RESOLVED full id, not the prefix input
    board = board_path(task["assignee"] or "")
    with _conn_by_path(board) as conn:
        conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", params)
    return get_task(task["id"])


def subtasks(parent_id: str) -> list[dict]:
    """The children of a goal task (decomposition) — hive-wide."""
    parent = _find_task(parent_id)
    if parent is None:
        return []
    board = board_path(parent["assignee"] or "")
    with _conn_by_path(board) as conn:
        rows = conn.execute(
            "SELECT t.* FROM tasks t JOIN task_links l ON l.child_id = t.id"
            " WHERE l.parent_id=? ORDER BY t.priority DESC",
            (parent["id"],),
        ).fetchall()
        return [dict(r) for r in rows]


def board_summary() -> dict:
    """Counts by status and per agent (profile) — the HIVE view: every
    board aggregated. This is the queen's admin dashboard."""
    by_status: dict[str, int] = {}
    by_agent: dict[str, int] = {}
    for name in _all_boards():
        try:
            with _conn(name) as conn:
                for r in conn.execute(
                        "SELECT status, COUNT(*) n FROM tasks GROUP BY status"):
                    by_status[r["status"]] = by_status.get(r["status"], 0) + r["n"]
                for r in conn.execute(
                        "SELECT assignee, COUNT(*) n FROM tasks WHERE status != 'done'"
                        " AND assignee IS NOT NULL GROUP BY assignee"):
                    by_agent[r["assignee"]] = by_agent.get(r["assignee"], 0) + r["n"]
        except Exception:
            continue
    return {"by_status": by_status, "by_agent": by_agent}


def delete_task(task_id: str) -> bool:
    """Delete a task by full id OR unique prefix. Returns True if deleted."""
    task = _find_task(task_id)
    if not task:
        return False
    board = board_path(task["assignee"] or "")
    with _conn_by_path(board) as conn:
        conn.execute("DELETE FROM tasks WHERE id=?", (task["id"],))
        conn.execute("DELETE FROM task_links WHERE parent_id=? OR child_id=?",
                     (task["id"], task["id"]))
    return True


def open_work_for(assignee: str) -> list[dict]:
    """The open tasks assigned to a profile (agent) — the agent's queue."""
    return list_tasks(assignee=assignee, status="todo", limit=20) + \
        list_tasks(assignee=assignee, status="in_progress", limit=20)


# -- Delegation (the queen-bee model) -----------------------------------

def delegate(title: str, assignee: str, *, created_by: str = "athena",
             priority: int = 10, body: str = "") -> dict:
    """Athena (or an agent) delegates work to a profile agent.

    The task is written INTO the assignee's own board — the worker bee
    sees it in their queue when their runtime ticks.

    Priority tiers (the hive model):
        >=10  Athena/the administrator delegated — top priority
         5-9  a fellow agent asked for help — help if you can
         <5   routine board work
    """
    if created_by in ("athena", "default", "system") and priority < 10:
        priority = 10  # the queen's command is always top priority
    task = add_task(title, body=body, assignee=assignee,
                    priority=priority, created_by=created_by)
    task["priority"] = priority
    task["created_by"] = created_by
    return task


# -- Subagents (Drone bees — a subagent IS a task) -----------------------

def spawn_subagent(parent: str, title: str, body: str) -> dict:
    """ANY agent (athena or a profile) spawns an unnamed worker — a DRONE.

    A subagent has NO name — it IS the task itself. It runs through the
    message loop with the task body as its instruction and returns its
    result to the parent. The drone lives in the parent's OWN board (the
    queen's drones in the queen's board, a worker's drones in theirs).
    """
    sub_id = str(uuid.uuid4())
    now = datetime.now().isoformat(timespec="seconds")
    with _conn(parent) as conn:
        conn.execute(
            "INSERT INTO subagents (id, parent, title, body, status, created_at)"
            " VALUES (?,?,?,?, 'queued', ?)",
            (sub_id, parent, title, body, now),
        )
    return {"id": sub_id, "parent": parent, "title": title, "status": "queued"}


def list_subagents(parent: str = "", status: str = "", limit: int = 50) -> list[dict]:
    """The subagents (optionally filtered by parent agent / status).
    With a parent, reads the parent's board; without one, the hive."""
    q = "SELECT * FROM subagents"
    clauses, args = [], []
    if parent:
        clauses.append("parent=?")
        args.append(parent)
    if status:
        clauses.append("status=?")
        args.append(status)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    boards = [board_path(parent)] if parent else \
        [board_path(n) for n in _all_boards()]
    rows: list[dict] = []
    for b in boards:
        try:
            with _conn_by_path(b) as conn:
                rows.extend(dict(r) for r in conn.execute(q, args).fetchall())
        except Exception:
            continue
    rows.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return rows[:limit]


def next_subagent(profile: str = "") -> dict | None:
    """The next queued subagent to run (oldest first) — from the given
    agent's OWN board. Default: the queen's board (the runtime's own
    drones)."""
    with _conn(profile) as conn:
        row = conn.execute(
            "SELECT * FROM subagents WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE subagents SET status='running', started_at=? WHERE id=?",
                     (datetime.now().isoformat(timespec="seconds"), row["id"]))
        conn.commit()
        return dict(row)


def complete_subagent(sub_id: str, result: str, *, failed: bool = False) -> None:
    """Mark a subagent done and store its return value (hive-wide find)."""
    for name in _all_boards():
        try:
            with _conn(name) as conn:
                row = conn.execute("SELECT id FROM subagents WHERE id=?",
                                   (sub_id,)).fetchone()
                if row:
                    status = "failed" if failed else "done"
                    conn.execute(
                        "UPDATE subagents SET status=?, result=?, completed_at=? WHERE id=?",
                        (status, result, datetime.now().isoformat(timespec="seconds"),
                         sub_id),
                    )
                    return
        except Exception:
            continue


def subagent_result(sub_id: str) -> dict | None:
    """The subagent's outcome: {id, status, result} (hive-wide find)."""
    for name in _all_boards():
        try:
            with _conn(name) as conn:
                row = conn.execute("SELECT * FROM subagents WHERE id=?",
                                   (sub_id,)).fetchone()
                if row:
                    return dict(row)
        except Exception:
            continue
    return None


# -- Subagent lifecycle (the Operator's spec: the ENTIRE lifecycle) ----------
# spawn → queued → running → done/failed, plus HEALTH (is it stuck?),
# REAP (stale runners get failed), and CLEANUP (done results archived).

# A subagent that has been 'running' longer than this is STALE (crashed
# or hung — its worker process died without completing).
STALE_RUNNING_S = 600.0


def subagent_health(profile: str = "") -> dict:
    """The pool's health: counts per status + any stale runners (scoped
    to one agent's board — the drones of one bee)."""
    subs = list_subagents(parent=profile or "")
    by_status: dict[str, int] = {}
    for s in subs:
        by_status[s["status"]] = by_status.get(s["status"], 0) + 1
    stale = []
    now = datetime.now()
    for s in subs:
        if s["status"] == "running" and s.get("started_at"):
            try:
                started = datetime.fromisoformat(s["started_at"])
                age = (now - started).total_seconds()
                if age > STALE_RUNNING_S:
                    stale.append({"id": s["id"], "title": s["title"],
                                  "age_s": int(age)})
            except Exception:
                continue
    return {"by_status": by_status, "stale": stale,
            "healthy": not stale}


def reap_stale(profile: str = "") -> list[str]:
    """Mark stale 'running' subagents as failed (their worker died)."""
    health = subagent_health(profile)
    reaped = []
    for s in health["stale"]:
        complete_subagent(s["id"], "stale: worker died or hung",
                          failed=True)
        reaped.append(s["id"])
    return reaped


def cleanup_done(keep: int = 50, profile: str = "") -> int:
    """Archive old done/failed subagents (keep the newest `keep`) —
    scoped to one agent's board unless profile is empty (hive-wide)."""
    if profile:
        subs = list_subagents(parent=profile, limit=1000)
    else:
        subs = list_subagents(limit=1000)
    finished = [s for s in subs if s["status"] in ("done", "failed")]
    finished.sort(key=lambda s: s.get("completed_at") or "", reverse=True)
    removed = 0
    for s in finished[keep:]:
        for name in _all_boards():
            try:
                with _conn(name) as conn:
                    cur = conn.execute("SELECT id FROM subagents WHERE id=?",
                                       (s["id"],)).fetchone()
                    if cur:
                        conn.execute("DELETE FROM subagents WHERE id=?", (s["id"],))
                        removed += 1
                        break
            except Exception:
                continue
    return removed


# -- Decompose + judge (LLM-assisted, through the provider chain) -------

def decompose(goal_id: str, *, providers=None, system_prompt: str = "") -> dict:
    """Break a goal task into subtasks (LLM-assisted).

    The model proposes a numbered subtask list; each becomes a task linked
    to the goal, assigned to the same agent (profile). Returns the created
    subtasks.
    """
    goal = get_task(goal_id)
    if goal is None:
        return {"success": False, "error": "goal task not found"}
    if providers is None:
        return {"success": False, "error": "no provider for decomposition"}

    try:
        from core.message_loop import MessageLoop
        loop = MessageLoop(
            providers=providers,
            system_prompt=system_prompt or "You are a task planner.",
            max_iterations=2,
        )
        prompt = (
            "Decompose this goal into 3-5 concrete subtasks. "
            "Return ONLY a numbered list, one per line, no extra text.\n\n"
            f"GOAL: {goal['title']}\n{goal.get('body') or ''}"
        )
        result = loop.run_turn(prompt)
    except Exception as exc:  # noqa: BLE001
        from core.logging import log_event
        log_event(4, f"kanban LLM call failed: {exc}", source="autonomy",
                  action="llm_task")
        return {"success": False, "error": str(exc)}

    created = []
    for line in result.reply.splitlines():
        line = line.strip()
        line = line.lstrip("0123456789.-) ")
        if not line or len(line) < 4:
            continue
        subtask = add_task(
            line, assignee=goal.get("assignee") or "",
            priority=goal.get("priority", 0), created_by="decompose",
            parent_id=goal_id,
        )
        created.append(subtask)
    return {"success": bool(created), "subtasks": created}


def judge(goal_id: str, *, providers=None, system_prompt: str = "") -> dict:
    """Judge whether a goal is complete (LLM-assisted).

    Looks at the goal + its subtasks' statuses. If all subtasks are done,
    it's complete; otherwise the model evaluates the partial progress.
    """
    goal = get_task(goal_id)
    if goal is None:
        return {"success": False, "error": "goal task not found"}
    children = subtasks(goal_id)

    if children and all(c["status"] == "done" for c in children):
        update_task(goal_id, status="done")
        return {"success": True, "complete": True, "reason": "all subtasks done"}

    if providers is None:
        return {"success": False, "error": "no provider for judging"}

    try:
        from core.message_loop import MessageLoop
        loop = MessageLoop(
            providers=providers,
            system_prompt=system_prompt or "You are a task reviewer.",
            max_iterations=2,
        )
        statuses = "\n".join(
            f"- [{c['status']}] {c['title']}" for c in children
        ) if children else "(no subtasks)"
        prompt = (
            "Judge whether this goal is COMPLETE or still OPEN. "
            "Reply with exactly one word: COMPLETE or OPEN.\n\n"
            f"GOAL: {goal['title']}\n{goal.get('body') or ''}\n\n"
            f"Subtasks:\n{statuses}"
        )
        result = loop.run_turn(prompt)
    except Exception as exc:  # noqa: BLE001
        from core.logging import log_event
        log_event(4, f"kanban LLM call failed: {exc}", source="autonomy",
                  action="llm_task")
        return {"success": False, "error": str(exc)}

    complete = "COMPLETE" in result.reply.upper()
    if complete:
        update_task(goal_id, status="done")
    return {"success": True, "complete": complete, "reason": result.reply.strip()[:200]}
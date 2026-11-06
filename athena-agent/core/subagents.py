"""Subagents: a bounded one-shot worker, and the paper trail it leaves.

An agent that needs thirty files read should not read them into its own context. It spawns a worker, the worker burns
its own small budget, and what comes back is a summary plus a trace on disk.

    profiles/<lane>/subagents/<slug>/
        ARTIFACT.md      what was asked, what came back, what it touched, what it cost
        state.db         one row per run: task, model, turns, tokens, status, timing
        sessions/        the worker's own transcript (same layout as a real profile)
        sandbox/         where its tools may write — and nowhere else

Four rules, each a way this could otherwise go wrong:

1. **Narrower than the parent, never wider.** The worker's scope is its own folder plus read access to the tree. It
   cannot write the parent's memories, its workspace, or the program.
2. **Bounded.** A turn cap, a dollar cap and a wall clock. A worker that runs long is a worker whose parent has
   stopped thinking.
3. **No recursion.** The worker's toolset has no `spawn_subagent`, so an agent tree cannot fork itself indefinitely.
4. **A trace or it did not happen.** `ARTIFACT.md` is written on success *and* on failure — the failure trace is the
   one you actually need at 2am.
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from pathlib import Path

MAX_TASK_CHARS = 4000
DEFAULT_TURNS = 6
DEFAULT_USD = 0.05
DEFAULT_TIMEOUT_S = 300


def _slug(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())[:6]
    base = "-".join(words) or "task"
    return f"{time.strftime('%Y-%m-%d_%H-%M-%S')}-{base[:48]}"


class Trace:
    """One subagent's folder: the artifact, the run log, its own session and sandbox."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.state = self.folder / "state.db"
        self.artifact = self.folder / "ARTIFACT.md"

    def prepare(self) -> None:
        for sub in ("sessions", "sandbox", "documents", "references"):
            (self.folder / sub).mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.state) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, started_at REAL, ended_at REAL,
                task TEXT, model TEXT, lane TEXT, status TEXT, turns INTEGER,
                tool_calls INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER, cost_usd REAL, note TEXT,
                agent_id TEXT)""")
            # a table created before the column existed keeps its old shape, and an INSERT naming the new column
            # would fail — then be swallowed. Migrate explicitly instead of hoping.
            columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            if "agent_id" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN agent_id TEXT DEFAULT ''")
            db.commit()

    def record(self, **row) -> None:
        fields = ("started_at", "ended_at", "task", "model", "lane", "status", "turns", "tool_calls",
                  "prompt_tokens", "completion_tokens", "cost_usd", "note", "agent_id")
        values = [row.get(f) for f in fields]
        try:
            with sqlite3.connect(self.state) as db:
                db.execute(f"INSERT INTO runs ({','.join(fields)}) VALUES ({','.join('?' * len(fields))})", values)
                db.commit()
        except sqlite3.Error as exc:
            # the trace is the only record that this work happened; losing it silently means an audit with a hole
            print(f"subagents: could not record the run ({exc}) — trace at {self.state}", file=sys.stderr)

    def write_artifact(self, *, task: str, result: dict, scope_notes: str, folder: Path,
                       agent_id: str = "") -> None:
        text = [
            f"# Subagent run — {folder.name}",
            "",
            f"- **task**: {task}",
            *([f"- **agent**: {agent_id}"] if agent_id else []),
            f"- **status**: {result.get('status')}",
            f"- **model**: {result.get('model', '')}",
            f"- **turns**: {result.get('turns', 0)} · **tool calls**: {result.get('tool_calls', 0)}",
            f"- **tokens**: {result.get('prompt_tokens', 0)} in / {result.get('completion_tokens', 0)} out"
            f" · **cost**: ${float(result.get('cost_usd') or 0):.4f}",
            f"- **started**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(result.get('started_at') or time.time()))}",
            f"- **scope**: {scope_notes}",
            "",
            "## Answer",
            "",
            (result.get("reply") or result.get("error") or "(nothing came back)").strip()[:6000],
            "",
            "## Files this run created or changed",
            "",
        ]
        files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*")
                       if p.is_file() and p.name not in ("ARTIFACT.md", "state.db")
                       and "__pycache__" not in str(p))
        text += [f"- `{name}`" for name in files[:60]] or ["- (none)"]
        if len(files) > 60:
            text.append(f"- … and {len(files) - 60} more")
        self.artifact.write_text("\n".join(text) + "\n", encoding="utf-8")


def narrow_scope(parent_scope, sub_root: Path):
    """The worker's scope: its own folder, read-only view of the rest.

    Built by re-rooting: `home` becomes the subagent folder, so `sandbox` and every writable place resolve inside it,
    and **no workspaces are inherited** — a grant given to the parent is not a grant given to its helper.
    """
    from core import paths as paths_mod

    child = paths_mod.Scope(sub_root, athena_root=parent_scope.root, workspaces=[])
    return child


def spawn(*, parent_scope, parent_cfg, parent_lane: str, parent_root: Path, task: str,
          provider, tools_factory, instructions: str = "", parent_agent_id: str = "",
          max_turns: int = DEFAULT_TURNS,
          max_usd: float = DEFAULT_USD, timeout_s: float = DEFAULT_TIMEOUT_S,
          approvals=None) -> dict:
    """Run one bounded turn in a fresh, narrower scope and leave the trace behind.

    `tools_factory(trace, scope)` builds the worker's registry — the caller passes one that omits `spawn_subagent`,
    which is how recursion is prevented rather than merely discouraged.
    """
    from core.budget import IterationBudget
    from core.loop import run_turn
    from core.session import Session

    clean = (task or "").strip()[:MAX_TASK_CHARS]
    if not clean:
        raise ValueError("a subagent needs a task")

    folder = Path(parent_root) / "subagents" / _slug(clean)
    trace = Trace(folder)
    trace.prepare()
    scope = narrow_scope(parent_scope, folder)

    started = time.time()
    # DEDUCTED from the turn that is running. The module's `spawn_child` existed and was never called, so an agent
    # could spawn workers for ever, each with its own 6 turns and its own $0.05, on top of its own ceiling. With the
    # parent's remaining money as the child's ceiling and the child's turns charged to the parent, delegating costs
    # what it costs.
    from core import budget as budget_mod

    parent_budget = budget_mod.active(parent_lane)
    if parent_budget is not None:
        budget = parent_budget.spawn_child(allocation=max(1, int(max_turns)),
                                           usd=min(float(max_usd), parent_budget.remaining_usd()))
    else:                                             # spawned outside a turn (a test, or a script): nothing to carve
        budget = IterationBudget(max_turns=max(1, int(max_turns)), max_usd=float(max_usd), grace_calls=1)
    session = Session(folder.name, folder)
    worker_cfg = dict(parent_cfg)

    result: dict
    try:
        registry = tools_factory(trace, scope)
        turn = run_turn(
            channel=f"{parent_lane}/subagent",
            channel_root=folder,
            session=session,
            provider=provider,
            tools_registry=registry,
            instructions=(instructions or
                          "You are a one-shot worker. Do the task, say what you did, and stop. "
                          "You cannot spawn further workers."),
            user_text=clean,
            cfg=worker_cfg,
            budget=budget,
            interrupts=None,
        )
        usage = getattr(turn, "usage", None) or {}
        result = {"status": getattr(turn, "status", "completed"), "reply": getattr(turn, "reply", ""),
                  "turns": getattr(turn, "turns", 0) or getattr(budget, "turns", 0),
                  "tool_calls": getattr(turn, "tool_rounds", 0),
                  "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                  "completion_tokens": int(usage.get("completion_tokens") or 0),
                  "cost_usd": getattr(turn, "cost_usd", 0.0) or getattr(budget, "cost_usd", 0.0),
                  "model": ((worker_cfg.get("model") or {}).get("name") or "")}
        if time.time() - started > timeout_s:
            result["note"] = f"over the {timeout_s:.0f}s budget; the turn completed anyway"
    except Exception as exc:                          # noqa: BLE001 — a worker fails, the parent does not
        result = {"status": "error", "error": f"{type(exc).__name__}: {exc}", "turns": 0, "tool_calls": 0,
                  "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "model": ""}

    trace.record(agent_id=str(parent_agent_id or ""),
                 started_at=started, ended_at=time.time(), task=clean, model=result.get("model", ""),
                 lane=parent_lane, status=result.get("status", "?"), turns=result.get("turns", 0),
                 tool_calls=result.get("tool_calls", 0), prompt_tokens=result.get("prompt_tokens", 0),
                 completion_tokens=result.get("completion_tokens", 0), cost_usd=result.get("cost_usd", 0.0),
                 note=result.get("note", ""))
    trace.write_artifact(agent_id=str(parent_agent_id or ""), task=clean, result=result,
                         scope_notes=f"{scope.home} (writes confined here) · no inherited workspaces · "
                                     f"budget {max_turns} turns / ${max_usd:.2f}", folder=folder)
    result.update({"trace": str(folder), "artifact": str(trace.artifact), "state_db": str(trace.state)})
    return result

"""AUTONOMY tools — the queen-bee delegation section (the Operator's 08-12 spec).

The Autonomy tool section registers Athena's hive-management tools:
  - delegate        — hand a task/procedure to a WORKER bee (profile
                      process) OR a DRONE (subagent) OR BOTH, based on
                      what's available
  - worker_status   — which worker profiles are live processes
  - board_summary   — the open work per agent board

The hive model (the Operator's architecture):
  - Athena (.default) is ALWAYS active — she is the administrator, she
    lives with the server/runtime.
  - WORKERS = non-default profiles (.nurse, .janitor, any named
    profile) — each runs as its OWN process when activated.
  - DRONES = subagents — spawned in-process, they perform the task and
    report back.

The DELEGATION RULE (the Operator's spec): delegate to the WORKERS if
available, else the DRONES if available, else BOTH if both are
available.
"""

from __future__ import annotations


def _available_workers() -> list[str]:
    """The worker profiles that are LIVE processes (or can be started)."""
    workers = []
    try:
        from intelligence.profiles import list_profiles
        from core.supervisor import list_runtimes
        runtimes = list_runtimes()
        for p in list_profiles():
            if p.name in (".default", ".nurse", ".janitor"):
                # .nurse/.janitor are system workers; .default is the queen.
                if p.name == ".default":
                    continue
            st = runtimes.get(p.name, {})
            if st.get("live") or st.get("status") == "running":
                workers.append(p.name)
        # Always include the system workers — they can be started on demand.
        for name in (".nurse", ".janitor"):
            if name not in workers:
                workers.append(name)
    except Exception:
        pass
    return sorted(set(workers))


def _calling_agent() -> str:
    """The CALLING agent's identity (the honest audit trail — the
    Operator's spec: a worker delegating must be recorded as the worker,
    never as athena)."""
    try:
        from core.config import get_current_profile
        name = get_current_profile()
        if name:
            return name
    except Exception:
        pass
    try:
        import os
        prof = os.environ.get("ATHENA_PROFILE", "")
        if prof:
            return prof
    except Exception:
        pass
    return "athena"


def delegate(task: str, *, assignee: str = "", body: str = "",
             mode: str = "auto", timeout: float = 120.0) -> str:
    """Delegate a task to a WORKER bee, a DRONE subagent, or BOTH.

    mode:
      auto   — workers if available, else drones, else both
      worker — always write to a worker's board (start the process if
               needed)
      drone  — always spawn an in-process subagent (the drone runs the
               task NOW and returns its result)
      both   — board task for a worker AND a drone runs it now

    Returns a human-readable summary of what was delegated.
    """
    if not task or not str(task).strip():
        return "error: task is required"
    # resolve the worker target
    workers = _available_workers()
    target = assignee.strip() or (workers[0] if workers else "")
    if mode == "worker" and not target:
        return "error: no worker profile available for worker-mode delegation"

    # THE CALLING AGENT (the 08-12 honest-audit fix): whoever invokes the
    # tool is the creator — a worker delegating is recorded as the worker.
    creator = _calling_agent()

    # THE AUTO-WAKE (the Operator's 08-12 dynamic-cost spec): if the
    # target worker is SLEEPING or HIBERNATING, wake it BEFORE writing
    # the task — delegation must be instant, never wait for a manual
    # start. (A sleeping worker = no process; the wake spawns it.)
    if target and target != "default":
        try:
            from core.supervisor import wake_runtime
            w = wake_runtime(target)
            if w.get("ok"):
                pass  # woke (or already awake)
        except Exception:
            pass

    results = []

    # 1) The BOARD task (worker lane) — unless mode=drone only.
    if mode in ("auto", "worker", "both"):
        try:
            from autonomy.kanban import delegate as kanban_delegate
            created = kanban_delegate(
                str(task), target or "default",
                created_by=creator, priority=10, body=body or str(task))
            results.append(
                f"board task {created.get('id', '')[:8]} assigned to "
                f"{target or 'default'}")
        except Exception as exc:
            results.append(f"board delegation failed: {exc}")

    # 2) The DRONE lane (in-process subagent) — unless mode=worker only.
    if mode in ("auto", "drone", "both"):
        # auto: only spawn a drone when no worker board got the task.
        if mode == "auto" and results and "board task" in results[0]:
            pass  # worker lane succeeded — no drone needed in auto mode
        else:
            try:
                from autonomy.scheduler import _run_subagent
                reply = _run_subagent({"body": f"{task}\n\n{body or ''}".strip()})
                results.append(f"drone result: {str(reply)[:120]}")
            except Exception as exc:
                results.append(f"drone failed: {exc}")

    if not results:
        return "error: nothing delegated — no workers and no drones available"
    return " | ".join(results)


def report_to_admin(title: str, *, body: str = "", priority: int = 8) -> str:
    """A WORKER reports its proposal/summary/results to the ADMIN (the
    queen — .default). The report lands on the QUEEN'S board as admin
    work, so Athena sees it when her scheduler ticks. This is the
    worker→admin reporting channel (the Operator's 08-12 spec)."""
    try:
        from autonomy.kanban import delegate as kanban_delegate
        created = kanban_delegate(
            title or "report", ".default",
            created_by=_calling_agent(), priority=priority,
            body=body or title or "report")
        return (f"reported to admin: board task "
                f"{created.get('id', '')[:8]} on the queen's board "
                f"(priority {priority})")
    except Exception as exc:
        return f"error: report failed: {exc}"


def schedule_task(name: str, schedule: str, prompt: str, *,
                  job_type: str = "custom", script: str = "") -> str:
    """Register a recurring or one-shot scheduled job (the scheduler's
    cron registry). The task runs on its own schedule — Athena or a
    worker handles it when it fires."""
    try:
        from autonomy.scheduler import add_job
        job = add_job(name, schedule, prompt, job_type=job_type,
                      script=script)
        return (f"scheduled '{name}' ({job.get('type')}) every "
                f"{job.get('schedule')} — id {job.get('id', '')[:8]}")
    except Exception as exc:
        return f"error: schedule failed: {exc}"


def coordinate(task: str, *, workers: list | None = None,
               use_drones: bool = True, timeout: float = 300.0) -> str:
    """AGENT-TO-AGENT MULTI-THREADING (the Operator's 08-12 spec): split
    a task across MULTIPLE agents — worker bees AND/OR drone subagents —
    run them in PARALLEL threads, and collect the results. Used when a
    task needs several agents working together; single-agent tasks stay
    with the caller's own drones (delegate handles that lane)."""
    import concurrent.futures as _fut
    import json as _json

    workers = workers or _available_workers()[:2]
    lane_count = len(workers) + (1 if use_drones else 0)
    if lane_count == 0:
        return "error: no workers and no drones to coordinate"
    if lane_count == 1:
        # fall back to a plain delegate — coordination needs >1 agent
        return delegate(task, mode="drone" if not workers else "worker")

    results = []

    def _worker_lane(profile: str) -> str:
        try:
            # AUTO-WAKE (the dynamic-cost spec): wake the worker before
            # assigning — a sleeping/hibernating worker gets woken.
            from core.supervisor import wake_runtime
            try:
                wake_runtime(profile)
            except Exception:
                pass
            from autonomy.kanban import delegate as kanban_delegate
            created = kanban_delegate(
                task, profile, created_by=_calling_agent(),
                priority=10, body=task)
            return f"{profile}: board task {created.get('id', '')[:8]}"
        except Exception as exc:
            return f"{profile}: failed ({exc})"

    def _drone_lane() -> str:
        try:
            from autonomy.scheduler import _run_subagent
            reply = _run_subagent({"body": task})
            return f"drone: {str(reply)[:100]}"
        except Exception as exc:
            return f"drone: failed ({exc})"

    lanes = [lambda p=w: _worker_lane(p) for w in workers]
    if use_drones:
        lanes.append(lambda: _drone_lane())

    with _fut.ThreadPoolExecutor(max_workers=len(lanes)) as ex:
        futures = [ex.submit(l) for l in lanes]
        for f in _fut.as_completed(futures):
            try:
                results.append(f.result(timeout=timeout))
            except Exception as exc:
                results.append(f"lane failed: {exc}")

    return "COORDINATED across " + str(len(results)) + " lanes:\n" + \
        "\n".join(f"  - {r}" for r in results)


def worker_status() -> str:
    """Which worker bees are live processes + their agent state
    (wake/hibernate/sleep — the Operator's 08-12 dynamic-cost spec)."""
    try:
        from core.supervisor import list_runtimes
        from intelligence.profiles import list_profiles
        runtimes = list_runtimes()
        lines = []
        for p in list_profiles():
            if p.name == ".default":
                continue
            st = runtimes.get(p.name, {})
            state = st.get("state", "sleep")
            live = "LIVE" if st.get("live") or st.get("status") == "running" \
                else "down"
            detail = st.get("state_detail", "")
            line = f"{p.name}: {state} ({live}) pid={st.get('pid', '-')}"
            if detail:
                line += f" — {detail}"
            lines.append(line)
        return "\n".join(lines) or "no worker profiles"
    except Exception as exc:
        return f"error: {exc}"


def board_summary() -> str:
    """The open work per agent board (the hive's queues)."""
    try:
        from autonomy.kanban import board_summary as kb_summary
        s = kb_summary()
        lines = []
        for agent, count in sorted((s.get("by_agent") or {}).items()):
            lines.append(f"{agent}: {count} open")
        return "\n".join(lines) or "no open tasks"
    except Exception as exc:
        return f"error: {exc}"


def register_autonomy_tools() -> list[str]:
    """Register the AUTONOMY tool section (the Operator's 08-12 spec) —
    the hive-management tools: delegate, worker_status, board_summary.
    Called at boot; the shared registry + canonical list pick them up."""
    from filesystem.tools import register, Tool
    register(Tool(
        name="delegate",
        description=("Delegate a task/procedure to a WORKER bee (profile "
                     "process) OR a DRONE subagent OR BOTH — based on what "
                     "is available (mode=auto: workers if available, else "
                     "drones, else both). Use when a task can be handled by "
                     "another agent."),
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string",
                         "description": "The task/procedure to delegate"},
                "assignee": {"type": "string",
                             "description": "Optional worker profile (.nurse, .janitor, ...)"},
                "body": {"type": "string",
                         "description": "Optional detail/instructions for the task"},
                "mode": {"type": "string",
                         "enum": ["auto", "worker", "drone", "both"],
                         "description": "auto (default): workers if available, else drones, else both"},
            },
            "required": ["task"],
        },
        fn=lambda args, timeout=120.0: delegate(
            args.get("task", ""), assignee=args.get("assignee", ""),
            body=args.get("body", ""), mode=args.get("mode", "auto"),
            timeout=timeout),
    ))
    register(Tool(
        name="worker_status",
        description=("Which worker bee profiles are live processes (and "
                     "their PIDs). Use to check the hive before delegating."),
        parameters={"type": "object", "properties": {}},
        fn=lambda args, timeout=60.0: worker_status(),
    ))
    register(Tool(
        name="board_summary",
        description=("The open work per agent board — the hive's queues. "
                     "Use to see what's pending across all agents."),
        parameters={"type": "object", "properties": {}},
        fn=lambda args, timeout=60.0: board_summary(),
    ))
    register(Tool(
        name="report_to_admin",
        description=("Report a proposal/summary/results to the ADMIN "
                     "(Athena — the queen). The report lands on the "
                     "queen's board as admin work. Use when a worker "
                     "finishes work and must report back to Athena."),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string",
                          "description": "The report title (what was proposed/done)"},
                "body": {"type": "string",
                         "description": "The summary/results detail"},
                "priority": {"type": "integer",
                             "description": "8 default; 10 for critical"},
            },
            "required": ["title"],
        },
        fn=lambda args, timeout=60.0: report_to_admin(
            args.get("title", ""), body=args.get("body", ""),
            priority=int(args.get("priority", 8) or 8)),
    ))
    register(Tool(
        name="schedule_task",
        description=("Register a recurring or one-shot scheduled job "
                     "(cron). The task fires on its own schedule — use "
                     "for periodic work (hourly checks, daily backups, "
                     "reminders)."),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The job name"},
                "schedule": {"type": "string",
                             "description": "cron '0 9 * * *' | 'every 30m' | ISO one-shot"},
                "prompt": {"type": "string",
                           "description": "The task/prompt to run when it fires"},
                "job_type": {"type": "string",
                             "description": "hourly|daily|weekly|custom (default custom)"},
            },
            "required": ["name", "schedule", "prompt"],
        },
        fn=lambda args, timeout=60.0: schedule_task(
            args.get("name", ""), args.get("schedule", ""),
            args.get("prompt", ""), job_type=args.get("job_type", "custom")),
    ))
    register(Tool(
        name="coordinate",
        description=("AGENT-TO-AGENT MULTI-THREADING: split a task across "
                     "multiple agents (worker bees AND/OR drone subagents), "
                     "run them in parallel, collect results. Use when a "
                     "task needs several agents working together. Single-"
                     "agent tasks: use delegate instead."),
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The task to coordinate"},
                "use_drones": {"type": "boolean",
                               "description": "Include drone subagents (default true)"},
            },
            "required": ["task"],
        },
        fn=lambda args, timeout=300.0: coordinate(
            args.get("task", ""),
            use_drones=bool(args.get("use_drones", True)),
            timeout=timeout),
    ))
    return ["delegate", "worker_status", "board_summary",
            "report_to_admin", "schedule_task", "coordinate"]

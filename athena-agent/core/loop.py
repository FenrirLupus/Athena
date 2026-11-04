"""The turn loop — call the model, run what it asks for, repeat until it answers.

Five statements (documentation/architecture/02-turn-loop.md):

    call → if tool calls, run them and append → if it answered, finish
         → any step may exit early → finalise

Invariants kept from that document:
  * retry/error handling wraps the model call, never a side-effecting tool
  * every tool call gets a result appended, including refusals
  * a turn that hits its budget still returns prose, never a truncated tool call
"""
from __future__ import annotations

import time
import traceback

from context import compress as compress_mod
from context import stack as stack_mod
from core.budget import IterationBudget
from core.context_window import effective_window


class TurnResult(dict):
    """A dict with attribute access, so callers can read result["status"] or result.status."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:                       # noqa: TRY003
            raise AttributeError(name) from exc


def run_turn(*, channel: str, channel_root, session, provider, tools_registry,
             instructions: str, user_text: str, cfg: dict,
             budget: IterationBudget | None = None,
             interrupts=None) -> TurnResult:
    """Run one turn. `interrupts` is an optional callable returning notices that arrived mid-turn.

    `instructions` may be a string or a callable returning one. A callable is resolved at **each iteration**, so
    anything in it that changes during a turn — the peers and their states, for instance — is current at
    the moment the model is asked, not stale from the turn's first call.

    An interruption cannot rewrite a prompt that has already been sent, so the notice is applied at the
    **next safe point**: the iteration boundary, before the next model call. That is the honest meaning of
    "adjust while thinking" — the agent sees the new information on its next call, not during one it has
    already made. Anything else would be claiming a model can be interrupted mid-token.
    """
    """Run one turn for one channel and return the result record."""
    b = cfg.get("budget") or {}
    model = cfg.get("model") or {}
    from core import budget as budget_mod

    budget = budget or IterationBudget(
        max_turns=int(b.get("max_iterations") or 100),
        max_usd=float(b.get("max_turn_usd") or 0.50),
        grace_calls=int(b.get("grace_calls") or 1),
    )
    budget_mod.enter(channel, budget)                  # a subagent spawned in this turn carves from HERE

    session.add("user", user_text)
    started = time.time()
    # the profile's own calibration: estimate and budget must be in the units the provider charges in
    from context import calibration as calibration_mod
    try:
        described = provider.describe() if hasattr(provider, "describe") else {}
    except Exception:                                  # noqa: BLE001 — a provider must not break a turn
        described = {}
    model_key = f"{described.get('provider') or model.get('provider') or '?'}:" \
                f"{described.get('model') or model.get('name') or '?'}"
    factor = calibration_mod.factor_for(channel_root, model_key)
    status = "completed"
    reply = ""
    calls = 0
    tool_rounds = 0
    # measurable per turn: tokens are what the provider reports, seconds are what the caller waits
    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        # the required procedures attached to this turn, recorded with the result
    procedures: list[str] = []
    model_calls: list[dict] = []
    last_report: dict = {}
    notes: list[dict] = []
    injected = 0
    system_text = ""

    try:
        while True:
            if not budget.consume_turn():
                status = budget.exhausted_reason() or "exhausted_budget"
                break
            calls += 1

            # compression happens at the turn boundary, before assembly — never mid-turn
            prompt_estimate = int((last_report or {}).get("prompt_tokens_est") or 0)
            compressed = _maybe_compress(session, cfg, factor, provider, prompt_estimate)
            if compressed:
                notes.append(compressed)

                        # interruptions are applied at the iteration boundary, so they affect the next model call
            if interrupts is not None:
                try:
                    pending_notices = interrupts() or []
                except Exception:                     # noqa: BLE001 — an inbox must never break a turn
                    pending_notices = []
                for notice in pending_notices:
                    session.add("user", notice)
                    injected += 1

            # resolved per iteration: a roster in the instructions has to describe the hive NOW
            live_instructions = instructions() if callable(instructions) else instructions
            # Required procedures are not optional reading: if the user's message triggers one, it is attached to
            # the instructions for this turn, in the system block, where the agent cannot mistake it for a skill.
            try:
                from core import instruction_docs

                matched = instruction_docs.select(user_text)
                procedure = instruction_docs.render(user_text)
                if procedure:
                    live_instructions = f"{live_instructions}\n\n{procedure}"
                    procedures = [doc["name"] for doc in matched]
            except Exception:                     # noqa: BLE001 — a missing folder must not break a turn
                pass
            messages, report = stack_mod.build(
                live_instructions, channel_root, session.history(), user_text, cfg,
                summary=session.summary_text(), factor=factor)
            last_report = report
            # keep the assembled system block so "what was the model actually told" is answerable
            # without trusting the model's account of its own instructions
            system_text = messages[0].get("content", "") if messages else ""

            try:
                call_started = time.perf_counter()
                resp = provider.call(
                    messages,
                    tools=tools_registry.schemas(),
                    max_tokens=int(model.get("max_output_tokens") or 1024),
                    temperature=float(model.get("temperature") or 0.7),
                )
                call_seconds = round(time.perf_counter() - call_started, 3)
            except Exception as exc:                  # noqa: BLE001 — model call failures are per-turn
                # NOT a system message: appending one mid-conversation is the trap doc 02 names, and a
                # template that requires "system first" then rejects every later turn. The failure is
                # reported in the turn result and the log, never injected into the transcript.
                print(f"model call failed: {exc}", file=__import__("sys").stderr)
                status = "error_api"
                reply = f"model call failed: {exc}"
                break

            call_usage = resp.get("usage") or {}
            call_prompt = int(call_usage.get("prompt_tokens") or call_usage.get("input_tokens") or 0)
            call_done = (int(call_usage.get("completion_tokens") or call_usage.get("output_tokens") or 0)
                         + int(call_usage.get("reasoning_tokens") or 0))
            usage_totals["prompt_tokens"] += call_prompt
            usage_totals["completion_tokens"] += call_done
            usage_totals["total_tokens"] += (int(call_usage.get("total_tokens") or 0)
                                             or call_prompt + call_done)
            # learn from what the provider billed: this is the only place the true token count exists
            if call_usage.get("prompt_tokens") and last_report.get("est_raw"):
                learned = calibration_mod.update(channel_root, int(last_report["est_raw"]),
                                                 int(call_usage["prompt_tokens"]), model_key)
                factor = float(learned.get("factor") or factor)
            model_calls.append({
                "index": len(model_calls) + 1, "seconds": call_seconds,
                "prompt_tokens": call_prompt, "completion_tokens": call_done,
                "finish_reason": resp.get("finish_reason") or "",
                "tool_calls": len(resp.get("tool_calls") or []),
            })

            # cost is computed here when the provider does not price itself: providers know tokens, the
            # price is an operator setting, and a turn that silently costs 0.00 breaks the budget guard
            cost = resp.get("cost_usd")
            # `not cost`, not `is None`: providers that do not price themselves return 0.0 (or omit it), and an
                        # an explicit zero would record $0.0000 for every local turn, so a zero falls back to the setting
            # metrics log computed the real figure, so `max_turn_usd` could never trip.
            if not cost:
                rate = float((cfg.get("metrics") or {}).get("usd_per_mtok") or 1.0)
                cost = (call_prompt + call_done) / 1_000_000 * rate
            budget.record_cost(cost)

            if budget.exhausted() and not resp.get("tool_calls"):
                # spent the ceiling and still got an answer — that is a completed turn
                status = "completed"
            content = resp.get("content") or ""
            tool_calls = resp.get("tool_calls") or []

            if not tool_calls:
                # Before the answer becomes final, check whether something arrived while the model was
                # answering. A single-call turn has no tool round to interrupt, so without this the
                # notice could only be dropped — an agent that cannot acknowledge new input until its
                # next task has not really been interrupted.
                late = []
                if interrupts is not None:
                    try:
                        late = interrupts() or []
                    except Exception:                 # noqa: BLE001
                        late = []
                if late:
                    for notice in late:
                        session.add("user", notice)
                        injected += 1
                    continue                          # one more pass, bounded by the iteration budget
                reply = content
                break

            # the model asked for tools: record the call, run each, answer every one
            session.add_assistant_tool_calls(content, tool_calls)
            tool_rounds += 1
            for call in tool_calls:
                call_id = call.get("id") or f"call_{tool_rounds}_{len(call)}"
                name = call.get("name") or ""
                args = call.get("arguments") or {}
                result = tools_registry.invoke(name, args, timeout=float((cfg.get("tools") or {}).get("timeout_s") or 60))
                session.add_tool_result(call_id, name, result)

    except Exception as exc:                          # noqa: BLE001 — the turn must always return
        status = "error_loop"
        reply = f"loop error: {exc}"
        print(f"loop error: {exc}\n{traceback.format_exc(limit=3)}", file=__import__("sys").stderr)

    from core import budget as budget_mod
    budget_mod.leave(channel)

    if not reply and status.startswith("exhausted"):
        reply = ("I ran out of budget before finishing. Here is where I got to: "
                 f"{tool_rounds} tool round(s), {calls} model call(s).")

    session.add("assistant", reply)
    return TurnResult({
        "channel": channel,
        "status": status,
        "reply": reply,
        "calls": calls,
        "tool_rounds": tool_rounds,
        "budget": budget.snapshot(),
        "prompt": last_report,
        "system_prompt": system_text[-3000:],
        "compressions": notes,
        "procedures": procedures,
        "interruptions": injected,
        "seconds": round(time.time() - started, 3),
        "usage": usage_totals,
        "model_calls": model_calls,
    })


def _maybe_compress(session, cfg: dict, factor: float = 1.0, provider=None,
                    prompt_tokens: int = 0) -> dict | None:
    """Compress when the CONTEXT WINDOW is filling, not when history has filled its share.

    Two thresholds, as configured (`compression.threshold_soft_pct` / `threshold_hard_pct`):
      * **soft (80 %)** — the window is getting full: compress if there are turns to evict.
      * **hard (90 %)** — compress regardless, because the next turn may not fit at all.

    Thresholds are measured against the assembled prompt (`prompt_tokens`), because that is what the model is
    sent. Measuring against the history block alone could never reach 80 % while history is capped at 30 %.
    """
    b = cfg.get("budget") or {}
    c = cfg.get("compression") or {}
    win = effective_window(cfg)
    soft = float(c.get("threshold_soft_pct", 80.0)) / 100.0
    hard = float(c.get("threshold_hard_pct", 90.0)) / 100.0
    history_share = int(win * float(b.get("history_pct", 30.0)) / 100 + 0.5)
    keep_frac = float(c.get("keep_after_compress_pct", 50)) / 100.0

    msgs = session.history()
    used = sum(stack_mod.message_tokens(m, factor) for m in msgs)
    fill = (prompt_tokens / win) if win and prompt_tokens else 0.0

    if fill >= hard:
        pass                                   # must compress, even with little history
    elif fill >= soft:
        if len(msgs) < 4:
            return None                        # nothing to evict yet
    else:
        return None

    # Evict the oldest turns until what remains fits the keep fraction. At the HARD threshold, also refuse to
    # keep more than the last exchange: "compress regardless" has to mean it frees space even when the history
    # block was under its share — otherwise a nearly-full window would compact nothing and stay full.
    keep_target = history_share * keep_frac
    keep_floor = 2 if fill >= hard else 0
    running = used
    evict = 0
    for m in msgs:
        if len(msgs) - evict <= keep_floor:
            break
        # at the hard threshold the keep target does not stop the eviction — only the floor does
        if fill < hard and running <= keep_target:
            break
        running -= stack_mod.message_tokens(m, factor)
        evict += 1
    if evict <= 0:
        return None
    # the digest is written by the turn's own provider (context/digest.py), with the mechanical one as fallback
    from context import digest as digest_mod
    result = session.compress(evict, digest_fn=digest_mod.digest_for(provider, cfg))
    return {"evicted": result.get("evicted"), "retained": result.get("retained"),
            "snapshot": result.get("snapshot"), "vaulted": result.get("vaulted"),
            "summary_written": result.get("summary_written")}

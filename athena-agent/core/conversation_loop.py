"""The Conversation Loop — the broad, dynamic, repeatable layer.

The server's loop: accepts events from Users, Assistants, and Systems —
but ONLY through their proper channels. For each valid event:

    validate channel → build prompt stack (1-5) → message_loop.run_turn
    → persist to session-{UUID}.db + vault.db → continue

Conversation is broad; message is specific (see Message-Loop.md).
"""
from __future__ import annotations

import heapq
import json
import queue
import time
import uuid
from datetime import datetime
from typing import Optional

from . import db as db_layer
from .channels import validate_event, get_channel
from context import compression
from context import retrieval
from context.prompt_builder import build_prompt_stack
from providers.provider import ProviderChain
from intelligence import plugins as plugins_system
from intelligence import skills as skills_system
from .message_loop import MessageLoop
from core.config import load_config


class ConversationLoop:
    """The repeatable conversation driver — one event at a time, forever.

    Profile-aware: each profile is a full agent — its own identity,
    sessions, skills, plugins and vault entries (tagged by profile).
    """

    def __init__(self, config: Optional[dict] = None,
                 providers: Optional[ProviderChain] = None,
                 session_id: Optional[str] = None,
                 profile: Optional[str] = None,
                 on_event: Optional[callable] = None,
                 on_approval: Optional[callable] = None):
        self.cfg = config or load_config(profile=profile or "")
        from intelligence.profiles import get_profile, default_profile
        self.profile = get_profile(profile) or default_profile()
        # The ProviderChain is built from THIS profile's config: each agent
        # owns its config.yaml and is pinned to its own provider+model;
        # authentication.json + .secret stay GLOBAL (shared credentials).
        self.providers = providers or ProviderChain(self.cfg)
        # The observer forwarded to each MessageLoop turn: the CLI renders
        # System/Tool/Skill events live between input and output.
        self.on_event = on_event
        # The interactive approval callback forwarded to each turn (the
        # CLI/GUI prompts Allow/Deny/Block when an unsafe tool needs a
        # decision). None = fail-closed (deny).
        self.on_approval = on_approval
        # THE INTERRUPT (the Operator's 08-12 spec): a threading.Event the
        # operator sets by sending a NEW message mid-turn. Each MessageLoop
        # receives interrupt_flag=self._interrupt.is_set — the running turn
        # checks it every iteration and stops ("interrupted_by_user"),
        # then the new message processes. Clear it when a turn starts.
        import threading as _threading
        self._interrupt = _threading.Event()
        self._streaming_override = None  # the /streaming runtime toggle
        # Auto-resume: the last session for THIS profile (Layer 6).
        # When no session exists AND the caller didn't pin one, generate
        # a UUID but DON'T create the file yet — the file is created
        # lazily on the first message write (never an empty orphan file).
        self.session_id = session_id or db_layer.find_last_session(
            profile=self.profile.name
        )
        if not self.session_id:
            self.session_id = str(uuid.uuid4())
        self._pending: "queue.Queue[dict]" = queue.Queue()
        # The thought queue: (priority, seq, content) — the autonomous work
        # the server considers when the thinking budget allows (Server-Loop.md).
        self._thoughts: list = []
        self._thought_seq = 0
        self.responses: list[dict] = []
        # Systems: plugins + skills for THIS profile (default = global dirs).
        self.plugin_summary = plugins_system.load_all(
            plugins_root=None if self.profile.is_default else self.profile.root
        )
        self.all_skills = skills_system.load_skills(
            self._collect_plugin_skills(),
            profile_dir=None if self.profile.is_default else self.profile.root,
        )

    def _collect_plugin_skills(self):
        """Gather bundled skills from every discovered plugin (this profile)."""
        collected = []
        plugins_root = None if self.profile.is_default else self.profile.root
        for plugin in plugins_system.discover_plugins(plugins_root=plugins_root):
            collected.extend(plugins_system.load_plugin_skills(plugin))
        return collected

    def _skills_for_channel(self, channel):
        """The skills this channel may use (default deny)."""
        return skills_system.filter_by_channel(self.all_skills, channel)

    # -- Door (Platform / CLI / server) --------------------------------
    def handle_event(self, event: dict) -> dict:
        """Queue an event. The channel gate runs when it is processed."""
        event.setdefault("id", str(uuid.uuid4()))
        event.setdefault("ts", time.time())
        event.setdefault("session_id", self.session_id)
        self._pending.put(event)
        return {"ok": True, "event_id": event["id"]}

    
    def set_streaming(self, value: bool) -> None:
        """THE 08-16 RUNTIME STREAMING SETTER: forward to the MessageLoop
        at the next turn (the loop checks streaming per turn)."""
        self._streaming_override = bool(value)

    def handle_thought(self, content: str, priority: float = 0.5) -> dict:
        """Queue an autonomous thought. The server fires it when the budget
        allows and the system channel's may_think gate is open."""
        self._thought_seq += 1
        # heapq is a min-heap; negate priority so HIGHER priority pops first.
        heapq.heappush(self._thoughts, (-priority, self._thought_seq, content))
        return {"ok": True, "queued": content}

    def has_pending(self) -> bool:
        return not self._pending.empty()

    # -- Server Loop gate interface (signal/think) ---------------------
    def has_signal(self) -> bool:
        """True when an autonomous thought is waiting."""
        return bool(self._thoughts)

    def signal_priority(self) -> float:
        """The highest priority waiting thought (for the budget gate)."""
        if not self._thoughts:
            return 0.0
        return -self._thoughts[0][0]

    def fire(self, kind: str, payload: dict | None = None) -> None:
        """Called by the Server Loop when a gate opens."""
        if kind == "message":
            self.drain()
        elif kind == "think":
            self._fire_thought()

    def drain(self) -> None:
        """Process every queued event through the channel gate."""
        while not self._pending.empty():
            event = self._pending.get_nowait()
            self._process_event(event)

    def _fire_thought(self) -> None:
        """Process ONE autonomous thought through the SYSTEM channel.

        The system channel is the only one with may_think=True (channels.py).
        The thought becomes an event with channel=system; the normal channel
        gate + capability gate still apply, then it persists like any turn.
        """
        if not self._thoughts:
            return
        _neg_priority, _seq, content = heapq.heappop(self._thoughts)
        self._process_event({
            "id": str(uuid.uuid4()),
            "ts": time.time(),
            "session_id": self.session_id,
            "channel": "system",
            "content": content,
        })

    # -- The broad loop ------------------------------------------------
    def _process_event(self, event: dict) -> None:
        channel = validate_event(event)
        if channel is None:
            # Not a proper channel — rejected at the gate.
            self.responses.append({
                "event_id": event.get("id"),
                "session_id": event.get("session_id"),
                "reply": "[rejected: unknown channel]",
                "ok": False,
            })
            return

        content = str(event.get("content", ""))
        session_id = str(event.get("session_id", "") or self.session_id)

        try:
            # READ the session's past (the recent window for the stack).
            history = db_layer.get_session_history(
                session_id, limit=200, profile=self.profile.name
            )
            recent_window = int(self.cfg.get("message_loop", {}).get("recent_window", 5))

            # CONTEXT: compress if the conversation is over the threshold.
            # The window is RIGHT-SIZED to the active model (model metadata).
            comp_cfg = self.cfg.get("compression", {})
            try:
                from core.model_metadata import active_model_context
                ctx_win = active_model_context(self.cfg)
            except Exception:
                ctx_win = int(comp_cfg.get("context_window", 32000))
            comp_status = compression.context_status(
                history,
                context_window=ctx_win,
                upper_threshold=float(comp_cfg.get("upper_threshold", 0.8)),
            )
            if comp_status["over_upper"]:
                comp_result = compression.compress_history(
                    session_id,
                    history,
                    context_window=ctx_win,
                    upper_threshold=float(comp_cfg.get("upper_threshold", 0.8)),
                    lower_threshold=float(comp_cfg.get("lower_threshold", 0.4)),
                    recent_window=recent_window,
                    providers=self.providers,
                    profile=self.profile.name,
                )
                # The compression is an event — visible in the logs.
                if comp_result.get("compressed"):
                    from metrics.logger import log
                    log(2, f"context compressed: "
                        f"{comp_result.get('tokens_before', 0)} → "
                        f"{comp_result.get('tokens_after', 0)} tokens "
                        f"(kept {comp_result.get('kept_raw', 0)} raw)",
                        profile=self.profile.name, source="context",
                        action="compress")
                # Reload: history is now summary + recent window.
                history = db_layer.get_session_history(
                    session_id, limit=200, profile=self.profile.name
                )

            # CONTEXT: retrieve more when the session has nothing on topic.
            retrieval_hits = None
            if self.cfg.get("retrieval", {}).get("enabled", True):
                retrieval_hits = retrieval.retrieve(
                    content, session_id, config=self.cfg,
                    profile=self.profile.name,
                )

            # The 5-part prompt stack, exact order (profile-aware identity).
            channel_skills = self._skills_for_channel(channel)
            # Skills are CONTEXT, not CALLS (the Operator's 08-12 spec):
            # they load into the prompt silently. The GUI stream shows
            # the REAL work — tool calls + the reasoning chain + the
            # typed reply — never the skill-load noise.
            system_prompt = build_prompt_stack(
                channel=channel.name,
                channel_instructions=channel.instructions,
                profile_root=None if self.profile.is_default else self.profile.root,
                history=history,
                recent_window=recent_window,
                session_id=session_id,
                retrieved=retrieval_hits,
                skills_index=skills_system.skills_index(channel_skills),
            )
            # SYSTEM HANDS-OFF (the Operator's spec): a system-channel event
            # (an autonomous thought — scheduler tick, nurse consult,
            # janitor pass) is the WRAPPER working, not the user/assistant
            # talking. It gets NO approval surface — unsafe tools fail
            # closed (denied) silently; only user/assistant events prompt.
            is_system = getattr(channel, "name", "") == "system"
            # The interrupt resets when a turn begins (the new turn is a
            # fresh run — the flag only cuts the turn it was set during).
            try:
                self._interrupt.clear()
            except Exception:
                pass
            loop = MessageLoop(
                providers=self.providers,
                system_prompt=system_prompt,
                max_iterations=int(self.cfg.get("message_loop", {}).get("max_iterations", 100)),
                max_tokens=int(self.cfg.get("message_loop", {}).get("max_tokens", 0)) or None,
                channel=channel,   # the capability gate (default deny)
                on_event=self.on_event,
                on_approval=None if is_system else self.on_approval,
                interrupt_flag=lambda: self._interrupt.is_set(),
                streaming=self._streaming_override,   # THE 08-16 RUNTIME TOGGLE
            )
            result = loop.run_turn(content, history=history)
            reply = result.reply

            # EMOTION CYCLE (the Operator's 08-11 spec): the turn is the
            # EXPERIENCE. The LLM gauges how IT feels and how the OPERATOR
            # feels from the words spoken — filling the complete 8-axis
            # vector, iterating over time. The snapshot flows into the
            # vault rows below:
            #   emotion = the numeric VECTOR (JSON) — the INWARD feeling,
            #             the complete private state (the time-series the
            #             polygraph plots)
            #   mood    = the ACTIVE COMBINATION (the pair-map result) —
            #             the OUTWARD display, what a reader would see
            #             ("Love (Affection)" vs the vector beneath it)
            emotion_snapshot, mood_snapshot = None, None
            try:
                from core.emotion import (gauge_turn, read_emotion,
                                          active_combinations)
                gauge_turn(self.profile.name, {
                    "user_message": content,
                    "reply": reply,
                    "exit_reason": getattr(result, "exit_reason", None),
                    "tool_failures": [t for t in (result.tool_transcript or [])
                                      if "error" in str(t.get("result", "")).lower()],
                })
                _a = read_emotion("assistant", self.profile.name)
                # Inward: the numeric vector (JSON).
                try:
                    import json as _json
                    emotion_snapshot = _json.dumps(_a.get("vector", {}))
                except Exception:
                    emotion_snapshot = None
                # Outward: the LIST of matched emotions (the mood — every
                # active combination the vector produces, canonical +
                # synonym). "Love (Affection), Joy (Joy)" vs the vector
                # beneath it.
                try:
                    _combos = active_combinations(_a.get("vector", {}))
                    if _combos:
                        mood_snapshot = ", ".join(
                            f"{_c['canonical']} ({_c['synonym']})"
                            for _c in _combos)
                    else:
                        mood_snapshot = "Neutral"
                except Exception:
                    mood_snapshot = _a.get("current") or None
            except Exception:
                pass  # emotion learning never breaks the turn

            # RESPONSE LENGTH: gauge the level from the user's message,
            # learn from the ACTUAL reply length, and record the ADJUSTED
            # level on both stored copies (session + vault). Athena sees
            # the past 5 messages natively — each carrying its levels —
            # so the history itself trains her (examples of the limits
            # in use, without retraining).
            #
            # The response-length GROUP is stored as REAL INTEGER COLUMNS
            # (the Operator's schema: 1 variable = 1 column, groups capped at
            # 3, no duplication — no JSON blob for structured metadata):
            #   response_length            = actual response word count
            #   response_prediction = the gauged level's word cap
            #   response_adjustment   = the matching level's word cap
            rl_vals = {"response_length": None,
                       "response_prediction": None,
                       "response_adjustment": None}
            try:
                from core.response_length import gauge, learn_usage, _word_count
                prediction = gauge(content, profile=self.profile.name)["words"]
                actual = _word_count(reply)
                learn_usage(content, actual, profile=self.profile.name)
                adjusted = gauge(content, profile=self.profile.name)["words"]
                rl_vals = {
                    "response_length": int(actual),
                    "response_prediction": int(prediction),
                    "response_adjustment": int(adjusted),
                }
            except Exception:
                pass

            # PERSIST: session file + archive, tagged with the profile.
            # The chat-format fields (the Operator's spec): name (participant),
            # tool_call / skill_call (the call references), model, provider
            # (which model/provider produced the reply), reason_stop, usage.
            try:
                from core.config import load_config
                _cfg = load_config()
                _sel = _cfg.get("provider", {}).get("selection", {}).get(
                    "reason", {}) or {}
                _prov = str(_sel.get("provider") or "")
                _model = str(_sel.get("model") or "")
                if not _model:
                    try:
                        from providers.switch import active_model_for
                        _model = active_model_for(_prov) or _prov
                    except Exception:
                        _model = _prov
            except Exception:
                _prov, _model = "", ""
            # The usage group: token accounting from the provider response
            # (OpenAI/Anthropic usage shape) — NULL when not reported.
            usage = getattr(result, "usage", None) or {}
            try:
                usage_prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0) or None
                usage_completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0) or None
                usage_total = int(usage.get("total_tokens") or 0) or None
            except Exception:
                usage_prompt = usage_completion = usage_total = None
            user_call = None
            for t in result.tool_transcript:
                if t.get("tool_name") and not user_call:
                    user_call = t.get("tool_call_id") or t.get("tool_name")
            skill_names = [sk.name for sk in channel_skills] or None
            # The SKILL columns (the Operator's spec, mirroring tools): skill =
            # the human-readable name; skill_call = what was used from the
            # skill (its description); skill_id = the load's own ID (a
            # generated UUID — like a session UUID, the skill-load's
            # identifier). Skills load passively, so each loaded skill
            # gets a call record of its own.
            skill_calls = None
            if channel_skills:
                skill_calls = [
                    {
                        "skill": sk.name,
                        "skill_call": sk.description or "",
                        "skill_id": str(uuid.uuid4()),
                    }
                    for sk in channel_skills
                ]
            # NAMES come from the identity files (the Operator's spec): the
            # assistant rows carry ASSISTANT.md's identity, the user rows
            # carry USER.md's — auto-populated, never guessed.
            try:
                from core.identity import agent_identity, user_identity
                ai = agent_identity(None if self.profile.is_default
                                    else self.profile.root)
                ui = user_identity(None if self.profile.is_default
                                   else self.profile.root)
                a_first = (ai.get("name_first") or ai.get("first_name") or "").strip()
                a_last = (ai.get("name_last") or ai.get("last_name") or "").strip()
                a_nick = (ai.get("name_nick") or ai.get("nickname") or "").strip() or a_first
                u_first = (ui.get("name_first") or ui.get("first_name") or "").strip()
                u_last = (ui.get("name_last") or ui.get("last_name") or "").strip()
                u_nick = (ui.get("name_nick") or ui.get("nickname") or "").strip() or u_first
            except Exception:
                a_first = a_last = a_nick = u_first = u_last = u_nick = ""
            db_layer.record_session_message(
                session_id, "user", content,
                profile=self.profile.name,
                name_first=u_first or None, name_last=u_last or None,
                name_nick=u_nick or None,
                # reason_pending: the request is queued while it waits.
                reason_pending=datetime.now().isoformat(timespec="seconds"),
                response_length=rl_vals["response_length"],
                response_prediction=rl_vals["response_prediction"],
                response_adjustment=rl_vals["response_adjustment"],
            )
            db_layer.record_session_message(
                session_id, "assistant", reply,
                profile=self.profile.name,
                name_first=a_first or None, name_last=a_last or None,
                name_nick=a_nick or None,
                api_model=_model, api_provider=_prov,
                # THE FLOW (the Operator's 08-12 persistence spec): the
                # turn's full thinking record — reasoning chain + every
                # tool/skill call (kind, name, args, result). Stored in
                # the message meta so the Thinking block survives reload.
                meta={"flow": _build_flow(result, channel_skills or [])} if (
                    _build_flow(result, channel_skills or [])) else None,
                # reason_stop = WHY it stopped (exit_reason, not "stop").
                reason_start=datetime.now().isoformat(timespec="seconds"),
                reason_stop=getattr(result, "exit_reason", None)
                or "completed",
                usage_prompt=usage_prompt, usage_completion=usage_completion,
                usage_total=usage_total,
                response_length=rl_vals["response_length"],
                response_prediction=rl_vals["response_prediction"],
                response_adjustment=rl_vals["response_adjustment"],
                emotion=emotion_snapshot, mood=mood_snapshot,
            )
            # TURN SUMMARY (the Operator's spec): a one-line recap of the
            # exchange, stored as the session's rolling summary — the
            # session becomes searchable by what happened.
            try:
                from core.turn_summary import summarize_turn
                summarize_turn(
                    session_id, content, reply,
                    tool_names=[t.get("tool_name") for t in
                                (result.tool_transcript or [])
                                if t.get("tool_name")] or None,
                    skills=[sk.name for sk in channel_skills] or None,
                    profile=self.profile.name,
                )
            except Exception:
                pass
            tool_names = [t.get("tool_name", "") for t in result.tool_transcript]
            db_layer.record_vault_entry(
                "message", content, role="User",
                source=channel.name,
                profile=self.profile.name,
                name_first=u_first or None, name_last=u_last or None,
                name_nick=u_nick or None,
                response_length=rl_vals["response_length"],
                response_prediction=rl_vals["response_prediction"],
                response_adjustment=rl_vals["response_adjustment"],
            )
            db_layer.record_vault_entry(
                "message", reply, role="Assistant",
                source=channel.name,
                profile=self.profile.name,
                name_first=a_first or None, name_last=a_last or None,
                name_nick=a_nick or None,
                tool=json.dumps(tool_names) if tool_names else None,
                skill=json.dumps(skill_calls) if skill_calls else None,
                api_model=_model, api_provider=_prov,
                emotion=emotion_snapshot, mood=mood_snapshot,
                # The reason GROUP (the Operator's spec): reason = the model's
                # reasoning CHAIN (how the response was crafted);
                # reason_start = when generation began; reason_stop = WHY
                # it stopped (the loop's exit_reason: completed |
                # budget_exhausted | provider_error | interrupted_by_user
                # — NOT the raw "stop").
                reason=(getattr(result, "reasoning", None) or None),
                reason_start=datetime.now().isoformat(timespec="seconds"),
                reason_stop=getattr(result, "exit_reason", None)
                or "completed",
                usage_prompt=usage_prompt, usage_completion=usage_completion,
                usage_total=usage_total,
                response_length=rl_vals["response_length"],
                response_prediction=rl_vals["response_prediction"],
                response_adjustment=rl_vals["response_adjustment"],
            )
            # Each loaded skill gets its own archive entry (the Operator's
            # spec): type=skill, role=System, skill=name, skill_call=the
            # skill's description (what was used), skill_id=a generated
            # UUID (the skill-load's own identifier — like a tool id).
            for sc in skill_calls or []:
                db_layer.record_vault_entry(
                    "skill", sc.get("skill_call", ""),
                    role="System", source=channel.name,
                    profile=self.profile.name,
                    skill=sc.get("skill") or None,
                    skill_call=sc.get("skill_call") or None,
                    skill_id=sc.get("skill_id") or None,
                    api_model=_model, api_provider=_prov,
                )
            for t in result.tool_transcript:
                db_layer.record_vault_entry(
                    "tool", t.get("result", ""),
                    # The Operator's role vocabulary: System | Agent | Assistant
                    # | User — a tool/skill execution is a SYSTEM action.
                    role="System", source=channel.name,
                    profile=self.profile.name,
                    # The Operator's tool columns: tool = the human-readable
                    # NAME; tool_call = the ARGUMENTS string (JSON, what
                    # was passed); tool_id = the call's own ID (like a
                    # session UUID — the call's identifier).
                    tool=(t.get("tool_name") or "").strip() or None,
                    tool_call=t.get("arguments") or None,
                    tool_id=t.get("tool_call_id") or None,
                    api_model=_model, api_provider=_prov,
                )
        except Exception as exc:  # noqa: BLE001
            from core.logging import log_event
            import traceback as _tb
            log_event(4, f"conversation loop error: {exc}\n{_tb.format_exc()[:2000]}",
                      source="runtime", action="handle_message")
            reply = f"[conversation loop error: {exc}]"

        # THE ERROR-PATH FLOW GUARD (the Operator's 08-14 fix): when the
        # provider/DB raised, `result` is never assigned — the flow must
        # degrade to [] instead of a NameError ("cannot access local
        # variable 'result'") that turned the error reply into a worker
        # crash.
        try:
            _flow = _build_flow(result, skill_calls or [])
        except Exception:
            _flow = []
        self.responses.append({
            "event_id": event.get("id"),
            "session_id": session_id,
            "reply": reply,
            "ok": True,
            "channel": channel.name,
            # THE FLOW (the Operator's 08-12 spec): the
            # turn's THINKING stream — every system/tool/skill call made
            # while producing the reply (name, args, result, kind). The
            # GUI renders these between the message and the response.
            "flow": _flow,
        })


def _build_flow(result, skill_calls: list | None = None) -> list:
    """The turn's THINKING stream (the Operator's 08-12 spec,
    adapted): every CALL made while producing the reply.

    Each entry: {kind, name, args, result} where kind is
    "system" | "tool" — the GUI shows the whole chain between
    the operator message and the agent's response (the flow:
    Agent ›› Thinking (the calls) ›› Response).

    Skills are NOT passive-context noise, but they ARE callable: the
    agent invokes `skill_load {name}` to apply a skill — that call is
    real work, rendered as a SKILL row (🖊️ skill:name). Tools render
    as tool rows.
    """
    flow = []
    # Tools/system calls in execution order.
    for t in (result.tool_transcript or []):
        name = str(t.get("tool_name") or "").strip()
        if not name:
            continue
        args = t.get("arguments") or ""
        result_txt = str(t.get("result") or "")[:200]
        # A skill_load call IS a skill call (the Operator's 08-12
        # mirror rule): tools and skills are the same mechanism, so the
        # invocation displays as 🖊️ skill:<name> with the loaded
        # skill's instructions as its result.
        if name == "skill_load":
            sk_name = "skill"
            try:
                _a = json.loads(args) if args else {}
                sk_name = str(_a.get("name") or "skill")
            except Exception:
                pass
            flow.append({
                "kind": "skill",
                "name": sk_name,
                "args": "",
                "result": result_txt,
            })
            continue
        # A tool call is a SYSTEM action (the Operator's role
        # vocabulary) — but the agent's own tools read as "tool".
        flow.append({
            "kind": "tool",
            "name": name,
            "args": args,
            "result": result_txt,
        })
    return flow

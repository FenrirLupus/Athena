"""The Message Loop — the bounded turn (Layer 4).

The SPECIFIC layer: how ONE message is handled. The native bounded cycle,
the same shape used — one user message in, one reply out, tool
calls between.

    ┌─ while (calls < max_iterations AND budget > 0) ──────┐
    │  model call → EITHER:                                 │
    │    • tool_calls → run tools → append results → LOOP   │
    │    • final text → done → EXIT ✓                       │
    └───────────────────────────────────────────────────────┘

Always terminates: the model stops asking for tools (normal), the budget
runs out (hard cap), or the server sets the interrupt flag (external).

The Conversation layer (broad) calls run_turn once per message and owns the
session/history/summaries (Layer 6 + 7).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from providers.provider import ProviderChain, ProviderError
from filesystem import tools as tool_registry


@dataclass
class TurnResult:
    reply: str
    updated_history: list = field(default_factory=list)
    tool_calls_made: int = 0
    api_calls: int = 0
    exit_reason: str = "completed"
    # Native chat-format transcript of tool activity: each entry is
    # {tool_name, tool_call_id, arguments, result} — archived to the vault.
    tool_transcript: list = field(default_factory=list)
    # Provider metadata (the schema's reason + usage groups): why the
    # generation stopped and the token accounting — parsed from ANY
    # provider format (OpenAI finish_reason/usage, Anthropic
    # stop_reason/usage.input_tokens, LM Studio/Qwen/DeepSeek = OpenAI).
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)
    # The model's REASONING CHAIN (the "how the response was crafted"
    # text — DeepSeek reasoning_content, OpenAI reasoning, etc.). This is
    # the `reason` column's value: the string of how the reply was made.
    reasoning: str | None = None


def _resolve_profile_name(loop) -> str:
    """Resolve the ACTIVE profile name for tool dispatch (the 08-17
    profile-alignment fix): profiles are individual, isolated — a tool
    must always target the profile that is actually running."""
    try:
        p = getattr(loop, "profile", None)
        return str(getattr(p, "name", "") or "") if p is not None else ""
    except Exception:
        return ""


class MessageLoop:
    """One bounded turn: system prompt + history + user message → reply."""

    def __init__(self, providers: Optional[ProviderChain] = None,
                 system_prompt: str = "",
                 max_iterations: int = 500,
                 max_tokens: Optional[int] = None,
                 interrupt_flag: Optional[callable] = None,
                 channel=None,
                 on_event: Optional[callable] = None,
                 on_approval: Optional[callable] = None,
                 streaming: Optional[bool] = None,
                 subagent: bool = False):
        self.providers = providers or ProviderChain()
        self.system_prompt = system_prompt
        self.max_iterations = max(1, max_iterations)
        # THE STREAMING FLAG (the Operator's 08-12 spec): when True, the
        # provider call uses stream:true and every token delta forwards
        # through on_event("delta", text) — the GUI types the reply live.
        # Defaults from the provider config (config.yaml
        # provider.streaming); an explicit override wins.
        if streaming is None:
            try:
                from core.config import load_config
                _cfg = load_config()
                # THE STREAMING NULL-SKIP (the 08-14 fix): a null
                # streaming value in config means "use the default" —
                # and the default is TRUE (live typing). bool(None)
                # would have silently disabled streaming on a seeded
                # .default config.
                _s = _cfg.get("provider", {}).get("streaming")
                streaming = True if _s is None else bool(_s)
            except Exception:
                streaming = True
        self.streaming = bool(streaming)
        # LOOP GUARDRAILS (adapted): per-turn runaway-loop
        # protection — exact-failure, same-tool, no-progress, loop caps.
        # reset_for_turn() runs at the top of every turn.
        # THE SUBAGENT RELAXATION (the Operator's 08-12 fix): subagent
        # turns (MCP chat, delegated tasks) get a HIGHER identical-read
        # threshold — their first-pass exploration (ls + reads of an
        # empty sandbox) is legitimate, and the old threshold logged a
        # spurious loop-guard block on every MCP chat. Main turns keep
        # the configured thresholds.
        try:
            from security.loop_guardrails import LoopGuardrails
            from core.config import load_config
            _lg_cfg = load_config().get("tool_loop_guardrails", {})
            if subagent:
                _lg_cfg = dict(_lg_cfg)
                _lg_cfg["hard_stop_after"] = dict(
                    _lg_cfg.get("hard_stop_after", {}) or {})
                _lg_cfg["hard_stop_after"]["exact_failure"] = 8
                _lg_cfg["hard_stop_after"]["idempotent_no_progress"] = 8
            self.loop_guardrails = LoopGuardrails(_lg_cfg)
            # THE 08-15 TEST-MODE WIRE: the doctor's self-tests set a
            # module flag — the loop's guardrails drop their log level so
            # the mocked-loop noise doesn't pollute the L3 log.
            try:
                import security.loop_guardrails as _lg_mod
                if getattr(_lg_mod, "DOCTOR_TEST_MODE", False):
                    self.loop_guardrails.test_mode = True
            except Exception:
                pass
        except Exception:
            self.loop_guardrails = None
        # Token cap per response (None = the model's default). The iteration
        # budget sets this: main agents use the configured limit; subagents
        # get 50% of it.
        self.max_tokens = max_tokens
        # A callable returning True means "stop the loop now" (user interrupt).
        self.interrupt_flag = interrupt_flag or (lambda: False)
        # THE SELECTION SUPPRESSION (the 08-15 fix): the workflow SELECTION
        # call (iteration 1 when no workflow is set) is INTERNAL machinery —
        # its streamed deltas must NOT render as a visible reply (the
        # "empty block before the response + populated block after" bug:
        # the selection's text leaked as the first response). Set before
        # the iteration-1 call; cleared once the selection is parsed.
        self._suppress_deltas = False
        # THE GUIDED-WORKFLOW NOTE (the 08-15 spec): set when the model
        # names a non-existent workflow — the caller appends it to the
        # System prompt so the model knows it fell back + can offer a
        # custom workflow.
        self._selection_note = ""
        # The capability gate: the channel whose tools/skills this turn may
        # use. None = no gate (caller hasn't given a channel).
        self.channel = channel
        # The OBSERVER: called for every notable event DURING the turn
        # (system events, tool calls, skill loads) so the CLI can render
        # the flow live between the user's input and the assistant's
        # output. Signature: on_event(kind, detail) where kind is
        # "system" | "tool" | "skill".
        self.on_event = on_event
        # The APPROVAL callback (interactive permissions): called when an
        # unsafe tool needs a user decision. Signature:
        #     on_approval(tool, arguments, risk) -> (verdict, scope)
        #   verdict: "allow" | "deny" | "block"
        #   scope:   "once" | "session" | "global"
        # None = NO interactive surface (CLI/GUI absent) → the tool is
        # DENIED (fail-closed: unsafe means unsafe until a human says so).
        self.on_approval = on_approval
        # THE WORKFLOW STATE (the Operator's 08-15 spec): the START >
        # CONTINUE > STOP lifecycle. `workflow_name` = the selected lane
        # (None = the START selection hasn't run yet); `chain_hops` guards
        # the chain (max hops, no self-continuation).
        self.workflow_name: Optional[str] = None
        self.workflow: Optional[dict] = None
        self.chain_hops = 0
        # Callers may pre-select (a chain hop from another turn, or an
        # explicit choice); otherwise the START call picks it.
        try:
            if channel is not None and getattr(channel, "workflow", None):
                self.workflow_name = channel.workflow
        except Exception:
            pass

    def set_streaming(self, value: bool) -> None:
        """THE 08-16 RUNTIME STREAMING SETTER: flip the streaming knob
        WITHOUT a restart. The /streaming CLI command calls this; the
        loop checks self.streaming at the next turn start."""
        self.streaming = bool(value)

    def _emit(self, kind: str, detail: str, extra: str = "") -> None:
        """Fire the observer (never raises — display must not break work)."""
        if self.on_event is not None:
            try:
                self.on_event(kind, detail, extra)
            except Exception:
                pass

    def _tool_allowed(self, tool_name: str, arguments: dict | None = None) -> bool:
        """Default DENY: a tool is usable only if the channel allows it.

        THE 08-15 GATE REORDER (the Operator's fix): the PERMISSION engine
        runs FIRST (persisted rules + session rules + the interactive
        approval — with the session_id carried through so session-scoped
        allows actually apply), THEN the channel as the final policy
        bound. The old order (channel first) made a session/global allow
        useless for tools not pre-listed on the channel, and write tools
        unreachable from the user channel.

        Order:
          1. SKILL gate (skill:<name> resolves through allows_skill)
          2. PERMISSION engine — check(tool, args, session_id):
               blocked → refuse; safe → allow; persisted/session rule →
               its verdict; in-bounds unsafe → allow; else NEEDS_PROMPT →
               on_approval asks; the verdict persists per scope
          3. CHANNEL policy — the final bound (a tool the channel never
             allows stays bounded, but approvals on listed tools persist)
        """
        arguments = arguments or {}
        # THE SKILL GATE (the 08-12 standardized schema): a skill:<name>
        # call is allowed when the channel permits that skill (or "*").
        # It resolves through the channel's allows_skill, not the tool
        # gate (skills aren't tools, but they execute the same way).
        if isinstance(tool_name, str) and tool_name.startswith("skill:"):
            skill_name = tool_name[len("skill:"):]
            if self.channel is not None:
                return self.channel.allows_skill(skill_name)
            return True
        # THE PERMISSION ENGINE FIRST (the 08-15 fix): the persisted +
        # session rules + the approval, with the session_id carried so
        # session-scoped allows actually apply.
        try:
            from security.permissions import check, decide
            _profile = ""
            try:
                from intelligence.profiles import get_profile
                _p = get_profile(getattr(self, "profile", None) or "")
                if _p is not None:
                    _profile = _p.name
            except Exception:
                pass
            _sid = getattr(self, "session_id", "") or ""
            perm = check(tool_name, arguments, profile=_profile,
                         session_id=_sid)
            if perm["allowed"]:
                # Allowed by the engine (safe / rule / in-bounds) — now the
                # channel is the final policy bound.
                if self.channel is not None and not self.channel.allows_tool(tool_name):
                    return False
                return True
            if perm["verdict"] in ("deny", "block") or not perm["needs_prompt"]:
                return False
            # NEEDS_PROMPT: ask the interactive surface (CLI/GUI).
            if self.on_approval is not None:
                try:
                    verdict, scope = self.on_approval(tool_name, arguments,
                                                      perm["risk"])
                    if verdict in ("allow", "deny", "block"):
                        decide(tool_name, verdict, scope, profile=_profile,
                               session_id=_sid)
                        if verdict == "allow":
                            # The channel is the final bound even after an
                            # approval — a channel that never allows this
                            # tool stays closed (the operator can add it in
                            # the Permissions tab).
                            if self.channel is not None and not self.channel.allows_tool(tool_name):
                                return False
                            return True
                        return False
                except Exception:
                    return False  # a broken prompt never unlocks the gate
            return False  # fail-closed: no interactive surface = denied
        except Exception:
            pass  # permission store errors never break the gate
        # Fallback: the channel bound alone (no permission engine).
        if self.channel is not None and not self.channel.allows_tool(tool_name):
            return False
        return True

    def _guided_tool_denial(self, tool_name: str) -> str:
        """THE GUIDED-DENIAL (the Operator's 08-15 spec): the model called
        a tool that does NOT exist (a hallucination). The denial (1) states
        plainly there is no such tool or skill, (2) lists the REAL
        advertised tools/skills that could serve the intent (by keyword
        overlap + the channel's allowed set) so she ACTIVATES an existing
        one, and (3) when nothing exists, offers to CREATE A SKILL in its
        place — she has the workflows (learning/writer/programmer) to
        autonomously build it, but she ASKS the operator first whether they
        want it implemented."""
        try:
            from filesystem.tools import canonical_names, schemas_with_skills
            from intelligence.skills import load_skills
            # The full advertised surface the model can actually call.
            advertised = set(canonical_names())
            for s in (schemas_with_skills(load_skills()) or []):
                fn = s.get("function", {}) or {}
                n = fn.get("name", "")
                if n:
                    advertised.add(n)
            # The channel's allowed tools (what would actually pass the gate).
            if self.channel is not None:
                allowed = set(getattr(self.channel, "tools", []) or [])
            else:
                allowed = advertised
            # Keyword overlap: split the hallucinated name into tokens and
            # find advertised tools/skills sharing a token (weather → search).
            import re as _re
            _tokens = set(_re.findall(r"[a-z]+", tool_name.lower()))
            _candidates = []
            for name in sorted(advertised):
                if name not in allowed:
                    continue
                _nt = set(_re.findall(r"[a-z]+", name.lower()))
                if _tokens & _nt:
                    _candidates.append(name)
            # Always include the general-purpose tools that can do nearly
            # anything (terminal + web_search/web_extract).
            for extra in ("terminal", "web_search", "web_extract"):
                if extra in advertised and extra not in _candidates:
                    _candidates.append(extra)
            if _candidates:
                _cand_str = ", ".join(_candidates[:6])
                return (
                    f"[no such tool or skill: '{tool_name}' does not exist. "
                    f"Available tools/skills that CAN serve this: {_cand_str}. "
                    f"ACTIVATE one of those instead — or ask the operator if "
                    f"they would like a '{tool_name}' TOOL and/or SKILL "
                    f"created (a tool is a hands-off button, a skill is the "
                    f"hands-on brain — create whichever is the most proper "
                    f"and efficient version for the purpose, not just "
                    f"because; I have the workflows to build it autonomously, "
                    f"but I need their go-ahead).]"
                )
            return (
                f"[no such tool or skill: '{tool_name}' does not exist and "
                f"no existing tool or skill matches this intent. I could "
                f"CREATE a '{tool_name}' TOOL and/or SKILL in its place — "
                f"a tool is a hands-off button, a skill is the hands-on "
                f"brain; create whichever is the most proper and efficient "
                f"for the purpose (or both only if truly necessary), with "
                f"reason and purpose — I have the workflows "
                f"(learning/writer/programmer) to build it autonomously. "
                f"Ask the operator if they would like that implemented.]"
            )
        except Exception:
            return (
                f"[no such tool or skill: '{tool_name}' does not exist. "
                f"Use an existing tool or skill instead, or ask the operator "
                f"if they would like a '{tool_name}' skill created.]"
            )

    def _check_interrupt(self) -> bool:
        try:
            return bool(self.interrupt_flag())
        except Exception:
            return False

    @staticmethod
    def _normalize_history(history) -> list[dict]:
        """Convert stored history rows into clean OpenAI message shapes.

        Session rows carry extra fields (id, ts, seq, ...) the API would
        reject; only role/content (and tool_call_id for tool messages)
        survive. Unknown roles are dropped silently.
        """
        out: list[dict] = []
        for row in history or []:
            if not isinstance(row, dict):
                continue
            role = row.get("role")
            if role not in ("user", "assistant", "system", "tool"):
                continue
            content = row.get("content")
            if content is None:
                content = ""
            msg = {"role": role, "content": str(content)}
            if role == "tool" and row.get("tool_call_id"):
                msg["tool_call_id"] = row["tool_call_id"]
            if role == "assistant":
                # THE REASONING_CONTENT REPLAY (the 08-14 zen-400 fix):
                # DeepSeek v4 thinking mode requires reasoning_content on
                # every assistant message replayed to the API. Persisted
                # assistant rows carry it (the loop injects it); keep it
                # through normalization or the replay 400s. Tool calls
                # also survive (their ids must match the tool results).
                if row.get("reasoning_content"):
                    msg["reasoning_content"] = str(row["reasoning_content"])
                if row.get("tool_calls"):
                    msg["tool_calls"] = row["tool_calls"]
                    # THE SPACE-PAD (the streaming refs #15250/#17400): a tool-call
                    # assistant message WITHOUT captured reasoning still
                    # needs non-empty reasoning_content for V4 Pro — a
                    # single space satisfies the relay's check without
                    # fabricating a chain of thought.
                    if not msg.get("reasoning_content"):
                        msg["reasoning_content"] = " "
            out.append(msg)
        return out

    def _ensure_workflow(self) -> str:
        """THE START PHASE (the Operator's 08-15 spec): pick the workflow.

        When no workflow is pre-selected, run the START selection — a
        single cheap call asking which of the 10 lanes applies. Load the
        chosen workflow's sections + requirements. Fallback = conversation.
        Returns the workflow name (never raises).
        """
        try:
            from workflows.registry import select_workflow
            if self.workflow is None:
                if self.workflow_name:
                    # A pre-set workflow (sticky or caller-provided): load it.
                    wf = select_workflow(self.workflow_name)
                    self.workflow = wf
                    self.workflow_name = (wf or {}).get("name", "conversation")
                # NO selection yet: leave self.workflow = None so the
                # PROMPT-FIRST ask fires and the FIRST response picks the
                # lane from the full 5-section context.
            return self.workflow_name or "conversation"
        except Exception:
            self.workflow_name = "conversation"
            self.workflow = None
            return self.workflow_name

    def _workflow_selection_block(self) -> str:
        """THE PROMPT-FIRST SELECTION (the CEO's 08-15 correction): the
        workflow list + the selection ask fold into the System section of
        the FULL prompt — NO minimal START call. The model reads the whole
        5-section stack (History summary + raw turns drive the pick) and
        answers with the workflow name at the start of its first response.
        Returns '' when a sticky workflow is already applied (no re-ask)."""
        try:
            from workflows.registry import selection_prompt
            if self.workflow is not None and self.workflow_name:
                # Sticky: already selected for this turn — no re-ask.
                return ""
            return (selection_prompt() +
                    "\n\nBegin your response with: workflow: <name>"
                    "\nThen proceed with the task.")
        except Exception:
            return ""

    def _apply_selection(self, response: str) -> str:
        """Parse the workflow name from the first full-prompt response
        (a leading 'workflow: <name>' line, or the first token matching a
        lane). Loads the chosen workflow's doc + requirements for CONTINUE.
        Fallback = the default workflow. Returns the workflow name."""
        try:
            from workflows.registry import workflow_names, select_workflow
            if self.workflow is not None:
                return self.workflow_name or "conversation"
            text = str(response or "").strip()
            _name = ""
            for line in text.splitlines():
                line = line.strip()
                if line.lower().startswith("workflow:"):
                    _name = line.split(":", 1)[1].strip(" .\"'`").lower()
                    break
            if not _name:
                # The first token that matches a lane (prose fallback).
                _tok = text.split()[0].strip(" .\"'`").lower() if text.split() else ""
                if any(_tok == n for n in workflow_names()):
                    _name = _tok
            if not _name:
                _name = self.workflow_name or ""
            # THE GUIDED-WORKFLOW (the Operator's 08-15 spec): detect a
            # NON-EXISTENT lane BEFORE select_workflow (which always
            # falls back to the default) — the model named a workflow
            # that isn't built-in.
            _selected_missing = False
            try:
                from workflows.registry import workflow_names
                if _name and _name not in workflow_names():
                    _selected_missing = True
            except Exception:
                _selected_missing = False
            wf = select_workflow(_name)
            self.workflow = wf
            self.workflow_name = (wf or {}).get("name", "conversation")
            if _selected_missing:
                try:
                    from workflows.registry import workflow_names
                    _lanes = ", ".join(sorted(workflow_names()))
                    self._selection_note = (
                        f"\n\nNOTE: the workflow you named ('{_name}') does not "
                        f"exist. Available workflows: {_lanes}. Continue with "
                        f"conversation — OR, if NONE of the built-in workflows "
                        f"match what the operator is asking, you may OFFER to "
                        f"create a custom workflow that matches their ask "
                        f"(only if none match, or the operator asks directly "
                        f"to create one). If the operator asks to create a "
                        f"workflow/skill/tool directly, HONOR it — only push "
                        f"back when it already exists."
                    )
                except Exception:
                    self._selection_note = ""
            # THE STICKY (the CEO's 08-15 correction): record the session's
            # workflow so the next turn pre-loads it.
            try:
                from workflows.registry import set_sticky_workflow
                _sid = getattr(self, "session_id", "") or ""
                if _sid:
                    set_sticky_workflow(_sid, self.workflow_name)
            except Exception:
                pass
            return self.workflow_name
        except Exception:
            self.workflow_name = "conversation"
            self.workflow = None
            return self.workflow_name

    def _maybe_clear_sticky(self, user_message: str) -> bool:
        """THE SHIFT SIGNAL (the CEO's 08-15 correction): an explicit new
        request ends the session's sticky workflow so the next turn
        re-selects from the full context. Returns True when cleared."""
        _sig = ["let's stop", "new topic", "switch to", "that's enough",
                "stop the scene", "now build", "new task", "change of plan",
                "different workflow", "enough roleplay"]
        _m = (user_message or "").lower()
        if any(s in _m for s in _sig):
            try:
                from workflows.registry import clear_sticky_workflow
                _sid = getattr(self, "session_id", "") or ""
                if _sid:
                    clear_sticky_workflow(_sid)
            except Exception:
                pass
            self.workflow = None
            self.workflow_name = ""
            return True
        return False

    def _workflow_requirements_prompt(self) -> str:
        """THE CONTINUE CHECKLIST: the workflow's requirements rendered with
        label + description + completed (the Operator's 08-15 spec)."""
        try:
            from workflows.registry import requirements_of
            if self.workflow is None:
                return ""
            reqs = requirements_of(self.workflow)
            if not reqs:
                return ""
            lines = ["The requirements for this call (label: description — completed):"]
            for r in reqs:
                lines.append(f"- {r['label']}: {r['description']} "
                             f"[{'done' if r['completed'] else 'pending'}]")
            lines.append("Fulfill every pending requirement before stopping.")
            return "\n".join(lines)
        except Exception:
            return ""

    def run_turn(self, user_message: str, history: Optional[list] = None) -> TurnResult:
        """Run the bounded cycle. Returns the final reply + updated history."""
        # TURN RETRY STATE (the Operator's spec): one retry per provider/model
        # per turn for transient failures. Bounded + reset each turn.
        import uuid as _uuid
        self._turn_id = str(_uuid.uuid4())
        try:
            from core.turn_retry import begin_turn
            begin_turn(self._turn_id)
        except Exception:
            pass
        # LOOP GUARDRAILS (adapted): counters reset per turn.
        try:
            if self.loop_guardrails is not None:
                self.loop_guardrails.reset_for_turn()
        except Exception:
            pass
        # SESSION STATE (the Operator's flow machine): Idle → Thinking.
        try:
            from core.session_state import get_state
            get_state(getattr(self, "session_id", "") or "default"
                      ).start_turn(self._turn_id)
        except Exception:
            pass
        messages = self._normalize_history(history)
        # THE WORKFLOW CONTEXT (the Operator's 08-15 spec): the START
        # phase runs BEFORE the main loop — pick the workflow, then the
        # system prompt carries its sections + the requirement checklist
        # (the CONTINUE contract). The user message is stashed for the
        # START call.
        self._pending_user = user_message
        # THE SHIFT SIGNAL (the CEO's 08-15 correction): an explicit new
        # request clears the session's sticky workflow → re-select.
        try:
            self._maybe_clear_sticky(user_message)
        except Exception:
            pass
        # THE STICKY WORKFLOW (the CEO's 08-15 correction): a session's
        # selected workflow persists across turns. When the conversation
        # loop's session already has a sticky, pre-load it (no re-ask).
        try:
            from workflows.registry import sticky_workflow
            _sid = getattr(self, "session_id", "") or ""
            if _sid and self.workflow is None and not self.workflow_name:
                _sticky = sticky_workflow(_sid)
                if _sticky:
                    self.workflow_name = _sticky
        except Exception:
            pass
        _wf_name = self._ensure_workflow()
        _wf_doc = ""
        _wf_sections = ""
        _wf_requirements = ""
        try:
            from workflows.registry import workflow_doc, sections_text
            if self.workflow:
                _wf_doc = workflow_doc(self.workflow)
                _wf_sections = sections_text(self.workflow)
            _wf_requirements = self._workflow_requirements_prompt()
        except Exception:
            pass
        # Build the request: system prompt (if any) first, then history.
        request = []
        if getattr(self, "system_prompt", ""):
            request.append({"role": "system", "content": self.system_prompt})
        # THE PROMPT-FIRST SELECTION (the CEO's 08-15 correction): the
        # workflow list + the selection ask fold into the System section
        # of the FULL prompt — NO minimal START call. The model reads the
        # WHOLE 5-section stack (History summary + raw turns) and answers
        # with the workflow name at the start of its first response. When
        # a sticky workflow exists, its doc + requirements load instead.
        _wf_block = ""
        if self.workflow is None or not self.workflow_name:
            _sel_ask = self._workflow_selection_block()
            if _sel_ask:
                _wf_block = "\n\n" + _sel_ask
        elif _wf_doc or _wf_requirements:
            _wf_block = (
                f"\n\nWORKFLOW: {_wf_name}\n"
                f"{_wf_doc}\n"
                f"{_wf_requirements}")
        if _wf_block:
            if request and request[0]["role"] == "system":
                request[0] = {
                    "role": "system",
                    "content": request[0]["content"] + _wf_block,
                }
            else:
                request.insert(0, {"role": "system",
                                   "content": _wf_block.lstrip("\n")})
        request.extend(messages)
        request.append({"role": "user", "content": user_message})
        # DEBUG (temporary): the request the loop builds.
        try:
            from core.logging import log_event
            log_event(2, f"RUN: user={user_message[:40]!r} sys={bool(getattr(self,'system_prompt',''))} "
                         f"hist={len(messages)} req={len(request)}",
                      source="core", tool="message_loop", action="run_shape")
        except Exception:
            pass
        self._tool_transcript: list = []

        api_calls = 0
        tool_calls_made = 0
        exit_reason = "completed"
        # THE PRE-LOOP INIT (the 08-15 interrupt fix): the interrupted
        # return path references finish_reason/usage/reasoning — when the
        # interrupt fires BEFORE the first model call these were never
        # assigned (UnboundLocalError). Initialize them here.
        finish_reason = None
        usage = {}
        reasoning = None

        for api_calls in range(1, self.max_iterations + 1):
            if self._check_interrupt():
                exit_reason = "interrupted_by_user"
                break
            # THE WORKING-STATE EVENT (the Operator's 08-15 spec): each
            # iteration announces the agent is working — the GUI's
            # thinking block stays open/live while she's active.
            try:
                self._emit("state", "working", f"iteration {api_calls}")
            except Exception:
                pass

            # 1. Call the model. Tools are advertised so it CAN use them.
            model_messages = list(request)
            # THE SELECTION SUPPRESSION (the 08-15 fix): on iteration 1
            # with no workflow selected yet, the call IS the selection —
            # suppress its visible deltas until the parse decides.
            if api_calls == 1 and (self.workflow is None
                                   or not self.workflow_name):
                self._suppress_deltas = True
            try:
                response = self._call_model(model_messages)
            except Exception as exc:  # noqa: BLE001
                # THE LOOP-GUARD RECOVERY RESET (the Operator's 08-12 fix):
                # a provider error is a TRANSIENT failure — the model's
                # recovery reads (identical context reads) are legitimate,
                # not a runaway loop. Reset the guard counters so they
                # aren't blocked as "exact-failure/no-progress".
                try:
                    if self.loop_guardrails is not None:
                        self.loop_guardrails.reset_for_turn()
                except Exception:
                    pass
                # THE GRACEFUL 429 (the Operator's 08-12 fix): a
                # rate-limit surfaces as a clean, actionable message —
                # not a raw "[provider error: HTTP 429]".
                _reply = f"[provider error: {exc}]"
                try:
                    from providers.provider import ModelError, ProviderError
                    # THE GRACEFUL 429 (the Operator's 08-12 fix): a
                    # rate-limit surfaces as a clean, actionable message —
                    # not a raw "[provider error: HTTP 429]". Covers BOTH
                    # the single-model ModelError AND the chain's
                    # ProviderError (all providers exhausted by 429s), and
                    # matches BOTH the "rate-limited" wording AND the raw
                    # "HTTP Error 429" text.
                    if (isinstance(exc, (ModelError, ProviderError))
                            and ("rate-limited" in str(exc)
                                 or "HTTP Error 429" in str(exc)
                                 or "429" in str(exc))):
                        _reply = (f"I hit a provider rate limit (HTTP 429) — "
                                  f"too many requests in a short window. "
                                  f"Try again in a minute or so.")
                except Exception:
                    pass
                return TurnResult(
                    reply=_reply,
                    updated_history=request,
                    tool_calls_made=tool_calls_made,
                    api_calls=api_calls,
                    exit_reason="provider_error",
                    tool_transcript=list(self._tool_transcript),
                )

            content = response.get("content", "") or ""
            tool_calls = response.get("tool_calls") or []
            finish_reason = response.get("finish_reason")
            usage = response.get("usage") or {}
            reasoning = response.get("reasoning")

            # THE PROMPT-FIRST SELECTION PARSE (the CEO's 08-15 correction):
            # on the FIRST call, the model's response names the workflow
            # ("workflow: <name>") from the full-context System ask. Parse
            # it, load the workflow's doc + requirements, and append the
            # contract to the System prompt for the CONTINUE calls. The
            # workflow: <name> line is STRIPPED from the visible content.
            if api_calls == 1 and (self.workflow is None or not self.workflow_name):
                _pre_sel = self.workflow_name or ""
                _wf_name = self._apply_selection(content)
                # THE SELECTION SUPPRESSION (the 08-15 fix): the parse is
                # done — visible deltas resume from here (a selection that
                # also answered keeps its reply; a pure selection is
                # silent).
                self._suppress_deltas = False
                # THE GUIDED-WORKFLOW NOTE (the 08-15 spec): when the
                # selection named a non-existent workflow, append the note
                # to the System prompt so the model knows + can offer a
                # custom workflow.
                if getattr(self, "_selection_note", ""):
                    try:
                        if request and request[0]["role"] == "system":
                            request[0] = {
                                "role": "system",
                                "content": request[0]["content"]
                                + self._selection_note}
                        else:
                            request.insert(0, {"role": "system",
                                               "content": self._selection_note.lstrip("\n")})
                    except Exception:
                        pass
                    self._selection_note = ""
                if _wf_name:
                    # The name line is stripped from the content.
                    _content_lines = content.splitlines()
                    if _content_lines and _content_lines[0].strip().lower().startswith("workflow:"):
                        content = "\n".join(_content_lines[1:]).strip()
                    # Load the doc + requirements into the System section.
                    try:
                        from workflows.registry import workflow_doc, sections_text
                        _doc = workflow_doc(self.workflow or {}) if self.workflow else ""
                        _reqs = self._workflow_requirements_prompt()
                        if _doc or _reqs:
                            _blk = (f"\n\nWORKFLOW: {_wf_name}\n{_doc}\n{_reqs}")
                            if request and request[0]["role"] == "system":
                                request[0] = {
                                    "role": "system",
                                    "content": request[0]["content"] + _blk,
                                }
                            else:
                                request.insert(0, {"role": "system",
                                                   "content": _blk.lstrip("\n")})
                    except Exception:
                        pass
                    self._emit("workflow", _wf_name, "selected from full context")

            # THE XML TOOL-CALL PARSER (the Operator's 08-12 release fix):
            # some providers (opencode/zen reasoning models) emit tool
            # calls as XML TEXT inside the content instead of the OpenAI
            # JSON tool_calls field:
            #   <tool_calls><invoke name="terminal"><parameter name="cmd">
            #   ls</parameter></invoke></tool_calls>
            # The zen models ALSO wrap the tags in FULL-WIDTH pipes:
            #   <｜tool_calls｜><invoke name="terminal">...
            #   </｜tool_calls｜>   (U+FF5C — the DSML-style wrapper).
            # Normalize BOTH into the OpenAI shape the loop consumes —
            # otherwise the XML becomes the "reply" and Athena appears
            # unresponsive (the loop-guard saw it as read exact-failure).
            if (not tool_calls and content and
                    ("<tool_calls>" in content or "tool_calls" in content)):
                xml_calls = self._parse_xml_tool_calls(content)
                if xml_calls:
                    tool_calls = xml_calls
                    # THE METRICS (the 08-12 audit): the XML/DSML fallback
                    # fired — the model used the text format instead of
                    # the JSON tool_calls field. Log it so format drift
                    # is diagnosable.
                    try:
                        from core.logging import log_event
                        log_event(2, f"XML tool-call format parsed: "
                                     f"{len(xml_calls)} call(s)",
                                  source="core", tool="message_loop",
                                  action="xml_tool_call")
                    except Exception:
                        pass
                    # Keep the prose (if any) as the content; strip the
                    # XML block so it doesn't leak into the final reply.
                    content = self._strip_xml_block(content)

            # 2. No tool calls → this is the final response.
            if not tool_calls:
                # THE INTERRUPT WINS (the Operator's 08-12 spec): if the
                # operator's new message arrived while the model was
                # answering, Athena ACKNOWLEDGES the interruption instead
                # of finishing a stale response — the new message is what
                # she must understand now.
                if self._check_interrupt():
                    exit_reason = "interrupted_by_user"
                    break
                # THE EMPTY-REPLY GUARD (the Operator's 08-12 release
                # fix): a final response with EMPTY content (a provider
                # quirk on some prompts) is retried ONCE; a second empty
                # returns a clean notice instead of a blank bubble.
                if not (content or "").strip():
                    if not getattr(self, "_empty_retried", False):
                        self._empty_retried = True
                        continue
                    content = ("(no response produced — the model returned "
                               "empty output; try rephrasing the request)")
                final_text = content if isinstance(content, str) else json.dumps(content)
                # TURN FINALIZER (the Operator's spec): the clean end-of-turn
                # step — sanitize the reply for the archive + mark the
                # flow machine closed (Responding → Done → Idle).
                try:
                    from core.turn_finalizer import finalize_turn
                    final_text = finalize_turn(
                        getattr(self, "session_id", "") or "default",
                        final_text)
                except Exception:
                    pass
                request.append({"role": "assistant", "content": final_text})
                if reasoning:
                    request[-1]["reasoning_content"] = str(reasoning)
                # THE STOP PHASE (the Operator's 08-15 spec): mark the
                # workflow's requirements complete (the reply exists — the
                # CONTINUE contract's primary gate) + surface the workflow
                # for the caller (the GUI/CLI can show which lane ran).
                try:
                    from workflows.registry import requirements_of
                    if self.workflow:
                        for r in requirements_of(self.workflow):
                            r["completed"] = True
                except Exception:
                    pass
                self._emit("workflow", self.workflow_name or "conversation",
                           "stop: responded to the operator")
                return TurnResult(
                    reply=final_text,
                    updated_history=request,
                    tool_calls_made=tool_calls_made,
                    api_calls=api_calls,
                    exit_reason=exit_reason,
                    tool_transcript=list(self._tool_transcript),
                    finish_reason=finish_reason,
                    usage=usage,
                    reasoning=reasoning,
                )

            # 3. Tool calls → execute them (through the channel gate),
            #    append results, continue.
            # THE REASONING_CONTENT REPLAY (the 08-14 zen-400 fix,
            # mirroring the streaming refs #15250/#17400/#17341): DeepSeek v4
            # thinking mode requires reasoning_content on EVERY assistant
            # tool-call message replayed to the API — without it the
            # relay returns HTTP 400 ("The reasoning_content in the
            # thinking mode must be passed back to the API"). V4 Pro also
            # rejects EMPTY strings, so pad with a single space when no
            # reasoning was captured (satisfies non-empty checks without
            # fabricating a chain of thought).
            assistant_msg = {"role": "assistant", "content": content,
                             "tool_calls": tool_calls}
            if reasoning:
                assistant_msg["reasoning_content"] = str(reasoning)
            elif tool_calls:
                assistant_msg["reasoning_content"] = " "
            request.append(assistant_msg)
            for tc in tool_calls:
                tool_call_id = tc.get("id", "")
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                # THE TOOL GATE: default deny — a role only uses tools its
                # channel allows. Diverted calls return a clean denial.
                # The REAL arguments ride into the approval prompt.
                try:
                    _gate_args = json.loads(raw_args) if raw_args else {}
                except Exception:
                    _gate_args = {}
                if not self._tool_allowed(tool_name, _gate_args):
                    # THE GUIDED-DENIAL (the Operator's 08-15 spec): when
                    # the model calls a tool that does NOT EXIST (a
                    # hallucination — e.g. "weather"), the denial must tell
                    # it so + guide it to the REAL tools that CAN do the
                    # job + offer to create a custom tool. A tool that
                    # EXISTS but is channel-blocked gets the plain denial.
                    _exists = False
                    try:
                        from filesystem.tools import canonical_names
                        _exists = tool_name in canonical_names()
                    except Exception:
                        _exists = False
                    if _exists:
                        result = (
                            f"[denied: tool '{tool_name}' is not allowed on "
                            f"the '{getattr(self.channel, 'name', '?')}' channel]"
                        )
                        self._emit("tool", f"{tool_name} [denied on {getattr(self.channel, 'name', '?')}]")
                    else:
                        result = self._guided_tool_denial(tool_name)
                        self._emit("tool", f"{tool_name} [no such tool — guided]")
                else:
                    # GUARDRAILS (the Operator's safety spec): validate the
                    # call's INTENT before executing — hard rejections
                    # refuse outright, holds require the interactive
                    # surface (the same approval path), passes continue.
                    try:
                        import json as _json
                        try:
                            g_args = _json.loads(raw_args) if raw_args else {}
                        except Exception:
                            g_args = {}
                        # LOOP GUARDRAILS (adapted): block a
                        # runaway call BEFORE it runs (loop caps + exact-
                        # failure + no-progress). A block returns a clean
                        # synthetic result the model can read.
                        _loop_block = None
                        try:
                            if self.loop_guardrails is not None:
                                _loop_block = self.loop_guardrails.before_call(
                                    tool_name, g_args)
                        except Exception:
                            _loop_block = None
                        if _loop_block is not None:
                            result = self.loop_guardrails.synthetic_result(
                                _loop_block) if self.loop_guardrails else \
                                f"[guardrail: {_loop_block.get('message', 'blocked')}]"
                            self._emit("tool", f"{tool_name} [{_loop_block.get('code', 'blocked')}]")
                        else:
                            from security.guardrails import check as guard_check
                            g = guard_check("tool", tool_name, g_args)
                            if g["verdict"] == "reject":
                                result = (f"[guardrail rejected: {tool_name} — "
                                          f"{g.get('reason', 'violates safety rules')}]")
                                self._emit("tool", f"{tool_name} [guardrail rejected]")
                            elif g["verdict"] == "hold" and self.on_approval is not None:
                                # A HOLD needs the user — UNLESS a persisted
                                # rule already covers this tool (session/global
                                # allow from an earlier call must STICK — the
                                # Operator's no-re-prompt fix). Check permissions
                                # first: if a rule exists, defer to it.
                                _decided = False
                                try:
                                    from security.permissions import check as perm_check
                                    _p = perm_check(tool_name, g_args)
                                    if not _p.get("needs_prompt", True):
                                        # A rule already decided this tool —
                                        # honour it (allow or deny) without
                                        # prompting again.
                                        if _p.get("allowed"):
                                            self._emit("tool", f"{tool_name} {raw_args[:120]}")
                                            result = tool_registry.execute_tool_call(
                                                tc, profile=_resolve_profile_name(self))
                                        else:
                                            result = (f"[denied: {tool_name} — "
                                                      f"permission rule]")
                                            self._emit("tool", f"{tool_name} [denied by rule]")
                                        _decided = True
                                except Exception:
                                    pass
                                if not _decided:
                                    # A HOLD needs the user — reuse the approval path.
                                    verdict, scope = self.on_approval(
                                        tool_name, g_args, "guardrail-hold")
                                    # PERSIST the decision (the Operator's fix): a
                                    # session/global allow must STICK — without
                                    # decide(), every later call re-prompts.
                                    try:
                                        from security.permissions import decide
                                        if verdict in ("allow", "deny", "block"):
                                            decide(tool_name, verdict, scope)
                                    except Exception:
                                        pass
                                    if verdict != "allow":
                                        result = (f"[denied: {tool_name} — "
                                                  f"guardrail hold refused]")
                                        self._emit("tool", f"{tool_name} [guardrail denied]")
                                    else:
                                        self._emit("tool", f"{tool_name} {raw_args[:120]}")
                                        result = tool_registry.execute_tool_call(
                                        tc, profile=_resolve_profile_name(self))
                            else:
                                self._emit("tool", f"{tool_name} {raw_args[:120]}")
                                result = tool_registry.execute_tool_call(
                                        tc, profile=_resolve_profile_name(self))
                    except Exception:
                        self._emit("tool", f"{tool_name} {raw_args[:120]}")
                        result = tool_registry.execute_tool_call(
                                        tc, profile=_resolve_profile_name(self))
                # EVENTS: log every tool call to the agent activity log
                # (levels 1-2 ONLY — the curator's learn-by-doing record;
                # the nurse never watches events, only metrics 3/4/5).
                try:
                    from metrics.events import log_event
                    agent = getattr(self.channel, "name", "default") or "default"
                    ok = not result.startswith("error:") and "[denied:" not in result
                    log_event(1 if ok else 2,
                              agent=agent, tool=tool_name,
                              action="tool_call", target=tool_name,
                              result=(result[:120] if ok else f"error/denied: {result[:120]}"))
                except Exception:
                    pass  # event logging never breaks the loop
                # STREAM COMPLETION (the Operator's 08-12 spec,
                # adapted): the live observer gets the RESULT too — the
                # GUI appends "→ result" to the call's row as it happens
                # (the tool.completed convention). Never raises.
                try:
                    self._emit("tool.result", f"{tool_name} {raw_args[:120]}",
                               str(result)[:200])
                except Exception:
                    pass
                # SECURITY: tool output is UNTRUSTED — marked before it
                # re-enters the model, so instructions inside it are data.
                from security.security import sanitize_tool_result
                untrusted_result = sanitize_tool_result(result)
                # LOOP GUARDRAILS (adapted): record the outcome —
                # failures feed the exact/same-tool counters; idempotent
                # successes feed no-progress. A HALT stops the turn's tool
                # phase (circuit breaker).
                try:
                    if self.loop_guardrails is not None:
                        _lg = self.loop_guardrails.after_call(
                            tool_name, g_args, result)
                        if _lg is not None and _lg.get("action") == "halt":
                            if self.loop_guardrails.halt_reason:
                                untrusted_result = sanitize_tool_result(
                                    self.loop_guardrails.halt_reason)
                except Exception:
                    pass
                # RESULT CLASSIFICATION (the Operator's spec): the MODEL sees
                # the SIMPLE classified output ("ok: file written"), the
                # vault keeps the raw. The system handles the complexity.
                # NOTE: classify the RAW result — classifying the
                # sanitized wrapper would make the model see the
                # "[UNTRUSTED CONTENT START..." marker line instead of
                # the actual output (the tool-output bug the Operator hit:
                # every probe returned "ok:" with zero payload).
                try:
                    from core.result_classifier import present
                    model_view = present(result, kind="tool",
                                         tool_name=tool_name)
                except Exception:
                    model_view = untrusted_result
                self._tool_transcript.append({
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "arguments": raw_args,
                    "result": result,
                    "allowed": self._tool_allowed(tool_name),
                })
                request.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": model_view,
                })
                tool_calls_made += 1

            # 4. Loop continues (model gets tool results next iteration).

        # Interrupted by the operator's new message (the interrupt flag
        # from the GUI) — the turn stops so the new message can process.
        # The reply ACKNOWLEDGES the interruption (the Operator's 08-12
        # spec): Athena says she's stopping to read the new message.
        if exit_reason == "interrupted_by_user":
            return TurnResult(
                reply=("Interruption Understood — I'll look at your new "
                       "message to understand the request more clearly..."),
                updated_history=request,
                tool_calls_made=tool_calls_made,
                api_calls=api_calls,
                exit_reason="interrupted_by_user",
                finish_reason=finish_reason,
                usage=usage,
            )

        # Budget exhausted (never hit a final response).
        return TurnResult(
            reply="[iteration budget exhausted — no final response]",
            updated_history=request,
            tool_calls_made=tool_calls_made,
            api_calls=api_calls,
            exit_reason="budget_exhausted",
            finish_reason=finish_reason,
            usage=usage,
        )

    def _advertised_schemas(self) -> list | None:
        """The model's function list: the CHANNEL's ALLOWED TOOLS + SKILLS
        (the 08-14 fix — NOT the full 96-tool registry). The full payload
        (~42KB) tripped the provider's WAF and degraded replies to terse
        "ok"/"checked". The channel's .tools/.skills hold names or "*" —
        resolve skills to Skill objects, keep tools by name."""
        try:
            from filesystem.tools import schemas_for_channel
            if self.channel is None:
                return schemas_for_channel(None, None)
            allowed_tools = self.channel.tools or []
            allowed = self.channel.skills or []
            if not allowed:
                return schemas_for_channel(allowed_tools, None)
            if allowed == ["*"]:
                from intelligence.skills import load_skills
                return schemas_for_channel(allowed_tools, load_skills())
            from intelligence.skills import load_skills
            all_skills = {s.name.lower(): s for s in load_skills()}
            wanted = [all_skills.get(str(n).lower())
                      for n in allowed]
            return schemas_for_channel(
                allowed_tools, [s for s in wanted if s is not None])
        except Exception:
            return None

    def _parse_xml_tool_calls(self, content: str) -> list:
        """Parse XML-format tool calls from content (the 08-12 release
        fix): some reasoning providers emit
            <tool_calls><invoke name="terminal"><parameter name="cmd">
            ls -la</parameter></invoke></tool_calls>
        Returns the OpenAI shape [{id, type, function:{name, arguments}}].
        Empty when no valid <invoke> blocks exist.
        """
        import re as _re
        # THE FULL-WIDTH + DSML NORMALIZATION (the 08-12 zen-fix): the
        # zen models wrap tags in FULL-WIDTH pipes (U+FF5C ｜) AND insert
        # |DSML| markers inside every tag — sometimes with DOUBLE pipes:
        #   <||DSML||tool_calls||DSML||><||DSML||invoke name="terminal"||DSML||>
        # Normalize all of it so the plain-XML parse matches identically.
        content = content.replace("\uff5c", "|")
        content = _re.sub(r"\|+\s*DSML\s*\|+", "", content)
        # THE REMAINING TAG-FLANK PIPES (the 08-12 zen-fix): the zen
        # wrapper leaves single pipes around the tag names —
        # <|invoke name="weather"|> — strip pipes that flank a tag
        # boundary (<| → <, |> → >) so the plain-XML parse matches.
        content = _re.sub(r"<\s*\|+", "<", content)
        content = _re.sub(r"\|+\s*>", ">", content)
        content = content.replace("||", "|")
        calls = []
        # One <invoke name="...">...</invoke> per tool call.
        invokes = _re.findall(
            r"<invoke\s+name=\"([^\"]+)\"[^>]*>(.*?)</invoke>",
            content, _re.S | _re.I)
        for i, (name, body) in enumerate(invokes):
            name = name.strip()
            if not name:
                continue
            # <parameter name="cmd">ls</parameter> → {"cmd": "ls"}
            params = _re.findall(
                r'<parameter\s+name="([^"]+)"[^>]*>(.*?)</parameter>',
                body, _re.S | _re.I)
            args = {}
            for pname, pval in params:
                pval = pval.strip()
                # Try JSON; fall back to a bare string.
                try:
                    args[pname.strip()] = json.loads(pval)
                except Exception:
                    args[pname.strip()] = pval
            # THE cmd → command ALIAS (the 08-12 release fix): XML-emitting
            # models use <parameter name="cmd"> but the tool schema says
            # "command" — normalize so the platform wrapper accepts it.
            if "cmd" in args and "command" not in args:
                args["command"] = args.pop("cmd")
            calls.append({
                "id": f"call_xml_{i}",
                "type": "function",
                "function": {"name": name,
                             "arguments": json.dumps(args)},
            })
        return calls

    def _strip_xml_block(self, content: str) -> str:
        """Remove the <tool_calls>...</tool_calls> block from content,
        keeping any prose before/after it."""
        import re as _re
        # Tolerant of the full-width pipe wrapper + |DSML| markers
        # (including the doubled-pipe variant).
        content = content.replace("\uff5c", "|")
        content = _re.sub(r"\|+\s*DSML\s*\|+", "", content)
        content = _re.sub(r"<\s*\|+", "<", content)
        content = _re.sub(r"\|+\s*>", ">", content)
        content = content.replace("||", "|")
        return _re.sub(r"<tool_calls>.*?</tool_calls>", "",
                       content or "", flags=_re.S | _re.I).strip()

    def _call_model_streaming(self, provider, model: str, url: str,
                              api_key: str, messages: list) -> dict:
        """The STREAMING provider call (the Operator's 08-12 spec,
        streaming-adapted): stream:true, read the SSE body line-by-line,
        forward every text delta via on_event("delta", text). Returns
        the same shape as the blocking call — the loop is unchanged.
        """
        import urllib.request
        import socket as _ssock  # THE 08-17 DEAD-PROVIDER GUARD: socket.timeout
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Athena/0.1",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        # THE DEEPSEEK THINKING FIELD (the 08-14 zen-400 fix): the
        # opencode relay 400s deepseek-v* models without an explicit
        # thinking control. Disable the relay's thinking (Athena renders
        # reasoning_content itself) — the same streaming shape.
        _flat = (model or "").strip().lower()
        if _flat.startswith("deepseek-v") and not _flat.startswith("deepseek-v3"):
            body["thinking"] = {"type": "disabled"}
        # THE TOOLS-ONLY-WHEN-PRESENT FIX (the 08-14 zen-400 fix): an
        # empty/null tools list with tool_choice:"auto" is INVALID on
        # opencode-style APIs — it returns HTTP 400. Only send the tools
        # section when there ARE schemas; otherwise the body is the
        # minimal chat payload the API always accepts.
        _tools = self._advertised_schemas() or []
        if _tools:
            body["tools"] = _tools
            body["tool_choice"] = "auto"
        # DEBUG (temporary): log the request shape for the terse-reply hunt.
        try:
            import json as _json
            from core.logging import log_event
            log_event(2, f"REQ: model={model} msgs={len(messages)} "
                         f"tools={len(body.get('tools') or [])} "
                         f"sys_len={len(str(messages[0].get('content','')) if messages else '')} "
                         f"body_len={len(_json.dumps(body))}",
                      source="core", tool="message_loop", action="req_shape")
        except Exception:
            pass
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        finish_reason = None
        usage: dict = {}
        # STREAMED TOOL CALLS (the Operator's 08-12 fix): OpenAI-style
        # streams deliver tool calls as fragmented delta.tool_calls chunks
        # — {index, id, function:{name, arguments}} — spread across many
        # chunks (id first, then name, then argument text piece by piece).
        # Accumulate by index and assemble at the end so the loop's
        # re-injection machinery sees the calls (the pre-fix code dropped
        # them entirely → tools never ran + the reply was empty).
        tool_call_fragments: dict[int, dict] = {}
        try:
            resp = urllib.request.urlopen(req, timeout=30.0)
        except urllib.error.HTTPError as exc:
            # THE 400-BODY CAPTURE (the 08-14 diagnostic fix): surface
            # the relay's exact rejection reason (the streaming path
            # previously raised a bare HTTPError the GUI showed as
            # "400 Bad Request" with no explanation).
            try:
                _body = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                _body = ""
            from core.logging import log_event
            log_event(3, f"stream HTTP {exc.code} ({url}): {_body[:200]}",
                      source="providers", tool="provider", action="http_error")
            raise ProviderError(f"HTTP {exc.code}: {_body}") from exc
        except (_ssock.timeout, TimeoutError, OSError) as exc:
            # THE 08-17 DEAD-PROVIDER GUARD (the crash fix): the connect
            # OR the first read stalled past the urlopen timeout — surface
            # it as a friendly provider error, never a frozen turn.
            from core.logging import log_event
            log_event(3, f"stream timeout ({url}): provider unresponsive",
                      source="providers", tool="provider",
                      action="stream_timeout")
            raise ProviderError("HTTP stream timed out: the provider did not "
                                "respond") from exc
        with resp:
            # THE 08-17 HARD STREAM DEADLINE (the crash fix): the connect
            # timeout only bounds the TCP handshake — the SSE BODY read can
            # block indefinitely when the provider accepts then stalls
            # (the observed hang: a turn stuck minutes + a service killed).
            # A deadline bounds the WHOLE read; a stall surfaces as a
            # friendly provider error, never a frozen turn.
            import time as _time
            _deadline = _time.monotonic() + 90.0
            for raw_line in resp:
                if _time.monotonic() > _deadline:
                    from core.logging import log_event
                    log_event(3, f"stream timeout ({url}): no data within 90s",
                              source="providers", tool="provider",
                              action="stream_timeout")
                    raise ProviderError("HTTP stream timed out: the provider "
                                        "stalled (no data within 90s)")
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except Exception:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    content_parts.append(text)
                    # THE SELECTION SUPPRESSION (the 08-15 fix): while the
                    # workflow-selection call streams, its deltas are NOT
                    # forwarded — the selection is internal machinery and
                    # must not render as a visible reply.
                    if not getattr(self, "_suppress_deltas", False):
                        try:
                            self._emit("delta", text)
                        except Exception:
                            pass
                # The streamed tool-call fragments (id/name/arguments).
                tc_deltas = delta.get("tool_calls")
                if tc_deltas:
                    for tcd in tc_deltas:
                        try:
                            idx = int(tcd.get("index", 0))
                        except (TypeError, ValueError):
                            idx = 0
                        frag = tool_call_fragments.setdefault(idx, {})
                        if tcd.get("id"):
                            frag["id"] = tcd["id"]
                        fn_part = tcd.get("function") or {}
                        if fn_part.get("name"):
                            frag.setdefault("name", "")
                            frag["name"] += fn_part["name"]
                        if fn_part.get("arguments"):
                            frag.setdefault("arguments", "")
                            frag["arguments"] += fn_part["arguments"]
                reasoning = (delta.get("reasoning_content")
                             or delta.get("reasoning") or "")
                if reasoning:
                    reasoning_parts.append(reasoning)
                    # REASONING MODELS (DeepSeek R1-style): the thinking
                    # chain streams FIRST with content=null. Emit it as a
                    # "reason" delta so the GUI can show the live chain.
                    # (Suppressed during the workflow-selection call too.)
                    if not getattr(self, "_suppress_deltas", False):
                        try:
                            self._emit("reason", reasoning)
                        except Exception:
                            pass
                if choice.get("finish_reason"):
                    finish_reason = choice.get("finish_reason")
                if chunk.get("usage"):
                    usage = chunk.get("usage") or {}
        # ASSEMBLE the streamed tool calls (sorted by index): the loop's
        # re-injection machinery consumes {id, type, function:{name,
        # arguments}} — the same shape as the blocking path returns.
        assembled = []
        for idx in sorted(tool_call_fragments):
            frag = tool_call_fragments[idx]
            if not frag.get("name"):
                continue  # a fragment without a name is debris
            assembled.append({
                "id": frag.get("id", f"call_{idx}"),
                "type": "function",
                "function": {
                    "name": frag["name"],
                    "arguments": frag.get("arguments", "{}"),
                },
            })
        return {
            "content": "".join(content_parts),
            "tool_calls": assembled or None,
            "finish_reason": finish_reason,
            "usage": usage,
            "reasoning": "".join(reasoning_parts).strip() or None,
        }

    def _request_for_provider(self, messages: list, is_anthropic: bool) -> list:
        """Convert the canonical history to the provider's request format.

        The canonical history is the OpenAI shape ({role, content,
        tool_calls, tool_call_id}). Anthropic native needs:
            • assistant tool_calls → content blocks {type:"tool_use",
              id, name, input}
            • tool results → user content blocks {type:"tool_result",
              tool_use_id, content}
        Everything else passes through unchanged.
        """
        if not is_anthropic:
            return list(messages)
        out = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content")
            if role == "assistant" and m.get("tool_calls"):
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    try:
                        input_args = json.loads(fn.get("arguments", "{}"))
                    except (TypeError, ValueError):
                        input_args = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": input_args,
                    })
                out.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": content or "",
                    }],
                })
            else:
                out.append(dict(m))
        return out

    def _call_model(self, messages: list) -> dict:
        """Call the provider chain; return {content, tool_calls, finish_reason, usage}.

        Supports the MAJOR chat formats (the Operator's spec):
          • OpenAI / LM Studio / Qwen / DeepSeek / vLLM / Ollama —
            the OpenAI-compatible /chat/completions shape:
                choices[0].message.{content, tool_calls}
                choices[0].finish_reason
                usage.{prompt_tokens, completion_tokens, total_tokens}
          • Anthropic native — /v1/messages (or /messages) shape:
                content = [ {type:"text", text} | {type:"tool_use", name, input} ]
                stop_reason (end_turn|max_tokens|stop_sequence|tool_use)
                usage.{input_tokens, output_tokens}
        Normalizes EVERYTHING to the OpenAI shape before returning.
        """
        # __new__-built loops (some doctor tests) skip __init__ — the
        # streaming flag falls back to the config default.
        if not hasattr(self, "streaming"):
            try:
                from core.config import load_config
                self.streaming = bool(load_config().get("provider", {}).get("streaming", True))
            except Exception:
                self.streaming = True
        # Advertise tools so the model can request them.
        payload_messages = list(messages)
        from providers.provider import _post_json, ProviderError

        # RETRY (the Operator's turn-retry spec): a transient post failure gets
        # ONE retry before the fallback — most hiccups pass on retry.
        def _post_with_retry(provider, model, *args, **kwargs):
            try:
                return _post_json(*args, **kwargs)
            except Exception as exc:
                turn_id = getattr(self, "_turn_id", "")
                if turn_id:
                    try:
                        from core.turn_retry import should_retry
                        if should_retry(turn_id, provider.name,
                                        model, str(exc)):
                            return _post_json(*args, **kwargs)
                    except Exception:
                        pass
                raise

        # WALK THE WHOLE CHAIN — primary first, fallback next (the Operator's
        # contract). ready_provider() alone bypassed the ladder: a 401 on
        # the primary never reached the fallback. The walk follows the
        # SELECTION LADDER (model_ladder: primary model → fallback
        # model → extras), so the configured fallback model is used —
        # not just the provider's first catalogued model.
        from providers.selection import model_ladder
        steps = []
        try:
            cfg = getattr(self, "cfg", None)
            steps = model_ladder("reason", cfg) if cfg else []
        except Exception:
            steps = []
        pairs = []  # [(provider, model)] in exact try order
        by_name = {p.name: p for p in self.providers.providers}
        for step in steps:
            p = by_name.get(step.get("provider"))
            if p is None or not p.ready or not p.models:
                continue
            m = step.get("model")
            if m not in (p.models or []):
                continue
            pairs.append((p, m))
        # No selection ladder resolved → walk ready providers in order.
        if not pairs:
            for p in self.providers.providers:
                if p.ready and p.models:
                    pairs.append((p, p.models[0]))

        last_error: Optional[Exception] = None
        for provider, model in pairs:
            base = str(provider.base_url).rstrip("/")
            api_key = provider.api_key

            # ANTHROPIC native: the /v1/messages endpoint (content is a
            # BLOCK ARRAY, stop_reason not finish_reason, usage in/out).
            try:
                if "/v1/messages" in base or base.endswith("/messages"):
                    url = f"{base}/v1/messages" if not base.endswith("/v1/messages") else base
                    body = {
                        "model": model,
                        "messages": self._request_for_provider(payload_messages,
                                                               is_anthropic=True),
                        "max_tokens": self.max_tokens or 1024,
                    }
                    # THE TOOLS-ONLY-WHEN-PRESENT FIX (the 08-14 zen-400
                    # fix): never send tools:null — the API 400s.
                    _t = self._advertised_schemas() or []
                    if _t:
                        body["tools"] = _t
                    data = _post_with_retry(provider, model, url, api_key, body)
                    blocks = data.get("content", []) or []
                    text = "".join(b.get("text", "") for b in blocks
                                   if isinstance(b, dict) and b.get("type") == "text")
                    tool_calls = []
                    for b in blocks:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            tool_calls.append({
                                "id": b.get("id"),
                                "type": "function",
                                "function": {
                                    "name": b.get("name"),
                                    "arguments": json.dumps(b.get("input", {})),
                                },
                            })
                    usage = data.get("usage", {}) or {}
                    # Anthropic's reasoning chain lives in `thinking` blocks.
                    reasoning = "".join(
                        b.get("thinking", "") for b in blocks
                        if isinstance(b, dict) and b.get("type") == "thinking"
                    ).strip() or None
                    return {
                        "content": text,
                        "tool_calls": tool_calls or None,
                        "finish_reason": data.get("stop_reason"),
                        "usage": {
                            "prompt_tokens": usage.get("input_tokens"),
                            "completion_tokens": usage.get("output_tokens"),
                            "total_tokens": (usage.get("input_tokens", 0) or 0)
                                            + (usage.get("output_tokens", 0) or 0),
                        },
                        "reasoning": reasoning,
                    }

                # OPENAI-COMPATIBLE: OpenAI, LM Studio, Qwen, DeepSeek,
                # vLLM, Ollama, Together, Groq, ... — /chat/completions.
                url = f"{base}/chat/completions"
                if self.streaming:
                    # THE STREAMING CALL (the Operator's 08-12 spec,
                    # streaming-adapted): stream:true + read the SSE body
                    # line-by-line. Each `data: {...}` chunk carries
                    # choices[0].delta.content — forwarded through
                    # on_event("delta", text) so the GUI types the reply
                    # LIVE. Handles the Anthropic-ish blocks when present.
                    return self._call_model_streaming(
                        provider, model, url, api_key, payload_messages)
                body = {
                    "model": model,
                    "messages": payload_messages,
                    "stream": False,
                }
                # THE DEEPSEEK THINKING FIELD (the 08-14 zen-400 fix).
                _flat = (model or "").strip().lower()
                if _flat.startswith("deepseek-v") and not _flat.startswith("deepseek-v3"):
                    body["thinking"] = {"type": "disabled"}
                # THE TOOLS-ONLY-WHEN-PRESENT FIX (the 08-14 zen-400
                # fix): never send tools:null + tool_choice — 400.
                _t = self._advertised_schemas() or []
                if _t:
                    body["tools"] = _t
                    body["tool_choice"] = "auto"
                if self.max_tokens:
                    body["max_tokens"] = self.max_tokens
                data = _post_with_retry(provider, model, url, api_key, body)
                choice = data["choices"][0]
                msg = choice.get("message", {})
                # The reasoning chain: DeepSeek sends reasoning_content;
                # other reasoning models may send `reasoning` — capture
                # it as the "how the response was crafted" string.
                reasoning = (msg.get("reasoning_content")
                             or msg.get("reasoning") or "").strip() or None
                return {
                    "content": msg.get("content"),
                    "tool_calls": msg.get("tool_calls"),
                    "finish_reason": choice.get("finish_reason"),
                    "usage": data.get("usage") or {},
                    "reasoning": reasoning,
                }
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                try:
                    self._emit("provider", f"{provider.name} failed ({exc}) — "
                                           "next provider")
                except Exception:
                    pass
                continue
        if last_error is not None:
            raise ProviderError(f"{provider.name}: {last_error}")
        raise RuntimeError("no ready provider")

"""The Runtime — what runs when something reaches the Message Loop.

The server is online 24/7; the runtime is what executes a turn when an
event arrives. This is the bridge between the Server Loop (forever) and the
Message Loop (bounded, per turn).

This is the minimal live Message Loop: build a system prompt from the
identity files (ASSISTANT.md + USER.md), send the event to the provider,
return the reply. The full bounded loop (tool calls, iteration caps, lean
window + summary) is documented in the project wiki.
"""
from __future__ import annotations

import json
import queue
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .config import ATHENA_ROOT, load_config
from providers.provider import ProviderChain


def _read_identity(rel: str) -> str:
    """Read an identity file from the profile root (empty if missing)."""
    path = ATHENA_ROOT / rel
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


class Runtime:
    """Event queue + gate state that the Server Loop reads each tick."""

    def __init__(self, config: Optional[dict] = None):
        self._pending: "queue.Queue[dict]" = queue.Queue()
        self._signal = False
        self._signal_priority = 0.0
        # THE THOUGHT QUEUE (the 08-15 kanban-feeder fix): the scheduler
        # feeds autonomous thoughts here (handle_thought). The server
        # loop's think gate pops the highest-priority one when the budget
        # allows. heapq is a min-heap; priorities are negated so HIGHER
        # priority pops first (the ConversationLoop contract).
        import heapq as _heapq
        self._thought_heap: list = []
        self._thought_seq = 0
        self.responses: list[dict] = []
        self.cfg = config or load_config()
        self.providers = ProviderChain(self.cfg)
        # Identity files — the system prompt bookends (System-Prompt.md).
        self.assistant_identity = _read_identity("assistant/ASSISTANT.md")
        self.user_identity = _read_identity("user/USER.md")

    # -- Server Loop gate interface -----------------------------------
    def has_pending(self) -> bool:
        return not self._pending.empty()

    def has_signal(self) -> bool:
        return self._signal

    def signal_priority(self) -> float:
        return self._signal_priority

    def handle_thought(self, content: str, priority: float = 0.5) -> dict:
        """Queue an autonomous thought (the 08-15 kanban-feeder fix).

        The scheduler calls this for board tasks + scheduled LLM jobs.
        Mirrors ConversationLoop.handle_thought: the thought lands in the
        priority heap, and the server loop's think gate fires _do_think
        when the budget allows.
        """
        self._thought_seq += 1
        import heapq as _heapq
        _heapq.heappush(self._thought_heap,
                        (-priority, self._thought_seq, str(content)))
        self._signal = True
        self._signal_priority = max(self._signal_priority, priority)
        return {"ok": True, "queued": content}

    def fire(self, kind: str, payload: Any = None) -> None:
        """Called by the Server Loop when a gate opens."""
        if kind == "message":
            self._drain_messages()
        elif kind == "think":
            self._signal = False
            self._do_think()

    # -- External door (Platform / CLI) --------------------------------
    def handle_event(self, event: dict) -> dict:
        """A door delivers an event here. Queues it; returns an ack."""
        event.setdefault("id", str(uuid.uuid4()))
        event.setdefault("ts", time.time())
        self._pending.put(event)
        return {"ok": True, "event_id": event["id"]}

    # -- Internals ------------------------------------------------------
    def _build_system_prompt(self) -> str:
        """The identity bookends: ASSISTANT.md first, USER.md last."""
        parts = []
        if self.assistant_identity:
            parts.append(self.assistant_identity)
        if self.user_identity:
            parts.append(self.user_identity)
        return "\n\n".join(parts)

    def _drain_messages(self) -> None:
        from . import db as db_layer
        from .message_loop import MessageLoop

        max_iter = int(self.cfg.get("message_loop", {}).get("max_iterations", 500))
        while not self._pending.empty():
            event = self._pending.get_nowait()
            content = str(event.get("content", ""))
            session_id = str(event.get("session_id", "") or "")
            try:
                # READ the session's past before the turn (the broad layer:
                # the loop consumes history so the model has memory).
                history = []
                if session_id:
                    history = db_layer.get_session_history(session_id)
                loop = MessageLoop(
                    providers=self.providers,
                    system_prompt=self._build_system_prompt(),
                    max_iterations=max_iter,
                )
                result = loop.run_turn(content, history=history)
                reply = result.reply
                # Persist: the exchange lands in the session file + the archive.
                if session_id:
                    # NAMES come from the identity files (the Operator's spec).
                    try:
                        from core.identity import agent_identity, user_identity
                        ai = agent_identity()
                        ui = user_identity()
                        a_first = (ai.get("name_first") or ai.get("first_name") or "").strip()
                        a_nick = (ai.get("name_nick") or ai.get("nickname") or "").strip() or a_first
                        u_first = (ui.get("name_first") or ui.get("first_name") or "").strip()
                        u_last = (ui.get("name_last") or ui.get("last_name") or "").strip()
                        u_nick = (ui.get("name_nick") or ui.get("nickname") or "").strip() or u_first
                    except Exception:
                        a_first = a_nick = u_first = u_last = u_nick = ""
                    db_layer.record_session_message(
                        session_id, "user", content,
                        name_first=u_first or None, name_last=u_last or None,
                        name_nick=u_nick or None)
                    db_layer.record_session_message(
                        session_id, "assistant", reply,
                        name_first=a_first or None, name_nick=a_nick or None)
                    # The tools used in THIS turn (native chat format).
                    tool_names = [t.get("tool_name", "") for t in result.tool_transcript]
                    db_layer.record_vault_entry(
                        "message", content, role="User",
                        source="cli",
                        name_first=u_first or None, name_last=u_last or None,
                        name_nick=u_nick or None,
                    )
                    db_layer.record_vault_entry(
                        "message", reply, role="Assistant",
                        source="cli",
                        name_first=a_first or None, name_nick=a_nick or None,
                        tool=json.dumps(tool_names) if tool_names else None,
                        # reason = the reasoning chain; reason_stop = WHY
                        # it stopped (exit_reason, not the raw "stop").
                        reason=getattr(result, "reasoning", None) or None,
                        reason_stop=getattr(result, "exit_reason", None)
                        or "completed",
                    )
                    # Each tool call+result is its own archive entry, linked
                    # by tool_id — the timeline stays 1:1.
                    for t in result.tool_transcript:
                        db_layer.record_vault_entry(
                            "tool", t.get("result", ""),
                            # The Operator's role vocabulary: a tool execution
                            # is a SYSTEM action.
                            role="System", source="cli",
                            # The Operator's tool columns: tool = NAME,
                            # tool_call = ARGUMENTS string, tool_id = ID.
                            tool=(t.get("tool_name") or "").strip() or None,
                            tool_call=t.get("arguments") or None,
                            tool_id=t.get("tool_call_id") or None,
                        )
            except Exception as exc:  # noqa: BLE001
                from core.logging import log_event
                log_event(4, f"message loop error: {exc}", source="runtime",
                          action="handle_event")
                reply = f"[message loop error: {exc}]"
            self.responses.append(
                {
                    "event_id": event.get("id"),
                    "session_id": session_id,
                    "reply": reply,
                }
            )

    def _do_think(self) -> None:
        """THE AUTONOMOUS THOUGHT (the 08-15 kanban-feeder fix): pop the
        highest-priority queued thought (handle_thought) and run it through
        a MessageLoop turn. The reply lands in responses (the caller reads
        it); failures surface as a log + a notice — never a crash."""
        import heapq as _heapq
        try:
            if not self._thought_heap:
                self._signal = False
                self._signal_priority = 0.0
                return
            _prio, _seq, content = _heapq.heappop(self._thought_heap)
            self._signal = bool(self._thought_heap)
            self._signal_priority = (
                max(-p for p, _, _ in self._thought_heap) if self._thought_heap else 0.0)
            from core.message_loop import MessageLoop
            max_iter = int(self.cfg.get("budget", {}).get("message_loop", {})
                           .get("max_iterations", 500)) or 500
            loop = MessageLoop(
                providers=self.providers,
                system_prompt=self._build_system_prompt(),
                max_iterations=max_iter,
                streaming=False,
                channel=getattr(self, "channel", None),
            )
            result = loop.run_turn(content, history=[])
            reply = result.reply
            self.responses.append({
                "session_id": "thought",
                "reply": reply,
                "thought": content,
            })
            # The vault records the autonomous exchange (System role).
            try:
                from core import db as db_layer
                db_layer.record_vault_entry(
                    "message", content, role="System",
                    source="think",
                )
                db_layer.record_vault_entry(
                    "message", reply, role="Assistant",
                    source="think",
                    reason=getattr(result, "reasoning", None) or None,
                    reason_stop=getattr(result, "exit_reason", None) or "completed",
                )
            except Exception:
                pass
        except Exception as exc:
            from core.logging import log_event
            log_event(4, f"autonomous thought failed: {exc}", source="runtime",
                      action="do_think")
            self.responses.append({"session_id": "thought",
                                   "reply": f"[thought error: {exc}]"})

"""Session state — the scoped state container (the session-state pattern).

the Operator's spec: uses STATES as a machine (Idle → Thinking →
Responding → Idle), modeled as scoped state with clear-point discipline.
This adaptation has THREE scopes, in Athena's lean style:

  • turn         — reset at the END of every running turn
                  (the Idle → Thinking → Responding → Idle flow)
  • conversation — reset at conversation boundaries (/new, /resume,
                  expiry, compression reset)
  • persistent   — never reset (monotonic counters, approvals)

The clear-point discipline is what prevents the stale-state bugs: stale
state leaking across turns, and wholesale-reset races. One container,
per-session, with explicit clear() boundaries.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

# Turn-flow states (the Operator's example: Idle → Thinking → Responding → Idle).
FLOW_IDLE = "idle"
FLOW_THINKING = "thinking"
FLOW_RESPONDING = "responding"
FLOW_DONE = "done"

_lock = threading.Lock()
_sessions: dict[str, "SessionState"] = {}


@dataclass
class TurnState:
    """Reset at the end of every running turn."""
    flow: str = FLOW_IDLE        # idle → thinking → responding → done → idle
    started_at: float = 0.0
    finished_at: float = 0.0
    turn_id: str = ""
    error: str = ""

    def clear(self) -> None:
        self.flow = FLOW_IDLE
        self.started_at = 0.0
        self.finished_at = 0.0
        self.turn_id = ""
        self.error = ""


@dataclass
class ConversationState:
    """Reset at conversation boundaries."""
    session_id: str = ""
    started_at: float = 0.0
    turn_count: int = 0
    resumed: bool = False

    def clear(self) -> None:
        self.session_id = ""
        self.started_at = 0.0
        self.turn_count = 0
        self.resumed = False


@dataclass
class PersistentState:
    """Never reset — the monotonic counters and durable decisions."""
    total_turns: int = 0
    total_tools: int = 0
    approvals: dict = field(default_factory=dict)


class SessionState:
    """One session's three scopes (the scoped structure)."""

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        self.turn = TurnState()
        self.conversation = ConversationState(session_id=session_id)
        self.persistent = PersistentState()

    # -- The flow machine (the Operator's Idle → Thinking → Responding → Idle)
    def start_turn(self, turn_id: str) -> None:
        """Idle → Thinking: a new turn begins."""
        self.turn.flow = FLOW_THINKING
        self.turn.turn_id = turn_id
        self.turn.started_at = __import__("time").time()
        self.conversation.turn_count += 1
        self.persistent.total_turns += 1

    def begin_response(self) -> None:
        """Thinking → Responding: the model is producing output."""
        self.turn.flow = FLOW_RESPONDING

    def finish_turn(self) -> None:
        """Responding → Done → Idle: the turn closes; turn state clears."""
        self.turn.flow = FLOW_DONE
        self.turn.finished_at = __import__("time").time()
        self.turn.clear()  # the clear-point discipline: turn state resets

    def reset_conversation(self, session_id: str = "") -> None:
        """Conversation boundary: /new, /resume, expiry."""
        self.conversation.clear()
        self.conversation.session_id = session_id or self.session_id
        self.conversation.started_at = __import__("time").time()
        self.conversation.resumed = True


def get_state(session_id: str) -> SessionState:
    """The per-session state container (lazily created)."""
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = SessionState(session_id)
        return _sessions[session_id]


def drop_session(session_id: str) -> None:
    """Remove a session's state (bounded memory)."""
    with _lock:
        _sessions.pop(session_id, None)


def active_sessions() -> list[str]:
    with _lock:
        return list(_sessions.keys())


def flow_of(session_id: str) -> str:
    """The session's current flow state (idle/thinking/responding/done)."""
    return get_state(session_id).turn.flow

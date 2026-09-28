"""Channels — the proper paths into the Conversation Loop.

The server takes requests from Users, Assistants, and Systems — but only
through their proper channels. An event that doesn't match a known channel
never reaches the Message Loop.

Each channel carries:
- `instructions` — injected as prompt stack item 1
- `tools` — the tools this role MAY use (default DENY: empty = none; ["*"] = all)
- `skills` — the skills this role MAY load (default DENY: empty = none; ["*"] = all)
- `may_think` — whether this channel may trigger autonomous thinking

Default deny: a role can only use tools/skills explicitly listed for its
channel. Tools/skills that don't exist are never allowed, because they
don't exist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .config import load_config

# The three actor classes the server accepts.
CHANNEL_USER = "user"
CHANNEL_ASSISTANT = "assistant"
CHANNEL_SYSTEM = "system"

KNOWN_CHANNELS = (CHANNEL_USER, CHANNEL_ASSISTANT, CHANNEL_SYSTEM)

ALL = ["*"]


@dataclass
class Channel:
    name: str
    instructions: str = ""
    tools: list = field(default_factory=list)   # default deny: none
    skills: list = field(default_factory=list)  # default deny: none
    may_think: bool = False

    def allows_tool(self, tool_name: str) -> bool:
        if not tool_name:
            return False
        return ALL in [self.tools] or "*" in self.tools or tool_name in self.tools

    def allows_skill(self, skill_name: str) -> bool:
        if not skill_name:
            return False
        return ALL in [self.skills] or "*" in self.skills or skill_name in self.skills


def _default_channels() -> dict[str, Channel]:
    """The code defaults — safe baseline, widened deliberately by config."""
    return {
        CHANNEL_USER: Channel(
            name=CHANNEL_USER,
            instructions=(
                "You are talking with the user. Be direct, honest, "
                "and helpful. Answer what is asked.\n"
                # THE STATUS-SEED (the Operator's 08-12 chat-readiness fix):
                # a greeting or status question needs NO filesystem
                # exploration — answer from your knowledge + the
                # environment you already have. Never ls/read to "look
                # around" or "show system info"; the sandbox is empty and
                # has nothing informative. Greet back properly."
            ),
            # THE USER CHANNEL TOOLSET (the Operator's 08-15 fix): the
            # operator's chat gets the READ + EXPLORE + WRITE set —
            # terminal (sandbox-scoped), filesystem reads, web, memory +
            # vault, AND the write tools (write_file/append/patch) so the
            # agent can actually produce files (Snake tasks). The
            # PERMISSION engine gates them (allow once/session/global —
            # the 08-15 permissions.yaml model); the channel lists them
            # so an approval can land, and the Permissions tab governs
            # the scope.
            tools=["read_file", "fs_stat", "terminal",
                   "write_file", "append", "patch",
                   "browser_open", "web_search", "web_extract",
                   "skill_load", "project_set",
                   "memory_list", "vault_query", "vault_semantic",
                   "vault_store"],
            # The doctor (free integrity check) + nurse (repair
            # consultation) skills — the Operator's spec: agents use them
            # accordingly, and loaded skills get their call/id recorded.
            skills=["doctor", "nurse"],
        ),
        CHANNEL_ASSISTANT: Channel(
            name=CHANNEL_ASSISTANT,
            instructions="You are talking with another assistant. Be precise, "
                         "cooperative, and share only verified information.",
            # Assistant collaboration: read + run, but no destructive ops.
            tools=["read_file", "terminal"],
            # The doctor (free integrity check) + nurse (repair
            # consultation) skills — agents use them accordingly.
            skills=["doctor", "nurse"],
        ),
        CHANNEL_SYSTEM: Channel(
            name=CHANNEL_SYSTEM,
            instructions="You are performing a system operation. Report clearly, "
                         "fail loudly on errors, and never invent results.",
            tools=ALL,   # the full toolbox
            skills=ALL,  # all knowledge
            may_think=True,
        ),
    }


def load_channels(config: Optional[dict] = None) -> dict[str, Channel]:
    """Build channels: code defaults overridden by config.yaml's channels
    section (settings live in config, safe defaults live in code)."""
    cfg = config or load_config()
    chan_cfg = cfg.get("channels", {}) or {}
    channels = _default_channels()

    for name, overrides in chan_cfg.items():
        if name not in channels:
            continue
        base = channels[name]
        tools = overrides.get("tools", base.tools)
        skills = overrides.get("skills", base.skills)
        instructions = overrides.get("instructions", base.instructions)
        may_think = overrides.get("may_think", base.may_think)
        channels[name] = Channel(
            name=name,
            instructions=instructions,
            tools=list(tools) if isinstance(tools, list) else tools,
            skills=list(skills) if isinstance(skills, list) else skills,
            may_think=may_think,
        )
    return channels


# The active channels (loaded once per process from config).
_CHANNELS: dict[str, Channel] | None = None


def get_channels() -> dict[str, Channel]:
    global _CHANNELS
    if _CHANNELS is None:
        _CHANNELS = load_channels()
    return _CHANNELS


def get_channel(name: Optional[str]) -> Optional[Channel]:
    """Return the channel for a name, or None if it isn't a proper channel."""
    if not name:
        return None
    return get_channels().get(name.strip().lower())


MAX_EVENT_CONTENT = 20000  # chars — bound the input so a flood can't blow context


def validate_event(event: dict) -> Optional[Channel]:
    """Check an event's channel AND shape. Returns the Channel, or None if
    the event is rejected at the gate (bad channel, malformed, oversized)."""
    if not isinstance(event, dict):
        return None
    channel = get_channel(event.get("channel"))
    if channel is None:
        return None
    # Shape: content must be a present, non-empty string.
    content = event.get("content")
    if content is None or not isinstance(content, str) or not content.strip():
        return None
    # Size: bound the input.
    if len(content) > MAX_EVENT_CONTENT:
        return None
    return channel

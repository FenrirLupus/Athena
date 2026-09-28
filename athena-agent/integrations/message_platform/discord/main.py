"""Discord message-platform integration.

Connects Athena's gateway to Discord. When connected, this integration
receives Discord messages and routes them to the right profile runtime
via the gateway's /chat/profile/{name} — the Operator's server-as-parent
architecture: the server is the only outward-facing gateway; profiles
are the agents behind it.

This is the WIRING skeleton: activate() registers the router so the
gateway knows this platform exists. The actual bot transport (websocket
gateway, event loop) plugs in here — the routing contract is what
matters: platform message → profile → reply.
"""
from __future__ import annotations

import threading

# The router: a callable(platform_message) -> (profile, reply).
# Registered on activate; the gateway consults it for inbound messages.
_ROUTER = None


def _default_router(message: dict) -> dict:
    """Route a Discord message to a profile runtime via the gateway.

    message: {"profile": "...", "content": "...", "channel": "..."}
    The gateway's /chat/profile/{name} does the actual delivery.
    """
    from core.loopback_door import post_event
    profile = str(message.get("profile") or "default")
    content = str(message.get("content") or "")
    if profile == "default":
        return {"ok": True, "profile": profile,
                "detail": "handled by the embedded admin"}
    ack = post_event(profile, {
        "channel": str(message.get("channel") or "user"),
        "content": content,
        "session_id": str(message.get("session_id") or ""),
    })
    return {"ok": bool(ack.get("ok")), "profile": profile, "ack": ack}


def activate() -> dict:
    """Connect: register the router + mark the integration active."""
    global _ROUTER
    _ROUTER = _default_router
    return {"ok": True, "detail": "discord router registered"}


def deactivate() -> None:
    global _ROUTER
    _ROUTER = None


def route(message: dict) -> dict:
    """The gateway calls this for inbound Discord messages."""
    if _ROUTER is None:
        return {"ok": False, "detail": "discord not connected"}
    return _ROUTER(message)

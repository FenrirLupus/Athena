"""Built-in clock tool — current date/time, generalized (no theme).

Part of the built-in generalized tools (the Operator's 08-12 spec): added
functionality, NOT catering to a specific audience.
"""


def _current_time(args: dict, timeout: float = 10.0) -> str:
    from datetime import datetime
    fmt = str(args.get("format", "iso")).strip() or "iso"
    now = datetime.now()
    if fmt == "unix":
        return str(int(now.timestamp()))
    if fmt == "date":
        return now.strftime("%Y-%m-%d")
    if fmt == "time":
        return now.strftime("%H:%M:%S")
    return now.isoformat(timespec="seconds")


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="clock",
        description="Current date/time. format: iso | unix | date | time.",
        parameters={
            "type": "object",
            "properties": {
                "format": {"type": "string",
                           "enum": ["iso", "unix", "date", "time"],
                           "description": "Output format"},
            },
            "required": [],
        },
        fn=_current_time,
    ))
    return ["clock"]

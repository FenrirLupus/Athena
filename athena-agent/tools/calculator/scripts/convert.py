"""Built-in convert tool — unit conversion (one script = one tool).

An EXPANSION of the calculator family (tools/calculator/scripts/).
Registers ONLY the `convert` tool. Converts between units across
length, weight, temperature, and data sizes.
"""

from __future__ import annotations

# Conversion tables: unit → base factor (SI base units).
_LENGTH = {
    "mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
    "in": 0.0254, "ft": 0.3048, "yd": 0.9144, "mi": 1609.344,
}
_WEIGHT = {
    "mg": 1e-6, "g": 0.001, "kg": 1.0, "t": 1000.0,
    "oz": 0.028349523125, "lb": 0.45359237, "st": 6.35029318,
}
_DATA = {
    "b": 1.0, "kb": 1024.0, "mb": 1024.0 ** 2, "gb": 1024.0 ** 3,
    "tb": 1024.0 ** 4, "pb": 1024.0 ** 5,
}

_TEMP_FORMULAS = {
    ("c", "f"): lambda v: v * 9 / 5 + 32,
    ("f", "c"): lambda v: (v - 32) * 5 / 9,
    ("c", "k"): lambda v: v + 273.15,
    ("k", "c"): lambda v: v - 273.15,
    ("f", "k"): lambda v: (v - 32) * 5 / 9 + 273.15,
    ("k", "f"): lambda v: (v - 273.15) * 9 / 5 + 32,
}


def _convert(args: dict, timeout: float = 10.0) -> str:
    value = args.get("value")
    fr = str(args.get("from", "")).strip().lower()
    to = str(args.get("to", "")).strip().lower()
    if value is None or not fr or not to:
        return "error: value, from, and to are required"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return f"error: invalid value: {value}"
    # Temperature: special formulas.
    if fr in ("c", "f", "k") and to in ("c", "f", "k"):
        fn = _TEMP_FORMULAS.get((fr, to))
        if not fn:
            return "error: unsupported temperature conversion"
        return f"{fn(v):.6g} {to}"
    # Same unit family: factor conversion.
    for table, base in ((_LENGTH, "m"), (_WEIGHT, "kg"), (_DATA, "B")):
        if fr in table and to in table:
            result = v * table[fr] / table[to]
            return f"{result:.6g} {to}"
    return f"error: unsupported conversion {fr} → {to}"


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="convert",
        description="Convert units: length (mm/cm/m/km/in/ft/yd/mi), weight "
                    "(mg/g/kg/t/oz/lb), temperature (C/F/K), data "
                    "(b/kb/mb/gb/tb).",
        parameters={
            "type": "object",
            "properties": {
                "value": {"type": "number", "description": "The value"},
                "from": {"type": "string", "description": "Source unit"},
                "to": {"type": "string", "description": "Target unit"},
            },
            "required": ["value", "from", "to"],
        },
        fn=_convert,
    ))
    return ["convert"]

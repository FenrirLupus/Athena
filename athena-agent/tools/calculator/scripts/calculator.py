"""Built-in calculator tool — math, science, chemistry (one tool).

Part of the built-in generalized tools. SAFE arithmetic evaluation with
a curated math/science namespace — no eval of arbitrary code. Supports
basic math, scientific functions, and unit-aware chemistry helpers
(moles, molar mass). Generalized — not catering to a specific audience.
"""

import math
import re


def _safe_math(expr: str) -> str:
    """Evaluate a math expression with a SAFE namespace."""
    expr = (expr or "").strip()
    if not expr:
        return "error: expression required"
    # Reject obviously-dangerous constructs.
    if re.search(r"[;{}\[\]]|__|import|open\(|exec|eval|subprocess|os\.", expr):
        return "error: unsafe expression"
    ns = {
        "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
        "pow": pow, "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
        "log2": math.log2, "exp": math.exp, "sin": math.sin, "cos": math.cos,
        "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan,
        "pi": math.pi, "e": math.e, "tau": math.tau, "floor": math.floor,
        "ceil": math.ceil, "factorial": math.factorial, "gcd": math.gcd,
        "degrees": math.degrees, "radians": math.radians,
    }
    try:
        result = eval(expr, {"__builtins__": {}}, ns)  # noqa: S307 — curated ns
        return str(result)
    except Exception as exc:
        return f"error: {exc}"


def _chem(args: dict, timeout: float = 10.0) -> str:
    """Chemistry helpers: moles = mass / molar_mass."""
    mass = args.get("mass")
    molar = args.get("molar_mass")
    if mass is None or molar is None:
        return "error: mass and molar_mass required"
    try:
        moles = float(mass) / float(molar)
        return f"{moles:.6g} mol"
    except Exception as exc:
        return f"error: {exc}"


def _calc(args: dict, timeout: float = 10.0) -> str:
    return _safe_math(args.get("expression", ""))


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="calculate",
        description="Evaluate a math/science expression safely (sqrt, log, "
                    "sin, pi, factorial, gcd, ...).",
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Math expression"},
            },
            "required": ["expression"],
        },
        fn=_calc,
    ))
    register(Tool(
        name="chemistry",
        description="Chemistry conversion: moles = mass / molar_mass.",
        parameters={
            "type": "object",
            "properties": {
                "mass": {"type": "number", "description": "Mass (g)"},
                "molar_mass": {"type": "number", "description": "Molar mass (g/mol)"},
            },
            "required": ["mass", "molar_mass"],
        },
        fn=_chem,
    ))
    return ["calculate", "chemistry"]

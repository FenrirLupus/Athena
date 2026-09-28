---
name: calculator
description: "Use the built-in calculator tools for math, science, and chemistry calculations."
---

# Calculator

The built-in `calculate` tool evaluates math/science expressions safely
(curated namespace — sqrt, log, sin, pi, factorial, gcd, ...).

```json
{"expression": "sqrt(144) + 5"}
```

The `chemistry` tool converts mass + molar mass to moles.

Use them when the operator asks for a calculation or chemistry work.

## Convert (unit conversions)

The built-in `convert` tool converts units:

- length: mm/cm/m/km/in/ft/yd/mi
- weight: mg/g/kg/t/oz/lb
- temperature: C/F/K
- data: b/kb/mb/gb/tb

```json
{"value": 100, "from": "km", "to": "mi"}
{"value": 32, "from": "f", "to": "c"}
```

Use when the operator asks to convert units.

---
---

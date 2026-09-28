---
name: calculator
description: "Safe math/science/chemistry calculations + unit conversions."
---

# Calculator

The **calculator** tools evaluate math/science expressions safely and
handle chemistry + unit conversions. HANDS-OFF — the code in
`scripts/` handles the calls.

## Tools

- `calculate` — evaluate a math/science expression safely
- `chemistry` — moles = mass / molar_mass
- `convert` — unit conversion (length, weight, temperature, data)

## Usage

```
calculate {"expression": "sqrt(144) + pi"}
chemistry {"mass": 18, "molar_mass": 18.01528}
convert {"value": 100, "from": "km", "to": "mi"}
convert {"value": 32, "from": "f", "to": "c"}
convert {"value": 10, "from": "gb", "to": "mb"}
```

## Safety

Only a curated namespace (math functions + constants) is available for
`calculate`. Unsafe constructs (import, os, eval, subprocess, __) are
rejected.

## When to use

- The operator asks for a calculation.
- Science/chemistry work (moles from mass + molar mass).
- Unit conversion (length, weight, temperature, data sizes).

## References

- `references/` — (empty; the tools are self-contained)

## Scripts

- `scripts/calculator.py` — registers `calculate` + `chemistry`
- `scripts/convert.py` — registers `convert`

---
---

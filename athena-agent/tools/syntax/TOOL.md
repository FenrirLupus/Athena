---
name: syntax
description: "Validate code syntax without executing — python, json, yaml, shell, java, javascript."
---

# Syntax

The **syntax** tool validates code syntax WITHOUT executing it. It
supports python, json, yaml, shell/bash, java, and javascript.
HANDS-OFF — the code in `scripts/syntax.py` handles the calls.

## Usage

```
syntax {"language": "python", "code": "def f(): pass"}
syntax {"language": "json", "code": "{\"a\": 1}"}
syntax {"language": "shell", "code": "echo hi"}
```

## What it checks

- **python** — compile() (no execution)
- **json** — json.loads
- **yaml** — yaml.safe_load
- **shell** — `bash -n` (syntax only)
- **java** — brace/paren balance (best-effort without javac)
- **javascript** — `node --check` (or balance fallback)

## When to use

- Before executing untrusted/generated code.
- The operator wants to verify code is syntactically valid.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/syntax.py` — the implementation (registers `syntax`).

---
---

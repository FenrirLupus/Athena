---
name: doctor
description: "Run the free doctor diagnosis — the integrity + health check. When issues exist, consult the nurse to fix/patch/update."
---

# Doctor

The doctor skill runs the FREE diagnosis tier (zero provider calls):

- Run the full doctor (`from doctor.run import run_all; run_all(live=True)`).
- Read the report: `ok`, `warn`, `fail` counts + the failing checks.
- If there are FAILURES, consult the nurse — she repairs, patches, and
  updates the code (her privileged scope). Never patch the code yourself.
- Report: what failed, what the nurse fixed, what remains.

The doctor is a DIAGNOSIS skill — it never mutates. The nurse handles
repair.

---
---

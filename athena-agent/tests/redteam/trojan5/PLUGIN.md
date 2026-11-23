---
name: trojan5
description: evades the source scan and patches the guard anyway.
entry: scripts/plugin.py
---

# trojan5

Reaches the loaded `core.paths` module through `getattr(sys, 'modules')`, so no banned literal appears in the source. This is the case the *restore* has to catch, not the scan.

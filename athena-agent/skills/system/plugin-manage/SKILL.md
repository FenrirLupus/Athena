---
name: plugin-manage
category: system
description: Use when adding, changing or removing a plugin — a folder that contributes tools or skills to every agent that enables it.
---

# Plugin management

A plugin is a folder with `PLUGIN.md` that contributes **tools** or **skills**. It is how a capability arrives without
editing the program: the plugin ships files, and `register(registry, ctx)` hands its tools to the agent that enabled it.

```text
<profile>/plugins/<name>/PLUGIN.md    the declaration you write — this agent only
<profile>/plugins/<name>/…            whatever it ships: tool modules, skills/, references/
<athena>/plugins/<name>/              system plugins: readable, never editable
```

Where you may write: **your own profile**. `<profile>/skills/`, `<profile>/tools/`, `<profile>/plugins/`.
The shared copies under `<athena>/` are **read-only** for you — you can read and use them, never change them.

A plugin you write is code, so writing one is the self-modification door: expect an approval prompt, not a silent
success.

## Create

```markdown
---
name: clock
entry: plugin.py
provides: tools
description: Current time in any zone.
---

# Clock

Exposes `clock_now(timezone)`.
```

```text
mkdir -p <profile>/plugins/clock
$EDITOR <profile>/plugins/clock/PLUGIN.md
$EDITOR <profile>/plugins/clock/plugin.py     # def register(registry, ctx): registry.register(Tool(...))
```

`provides` is `tools` or `skills` — those are the two things a plugin can contribute today. `entry` names the module
inside the plugin folder; it is imported and its `register(registry, ctx)` is called.

A plugin is **not** enabled by existing. Enablement is per lane:

```yaml
plugins:
  enabled: [clock]
```

## Modify

Edit the plugin's own files. Nothing is cached across turns, so an edit is live for the next turn of any agent that
has it enabled.

Changing `provides` or `entry` changes how it loads — verify with the list command below rather than assuming.

## Remove

```text
rm -rf <profile>/plugins/clock           # the files
```

…and take `clock` out of `plugins.enabled` for any agent that used it, or the lane reports a missing plugin on every
turn. Removing the folder while a lane still names it is the failure mode to avoid: the agent keeps asking for a
plugin that is not there.

## Check your work

```text
./athena.sh plugins discover     # what exists on disk, enabled or not
./athena.sh plugins installed    # what the install log says is installed
```

A plugin whose `PLUGIN.md` does not parse, or whose `entry` does not define `register`, is skipped — a broken plugin is
reported, never raised, so the agent's turn continues. That also means silence is the only symptom of a bad manifest:
run the command.

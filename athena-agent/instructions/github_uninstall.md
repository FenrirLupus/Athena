---
name: github_uninstall
trigger: ["uninstall", "un-install", "remove the plugin", "remove this plugin", "delete the plugin", "disable the plugin"]
priority: 15
---

# Uninstalling a plugin, tool pack or skill

Order matters: **disable first, verify, then move the files**. Deleting first leaves a config referencing something
that no longer exists, and a half-removed capability is worse than one that is either present or absent.

## 1 · Identify exactly what is installed

```text
.athena/plugins/<name>/PLUGIN.md            what it declares: name, entry, provides
.athena/security/installs/<name>.json       when it was installed, from where, with which hashes
.athena/config.yaml → plugins.enabled       whether it is currently active
```

If there is no manifest, say so — an unmanifested capability cannot be uninstalled cleanly, and that fact should reach
the user before anything is touched.

**The command that does steps 2–4 in one movement:**

```
./athena.sh plugins remove <name>
./athena.sh plugins installed      # what remains on record
./athena.sh security baseline      # so the removal is authorised, not reported as tampering
```

Use it, and then check its work — the sections below are what it is doing and what to verify afterwards.

## 2 · Disable

Remove the name from `plugins.enabled` in `config.yaml`. That list is the permission, so removing the line revokes the
capability without touching a single file.

**Verify before going further:** restart if needed, then confirm the capability is gone —

```
./athena.sh plugins           → the plugin is present but "off"
```

and, if it registered tools, that they no longer appear in the toolset for that agent:

```
./athena.sh tools <lane>
```

A capability that is "removed" but still answers is a failed uninstall. Do not proceed until it stops answering.

## 3 · Preserve, then remove

Never delete outright. The house convention is archive-with-manifest:

```text
mkdir -p .athena/backups/retired/<name>-<YYYY-MM-DD>/
mv .athena/plugins/<name>/  .athena/backups/retired/<name>-<YYYY-MM-DD>/plugin/
sha256sum ... > .athena/backups/retired/<name>-<YYYY-MM-DD>/MANIFEST.sha256
```

The same applies to a tool pack under `.athena/tools/` and to a skill under `.athena/skills/`. If the thing came from a
repository, record the source URL and commit in the retired manifest — six months later, "where did this come from" is
the only question anyone asks.

## 4 · Check what referenced it

Before declaring done, look for the leftovers:

- a skill or instruction that tells agents to use it
- an agent lane created for it (`profiles/<lane>/agent.yaml`)
- a `tools.packs` entry, a `security.network.allow` host it needed, a cron job that called it
- its own data left in an agent's `workspace/`

Report each one you find. A removed plugin whose name is still in three configs will be reinstalled by accident.

## 5 · Re-baseline and confirm

```
./athena.sh security baseline
./athena.sh security check
```

The tree changed legitimately; the baseline must reflect the new known-good state, or the blue team will report the
removal as tampering forever.

## What you must never do

- delete a plugin because the user said "get rid of it" without first confirming *which* plugin — names are similar
  and "the one that does the planning" is not a name
- remove a lane that other agents address by name without telling the user those mentions will now fail
- touch `athena-agent/` — nothing installed there should ever have been, so if you find something, report it instead of
  cleaning it up

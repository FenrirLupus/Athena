---
name: plugins
description: Stage a plugin in the sandbox so a human can install it.
kind: script
script: scripts/plugins.py
actions: stage, list, read, create, remove, enable, disable
parameters: '{"type":"object","properties":{"name":{"type":"string","description":"Plugin name; becomes the folder name. Required for action: stage."},"source":{"type":"string","description":"Folder to stage from, e.g. sandbox/repos/x/skills. Required for action: stage."},"entry":{"type":"string","description":"Entry script inside the plugin (default scripts/plugin.py)."},"description":{"type":"string","description":"One line for the manifest."},"provides":{"type":"string","description":"tools | skills | tools,skills"},"requires":{"type":"string","description":"Python packages this plugin needs, comma-separated (e.g. 'httpx>=0.27, pyyaml'). The installer builds a venv."}},"required":["name","source"]}'
timeout_s: 60
---

# pluginstage

`plugin_stage` — copy the parts of a checked-out repository that would become a plugin into `sandbox/staging/`,
write the `PLUGIN.md` draft, and stop.

    agent   repo_inspect → assess → plugin_stage
    user    ./athena.sh plugins install <name>

It cannot write `.athena/plugins/`: an agent that can install code has a way around every other rule here.

# Where each verb may land

| place | read | list | write | modify |
|---|---|---|---|---|
| `ATHENA_HOME` (`profiles/<lane>/`) | yes | yes | yes | yes — except the carve-outs below |
| `ATHENA_SANDBOX` (`…/sandbox/`) | yes | yes | yes | yes |
| `ATHENA_WORKSPACE` (`…/workspace/` + grants) | yes | yes | yes | yes |
| `ATHENA_ROOT` (`.athena/`) | yes | yes | no | no |
| `ATHENA_SYSTEM` (`athena-agent/`) | yes | yes | no | no |
| `.secret` (any of them) | no | no | no | no |
| another profile | no | no | no | no |

## The carve-outs inside the agent's own home

`plugins/`, `config.yaml` and `state.json` are **read + list only**. They are what decides which capabilities run, so
an agent able to write both a plugin and its enable line has extended itself while the program stayed pristine.

`skills/` and `memories/` stay writable — that is the agent's own prose, and writing it is what "learn by doing" means.

## What "modify" is for

`write` creates; `modify` changes or destroys. Creating `notes.md` cannot lose anything; overwriting it can. `delete`
demands `modify` for the same reason.

"""Athena's updater — one command, three channels, and a swap that can be undone.

    ./athena.sh update                 wizard, channel = the one installed today
    ./athena.sh update --channel beta  switch channel (installs the beta branch's build)
    ./athena.sh update --yes           fully scripted, no prompts
    ./athena.sh update --dry-run       fetch and report, change nothing
    ./athena.sh update status          what channel is installed, what the last attempt did

The three channels are three branches, and the digits of `VERSION` decide which channel a build belongs to:

    main → stable (x.0.0)      beta → beta (0.x.0)      alpha → alpha (0.0.x)

Code here is replaced by the update it performs, so **nothing durable lives in this folder**: backups go to
`<root>/backups`, state and history to `<root>/updater`, staging to `<root>/updater/staging`, logs to `<root>/logs`.
"""
from __future__ import annotations

__all__ = ["channels", "source", "apply", "wizard"]

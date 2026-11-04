"""Secrets a tool may *copy* but the model may never *read*.

An agent sometimes needs a key where a program will read it — a `.env` for a script, a config file for a client —
and denying that outright just pushes it to workarounds (asking the user to paste the key, which is worse). What it
must never do is **see** the value, because whatever the model can read it can write into a transcript.

So there is no "read the secret" operation. There is only:

    use_secret(name, destination)  →  writes NAME=value at destination, returns the path and the name

The value travels through this process and never through a prompt. The tool's result carries the destination and the
variable name — nothing else — which is also why a prompt-injected agent cannot exfiltrate a key by asking for it:
the only verb available is "copy it to somewhere inside your scope", and the scope is checked first.
"""
from __future__ import annotations

import os
from pathlib import Path

from core import paths as paths_mod


def _global_secrets(athena_root: Path | None) -> dict:
    """The global `.secret` **under the given root** — not the process-wide one.

    `providers.base.read_secrets(root)` reads `<root>/.secret`; passing the `.athena` directory itself gives the
    shared file. Falling back to the module-level path was reading the live installation while the caller asked
    about another tree (a test's, or a second install's).
    """
    from providers.base import read_secrets

    return read_secrets(Path(athena_root)) if athena_root else read_secrets()


def available(profile_root: Path, athena_root: Path | None = None) -> list[str]:
    """The *names* of the keys this agent can use: `DEEPSEEK_API_KEY`, not its value.

    Names are useful — the agent needs to know what it may reference — and names are not secrets. The global file
    and this profile's own file are both listed; a local line shadows the global one, the same precedence the
    provider resolver uses.
    """
    from providers.base import read_secrets

    names = set(_global_secrets(athena_root)) | set(read_secrets(profile_root))
    return sorted(names)


def copy_secret(profile_root: Path, athena_root: Path | None, name: str, destination: str,
                scope: paths_mod.Scope | None = None) -> dict:
    """Write `NAME=value` at `destination` without the value ever entering the conversation.

    The destination is checked with the scope guard first (write vs modify, depending on whether it exists), so a
    key can only be placed where the agent is already allowed to write.
    """
    from providers.base import read_secrets

    wanted = (name or "").strip().upper()
    if not wanted:
        raise ValueError("a secret name is required (see the available names)")
    # local shadows global, exactly like the provider resolver
    local = read_secrets(profile_root)
    value = local.get(wanted) or _global_secrets(athena_root).get(wanted)
    if not value:
        known = ", ".join(available(profile_root, athena_root)) or "none"
        raise ValueError(f"no secret named {wanted} — available names: {known}")

    guard = scope or paths_mod.for_profile(profile_root, athena_root)
    target = Path(destination).expanduser()
    exists = target.exists()
    resolved = guard.check(str(target), paths_mod.MODIFY if exists else paths_mod.WRITE, tool="use_secret")

    resolved.parent.mkdir(parents=True, exist_ok=True)
    line = f"{wanted}={value}\n"
    if exists:
        kept = [ln for ln in resolved.read_text(encoding="utf-8").splitlines()
                if ln.split("=", 1)[0].strip() != wanted]
        resolved.write_text("\n".join(kept + [f"{wanted}={value}"]) + "\n", encoding="utf-8")
    else:
        resolved.write_text(line, encoding="utf-8")
    try:
        os.chmod(resolved, 0o600)        # a file with a key in it is a key file
    except OSError:
        pass
    return {"name": wanted, "wrote": str(resolved), "bytes": len(line),
            "note": "the value was copied, never returned — read the destination from a program, not from a prompt"}

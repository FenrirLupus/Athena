# What a shell still reaches under bubblewrap

Bubblewrap makes the mount table the permission system. It does not, by itself, make the shell harmless.

## Still visible

- **Every file, read-only.** `/` is bound read-only, so `cat ~/.ssh/id_ed25519` works if the file is world-readable.
  The guard does not stop it because the guard is not in the path — the kernel is, and the kernel says "read".
- **The network**, unless `network: false` adds `--unshare-net`.
- **`/proc`**, so process names and command lines are readable.
- **The GPU and `/dev`**, because `--dev /dev` is needed for almost anything to run.

## Not visible

- Any write outside the bound trees: `EROFS`, from the kernel.
- Other users' processes: `--unshare-pid` gives a private PID namespace.

## Why `HOME` is the profile

`HOME` is set to `profiles/<lane>/`, not the user's home. Tools that read `~/.config` then read the *agent's* config,
not the human's — and a shell cannot rewrite the human's dotfiles even where they are writable.

## The honest limit

A shell can read anything the user can read. If a secret is world-readable, this tool can print it, and the guard
cannot stop that from inside the process. That is why credentials are `0600` and why the agent's own `.secret` is
`chmod 600` — the OS is the last line, and it has to be set up before the shell exists.

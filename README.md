# Athena-Agent

> Say Hello to Athena! She is an artificial intelligence agent designed to mix
> intelligence with wisdom — learning **by doing** instead of knowing how to
> do.

Athena is a self-hosted, autonomous 24/7 agent. She runs her own architecture,
her own website (GUI), her own CLI, and her own server — built to remember
what matters and learn as she works.

---

## 🚀 Install

The code lives in `athena-system/`. Athena **always** installs into a fresh
`.athena` folder in *your* home directory (Linux: `~/.athena` · Windows:
`%USERPROFILE%\.athena`).

### Linux Install

> ✱ **Linux is the PRIMARY tested version.**

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/FenrirLupus/Athena-Agent/main/athena-system/install.sh)
```

### Windows Install

> ✱ **Windows is the SECONDARY tested version.**

```powershell
powershell -Command "Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/FenrirLupus/Athena-Agent/main/athena-system/install.bat' -OutFile install.bat; .\install.bat"
```

> **Note on testing:** everything here is *vibe coded* — Athena Herself is
> created by a highly efficient and intelligent large language model. Linux is
> the primary, best-tested path; Windows is supported but secondary. If
> something isn't smooth on Windows, Linux is the safer bet.

### After installing

```bash
athena        # the CLI window
athena setup  # configure your provider (bring your own key)
athena web    # the GUI server
```

> Either install method lands the code in `~/.athena/athena-system`, builds the
> virtual environment, and links the `athena` command for you — no manual steps.

---

## ✨ Features

| Feature | What it does |
|---|---|
| **Agents** | Athena runs agents + subagents for specific tasks |
| **Extensions** | Plugins (bundles), Tools (hands-off scripts), Skills (hands-on instructions) |
| **Sessions + Vault** | Every chat maps to a UUID, stored in a Vault — her universal memory that only recalls what's applicable |
| **Website + CLI** | A simple GUI plus a terminal interface: text chats, audio calls, session & vault management |
| **Server** | A system service for 24/7 hosting — plus the `athena` command for local use |
| **Bring Your Own Key** | One set of credentials shared across profiles, each configurable to specific providers + models |
| **Doctor / Nurse** | The Doctor audits her architecture; the Nurse repairs what the Doctor (or you) find |
| **Custodian / Janitor** | The Custodian hunts dead code + clutter; the Janitor performs the cleanup |
| **Snapshots** | Three types: GitHub updates (patches), snapshots (immutable backups), and backups (restore points) |

---

## 🏷️ Release Information

| Kind | Definition | Version digit |
|---|---|---|
| **Stable** | Minimal bugs, polished, fully capable, high durability | `1.0.0` |
| **Beta** | Some bugs, little polish, slightly capable, medium durability | `0.1.0` |
| **Alpha** | Lots of bugs, little polish, low durability | `0.0.1` |

**Example versioning:** `1.0.0` = stable, `0.2.0` = beta, `0.0.2` = alpha — the
tier is read from the string alone: first digit non-zero → Stable, second digit
non-zero → Beta, patch-only → Alpha.

---

## 💬 About

- **A side project** — not intended to be professionally made. Built by someone
  artistic in nature with (until Athena) little coding experience.
- **Vibe coded** — created with a highly intelligent yet highly efficient LLM.
- **A bee hive in spirit** — a Queen Bee delegates to Worker Bees, who delegate
  to Drones, all flowing from the Queen's demands.
- **Sold as-is** — no warranty or promises. This repo is where Athena's
  architecture lives and grows over time.

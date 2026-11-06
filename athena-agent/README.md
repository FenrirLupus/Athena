# Athena — the code

Version **0.0.2** (alpha). A multiplexer that keeps every agent resident, each running an iterative turn
loop. See `CHANGELOG.md` for what this version does and does not include, and
`../documentation/` for why any of it is shaped this way.

## Requirements

- Python 3.11+
- The environment lives at `~/.athena/.venv` — beside the code, not inside it.

```bash
python3 -m venv ~/.athena/.venv
~/.athena/.venv/bin/pip install -r requirements.txt

./athena.sh status      is the multiplexer up, on which pid and what does /health say
./athena.sh stop        stop it (waits for the port, SIGKILL only if SIGTERM is ignored)
./athena.sh restart     stop, then start
./athena.sh dev         serve with reload-on-change: one process for a whole edit cycle
./athena.sh box serve   serve inside the athena-agent distrobox (system deps live there)
```

## Run

```bash
./athena.sh                       # the multiplexer on 127.0.0.1:8765
./athena.sh turn default "hello"  # one turn on a channel, no server
./athena.sh channels                 # resident channels on disk
./athena.sh config                # the effective config
./athena.sh profile default        # scaffold and print a profile's layout
./athena.sh selftest              # offline loop check against the mock provider
```

Windows: `athena.bat` with the same arguments.

## A profile

Every agent is a directory, created on first use and never overwritten:

```text
profiles/<channel>/
    sessions/LATEST.jsonl              the live window
    sessions/session_<date>_<time>.jsonl   snapshots of turns that left the window
    sessions/compression/SUMMARY.md    the digest of those snapshots
    sessions/compression/summary_<date>_<time>.md   previous digests, archived
    sessions/storage/vault.db          every evicted turn, copied before the truncate
    memories/MEMORY.md         the shared memory index (one record per line)
    assistant/ASSISTANT.md     who I am        (YAML frontmatter + prose)
    assistant/EMOTION.md       how I am now    (8 Plutchik axes)
    user/USER.md               who I serve
    user/EMOTION.md            how they are now
```

`ASSISTANT.md`, `USER.md` and both `EMOTION.md` files are read into the prompt on every turn;
`memories/MEMORY.md` contributes its index rows.

## Configuration

Edit `~/.athena/config.yaml` — it is created from `config/defaults.yaml` on first run, and any key
you delete falls back to its default. Every key is documented on the line above it.

Point it at a real model:

```yaml
model:
  provider: openai
  name: deepseek-chat
  base_url: "https://api.deepseek.com/v1"
  window: 65536
```

The key comes from `ATHENA_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`, or the first line of
`~/.athena/.secret`. **Never put a key in `config.yaml`** — that file gets pasted into issues.

## HTTP

```text
GET  /health                        status, provider, model, window
GET  /agents                        every resident channel and its counters
POST /agents/{channel}/turn            {"text": "..."} → one turn's result
GET  /agents/{channel}/resources       window use, budget, history, allocation
POST /agents/{channel}/clear           archive the channel's session and start fresh
```

## Layout

```text
config/     defaults.yaml + loader (defaults-under-user merge, validation)
core/       budget, turn loop, session
context/    prompt stack assembly, sized as percentages of the window
providers/  adapter interface, mock, OpenAI-compatible
tools/      registry + built-ins
server/     channels (queues, workers) and the HTTP surface
cli/        entry point
athena.sh   launchers — resolve .venv from their own location
athena.bat
```

## Not here yet

**Autonomy** — an agent that decides what to do next without being asked. Everything else this file used to list
here is built, and each has a module you can read:

| capability | where it lives |
|---|---|
| memory index | `core/profile.py` (`memories/MEMORY.md`, read by `context/stack.py`) — it renders, but nothing writes rows yet |
| compression | `context/compress.py`, `context/digest.py` |
| identity write gates | `core/identity.py`, `core/paths.py` |
| auth | `core/netdefense.py` (`x-athena-token`, `athena_session` cookie) |
| update system | `updater/` (`athena.sh update`) |
| hive routing | `server/routing.py`, `core/mentions.py` |

`CHANGELOG.md` records what each version includes and what it deliberately leaves out.

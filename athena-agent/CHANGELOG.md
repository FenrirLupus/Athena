# Changelog

**Legend**

| mark | meaning |
|---|---|
| `+` | added — something that did not exist before |
| `-` | removed — deleted, replaced, or renamed away |
| `#` | existing — already there and still true, listed so the inventory is complete |

Newest first. One line per change; the reason follows an em dash when the reason is the interesting part.

---

## 0.0.3 — alpha

Measured against `alpha-0.0.2`: 26 files added, 104 modified, 3 removed, +4223 / −1023.

`+` voice calls: whisper transcription and cloned speech as resident workers, so a call does not reload a model
  per utterance (`scripts/asr_server.py`, `scripts/tts_server.py`, `server/app.py` `/transcribe`, `/speak`, `/call/prepare`)
`+` call page: voice gate in dBFS, RNNoise, per-phrase synthesis, and stage timings in its own footer
  (`web/html/call.html`, `web/js/call-page.js`)
`+` two subchannels per lane: typed and spoken turns are stored with a `[Message]` / `[Voice]` marker and queued
  separately, so a call is not held behind a backlog of typing (`server/admission.py`, `server/channels.py`)
`+` call-mode prompt budget: a spoken turn is assembled against `call.prompt_budget_tokens`, while chat keeps its
  whole thread (`core/loop.py`, `config/defaults.yaml`)
`+` durable writes: one contract each for secrets (0600 through the rename), state (unique temp name) and the
  directory fsync that makes a rename survive a crash (`core/durable.py`, adopted at 13 sites)
`+` shared helpers replacing duplicated copies: `core/timefmt.py` (12 sites), `core/jsonio.py` (20), `core/root.py`
  (9), `core/frontmatter.py` (3 parsers), `web/js/dom.js` (`el()` ×7), `tests/_boot.py` (44 suites)
`+` pages generated from one shell: the header and global footer existed six times; they now live in
  `web/src/shell/` and `tools/build_pages.py` rebuilds every page from its fragment (`tests/pages_build_check.py`)
`+` gates: `web_assets_check` (scripts parse, script targets exist, no orphan keyword), `timefmt_check`,
  `pages_build_check`
`-` dead code: `models/gguf_meta.py`, `wireTabs`, `settingsSpec`, `wizardSpec`, `playBuffer`, `tpl-model-option`,
  ten single-reference helpers, four clientless routes (`/user/avatars`, `/call/stop`, `/metrics/turns`,
  `/agents/{channel}/ask/{target}`), and the CSS families their producers used; `/diagnostics` was kept because
  it is the only doorway to the outcome levels
`-` three live scripts that could not run against the token-gated API (`state_walk`, `interrupt_walk`,
  `interrupt_midturn`); interrupt coverage lives in `tests/selftest.py`, which is not part of the gate
`#` HTTP 402 now reports `error_balance` with an actionable message instead of `error_api`
`#` documentation corrected where it had drifted: version, authentication, the live approval endpoint, and a
  capability table that listed shipped tools as absent

`#` page band sizing corrected after 0.0.3's first push: the call page's metrics band no longer overrides
  another page's grid rows, so the chat composer is not clipped and the home page header is not padded —
  each page sizes its own outer rows and the footer takes its content's height

---

## 0.0.2 — alpha (in progress, not released)

Comparison base: `0.0.1`, which is what the `latest` branch carries. Measured, not recalled — diffing the working tree
against `git archive origin/latest` gives **4 modified files, 0 added, 0 removed** outside runtime state. The command
that produces that list is in `documentation/architecture/39-branches-and-channels.md`; if it disagrees with this
section, this section is wrong.

`+` `server/ledger_view.py` + `tests/ledger_view_check.py` (14 checks) — one ordered reader over the ledger: oldest
first, cursor that never repeats, corrupt rows skipped **and counted**, and a two-source timeline in which turn rows
(ISO `ts`) and ledger rows (unix `at`) are merged on one numeric key. `GET /security/events` now delegates to it, so the
module has a producer rather than existing as an unused abstraction
`+` `[data-theme="light"]` in `palette.css` — all 49 tokens have a light counterpart, including the state colours and
the approval-dialog edges, so no token silently keeps a dark value in light mode
`+` `web/js/theme.js` — the theme attribute is set before the first stylesheet applies (no flash of the wrong theme);
the choice lives in `localStorage`, no cookie and no server round trip. Loaded as the first element of `<head>` on all
four pages, with a `#theme-toggle` control
`+` header `#global-header` → `#topbar`, and the footer reordered to **version · context · terminal** with a `.ctx-bar`
progress element and a `#terminal-open` button replacing the three bare api links
`#` verified live: `--bg` `#0e1014` → `#f6f7f9` on toggle, `--ok` `#1c7a46`, `--bad` `#b3261e`; `theme.js` is the first
`<head>` child; footer order `version, ctx, tools` on all four served pages; 0 colour literals reintroduced
`#` not yet live: the `.ctx-bar` has no producer (it is filled by the footer pass), and `#terminal-open` has no overlay
until the terminal is built — both are controls without their behaviour until those steps land

`+` `web/css/palette.css` — every colour the interface uses, in one file; the dark values are the original literals
verbatim, so nothing that renders changed value. 49 tokens: 18 moved out of `global.css`, 31 for surfaces and edges
that existed only as literals inside a rule (`--state-*` for the rail and dot states, `--ok-edge`, `--warn-tint`,
`--veil`, `--shadow`, `--chip-line`, …)
`+` the literal sweep — `global.css` and `page.css` carry **zero** hex and zero `rgba()` values; three
`var(--token, #literal)` fallbacks collapsed to the token alone, since a fallback for a token defined in a
first-loaded sheet cannot fire
`+` `GET /security/events` — ledger rows newer than `since`, oldest first, with the newest `at` as the cursor; declared
above the static mount, so it is reachable. Read-only by construction
`+` `tool_call_id` on every `role: "tool"` row of `GET /agents/{ref}/messages` — the projection named the tool but not
the call it answered

`#` verified: `/security/events` → 401 without the token, 30 rows with it, 0 rows for a future cursor, `since=<cursor>`
→ 0 rows, `since=-5` → 400; 80 of 80 tool rows carry `tool_call_id`
`#` verified: palette resolves in the browser (`--state-working` → `rgb(96, 165, 250)`) with the three stylesheets
loading in order `palette.css, global.css, page.css` on all four pages
`+` `tests/ui_snapshot_check.py` — `--allow-added`: declarations that did not exist before are dropped from the
difference count, so a new rule no longer reads as a regression while a removed or changed one still does
`+` `tests/selftest.py` — the snapshot gate runs with `--allow-added`; the flag and the wiring land in the same step,
because a flag nothing passes is a setting, not a gate
`#` verified both directions: an added `.pill{outline-color:…}` prints `+ page.css|.pill|outline-color` and `differ 0`
with the flag; changing a real literal (`#4ade80` → `#010203`) prints `differ 1` and `SELFTEST FAIL`; restoring gives
`differ 0`

`-` `/config` dropped `model.max_output_tokens` — the redaction matched any key *containing* `token`, so a setting the
settings page displays was removed along with the secrets. The rule is now exact names (`token`, `secret`, `api_token`,
…) plus `_token`/`_secret`/`_password`/`_api_key`/`_passphrase` suffixes. Verified: the route returns
`max_output_tokens: 512` and still strips `security.network.api_token`. Found by a reviewer checking the design against
the tree, in code that had already been called done.

`#` `VERSION` (root and `athena-agent/`) reads `0.0.2`
`#` pending in this version: the website plan steps 2–9 (`documentation/proposals/website/design-r7.md`) — palette and
literal sweep, `ledger_view`, `/security/events`, `theme.js`, topbar and footer, `live.js` additions, `terminal.js`,
page specs, browser pass

---

## 0.0.1 — 2026-11-01 (released on `latest`)

### Phase 2 — local decode throughput (C3)
`+` `promote_layers()` in the local runtime — moves CPU decoder layers to the GPU **after** load, detaching each layer's accelerate hook first
`+` a **KV reserve** instead of a layer count — `ATHENA_PROMOTE_RESERVE_MB` (default 1600); a fixed 14 left 565 MiB and every generation 500'd
`+` `model.local.promote_layers` / `promote_reserve_mb` in config; `/health` reports `promotion: {promoted, total, requested}`
`+` `documentation/architecture/25-local-decode.md` — the two-budget bug, the measurements, the ceiling
`+` a self-explaining stop message — "1768 MiB free now, 264 MiB needed, would leave 1504 MiB (reserve 1600 MiB)"; the old text printed only the *before* figure, so a correct guard read like a bug

`-` the "hardware-bound" claim — decode 1.14 → **1.44 tok/s (+26 %)** by using the card the load workaround had given away
`-` the fiction that the planner and loader agreed — planner sized 14 layers at 90 % fill, loader admitted 5 at 45 %
`-` blind layer counts as a tuning knob — 14 promoted crashed generation with `CUDA out of memory … 565 MiB free`

`#` prefill also improved, 1.36 → 1.11 s (921-token prompt)
`#` 23 of 32 layers remain on CPU: a 4B in bf16 (8.46 GiB) plus KV does not fit an 8 GB card
`#` the boxed server picks it up because `providers/local.py` passes both settings to the runtime it spawns

### Phase 2 — the `athena-agent` distrobox
`+` `distrobox create --name athena-agent --image ubuntu:24.04 --nvidia` — the system-dependency layer, GPU passthrough verified (`RTX 3070 Ti, 615.78.08`)
`+` inside it: `build-essential`, `git`, `curl`, `python3-dev`, `python3-venv`, `libgl1`, `libglib2.0-0`
`+` `./athena.sh box <command>` — runs any athena command inside the box; refuses with the create-command if the box is missing
`+` `documentation/architecture/24-distrobox.md` — the rule, the recipe, the verification, what stays out

`-` system packages on the host — never again: Bazzite's rootfs is atomic and the box is disposable
`-` the assumption that a boxed server is a second environment — `$HOME` is shared, so venvs, models, profiles and secrets are the same files

`#` Python dependencies still install into the venvs on first run — that is what a venv is for
`#` `./athena.sh stop` from the host stops a boxed server *and* its boxed model runtime (shared PID namespace)
`#` the container stays `Up` when idle; it is an environment, not a process

### Phase 2 — prompt cost
`+` prefill/decode timing in the local runtime — `athena.prefill_s`, `decode_s`, `decode_tokens_per_s` per call, stamped by a logits processor at the first generated token

`-` the assumption that prefix reuse was the big local win — measured: ~0.7 s of a 4.7 s turn, against 3.06 s of decode and ~1.0 s of fixed per-call overhead
`-` the KV cache itself — **not built**: 15 % for a silent-corruption failure mode is a bad trade while decode dominates

`#` decode 1.29–1.31 tok/s (0.77 s/token) with 14 GPU / 18 CPU layers
`#` tiering (C3) is now the highest-value remaining item on the local path
`+` `flash-linear-attention` installed as a C3 probe — then **removed**: decode 1.31 → 1.12–1.30 tok/s (noise) and a 28.8 s Triton JIT on the first call

`-` the claim that missing optimized kernels explain 0.77 s/token — measured false at this placement: 32 of 37 layers run on CPU
`-` the "14 GPU / 18 CPU layers" figure — the plan artefact carries no layer counts; the runtime reports **5 GPU / 32 CPU**
`-` the assumption that the 4B is hardware-bound — 52 % of the card (4 254 of 8 192 MiB) sits idle because `budgets()` halves it to survive load-time warmup
`+` `documentation/architecture/23-prompt-cost.md` — where the 915 prompt tokens go, measured
`+` compacted instructions — `DEFAULT_INSTRUCTIONS` 125 → 82 tokens, `HIVE_INSTRUCTIONS` 118 → 74 (973 → 625 chars)
`+` an eleven-rule checklist for the rewrite — identity, persistence, honesty, tool discipline, conversation-not-a-tool, no false claims, mention syntax, queued-not-blocking, idle-vs-busy, only-listed-names, roster slot

`-` 101 billed-equivalent tokens per model call — saved (284 → 183 at calibration 1.167), paid again on every tool round
`-` the prose connectives in both instruction blocks — no rule removed, verified rule by rule
`-` a test that pinned the phrase "Peers right now" — it failed on wording rather than on the roster being dropped

`#` `*kisses*` still returns `*kisses back*` with 0 tool rounds
`#` the agent can still state the delegation rule: "Write `@name: <question>` in my reply — and pick an idle peer…"
`#` history left alone on purpose: ~60 % of it is 8 tokens of per-message framing, and every way to reduce it drops context

### Cards name the agent, the reason and the options
`+` install requests record the requesting lane (`request_install`/`request_tool_install`/`request_box_install` take `agent=`, passed by `plugin_stage` and `env_request` from the bound scope)
`+` the card shows `agent`, `reason`, `state` and an explicit `options:` line before the buttons — not just a sentence to infer from
`+` `provides:` is validated against what this build hosts (`tools`, `skills`); anything else is refused at install with the reason

`-` **an install card said "your queue"** — the queue never stored who staged it, so the one thing a user needs in order to judge it was missing
`-` **`provides: agents` was accepted and ignored** — a plugin could promise an agent lane and nothing would host one; the manifest now says what actually happens
`-` the reason rendered twice on the card: once as a field and once appended to the detail block

`#` a label with no value spans the row, so state-only lines do not hang in the value column

### Permissions manager — one queue for every question
`+` `core/permissions.py` — `PermissionManager`: kinds, blocking vs queued asks, grants (`once`/`session`/`permanent`), one store, one ledger trail, one queue
`+` `attach()` — `plugininstall`'s durable queue joins the same panel and the same resolve path without a second store
`+` `/permissions` (GET pending/answered/policy, POST resolve, POST `_revoke`); `/approvals` and `/installs` are now filtered views of the one queue
`+` `web/js/approvals.js` rewritten as a generic renderer: it renders `ask.actions`, so a card cannot offer an action the ask did not name
`+` `tests/permissions_check.py` (40) and `tests/run_gate.sh` (15 suites)

`-` **`policy()` deadlocked `/permissions`** — a non-reentrant lock taken twice; the first live request hung
`-` **`resolve()` removed the row before the waiter read the verdict** — every answered path ask looked like a timeout
`-` **install `detail` is a string** — the card rendered one character per line
`-` **`/permissions/{rid}` was declared before `/permissions/_revoke`** — "revoke" was captured as an id
`-` **`revoke()` never assigned the kept list** — it removed nothing, and left the `workspaces.json` mirror behind, so the folder stayed allowed after the grant was withdrawn

`#` blocking asks wait and time out into an *absence*; queued asks never block and never expire
`#` a permanent path grant is a folder, and revoking clears both the authority and the mirror
`#` live-verified: card render, `Once`, `Always` (+mirror), revoke (+mirror), tool install through the card, uninstall through the card

### Orphaned credential proxies (and a ledger that could be flooded)
`+` `credproxy._die_with_parent()` — the proxy exits when the server that spawned it is gone (parent-change watchdog)
`+` `athena.sh stop` sweeps `core.credproxy` as well as the server
`+` `Ledger.record()` folds identical events (one per key per 60 s, with a `suppressed` count), emits a summary every 100, and rotates its older half over 2 MB
`+` `security.SKIP_PARTS` gains `.venv` / `venv` / `.mypy_cache` / `site-packages` — a plugin venv is generated, not installed
`+` `plugininstall` prunes requests whose staged copy is gone; tool packs gained `uninstall_tool()`

`-` **five orphaned `credproxy` processes** were alive while the server was down, each still holding provider keys in memory. The proxy is a plain child process; nothing tied its life to the server's.
`-` `stop` killed the server and left the proxy — so every restart accumulated one
`-` the security baseline hashed plugin venvs (904 files for one plugin), so a `pip` upgrade would have read as tampering
`-` the ledger recorded **2,044 `inbound-401` rows** from a stale browser tab polling without a token, burying the real signal; the noisy file is archived at `security/archive/events_2026-11-01_1420-401storm.jsonl` (2,195 rows, not deleted)
`-` requests whose staged folder had been cleaned up kept re-opening a dialog that could only offer Cancel

`#` verified lifecycle: start → 1 proxy, health `sidecar`; stop → 0 proxies, port 8765 free
`#` a watchdog that used `time` without importing it would have died silently — caught before shipping

### Environment requests — tool packs and box packages on the same card
`+` `plugininstall.install_tool()` / `request_tool_install()` — a staged TOOL.md pack installs into `.athena/tools/`, gains a `tools.packs` line, is hashed, and is loaded to verify
`+` `plugininstall.install_box()` / `request_box_install()` — system packages via `sudo -n apt-get install --no-install-recommends`, name-validated, dry-run capable, every attempt appended to `security/box_installs.json`
`+` `tools/envrequest.py` — `env_request(kind="tool"|"box")`: the agent asks; the server installs on the user's click
`+` the card distinguishes the three kinds, and the box card carries its warning ("affects every agent, not undone by removing a plugin")
`+` `tests/tool_install_check.py` (20) and `tests/box_request_check.py` (19)

`-` **tool packs had no card** — an agent could prepare a TOOL.md pack and still have to tell the user "edit tools.packs", which is what produced "there is no way to install graphify"
`-` `security.allow_box_installs: false` by default: a package is shared by every plugin and the server, so it cannot be something one artefact asks for and gets
`-` package names are names only — `cowsay; rm -rf /`, `--option`, `/usr/bin/evil` and spaces are all refused before apt is touched

`#` three kinds, one queue, one card: plugin / tool / box — each with the actions its state allows
`#` box changes are recorded but not rolled back by uninstalling a plugin, and the card says so

### graphify is a tool pack, not an impossibility
`+` `.athena/tools/graphify/` — `TOOL.md` (one `action` parameter: explain | affected | path | hubs | query) + `scripts/graphify.py`, enabled via `tools.packs`
`+` instruction §3 — "a tool that is already on the machine" is a first-class shape: expose it, do not refuse it
`+` "there is no way to install this" is almost never true; the four honest answers are now listed, with "say what you checked" as the fallback

`-` **a false refusal**: an agent said graphify could not be installed as a plugin. Graphify is an installed CLI (uv tool, 0.9.32); its shape is a tool pack, which needs no repository, no entry point and no dependency.
`-` the shape catalogue only described repository-shaped things, so "installed CLI" had no home in the procedure

`#` live: the agent called `graphify` and answered with the top three hubs (ChannelManager 76 edges, the version node 52, Tool 43) plus an explanation, all read-only off `~/.athena/graphify/graph.json`
`#` remaining gap, stated: tool packs have **no install card** — only plugins do. The agent prepares the folder and the `tools.packs` line; the user enables it.

### Shared stack and lockfiles — share the libraries, pin the versions
`+` `.pth` seed — each plugin venv points at the server's `site-packages`, so shared libraries are visible read-only (13 MB plugin venv vs 37 MB server, no duplication)
`+` `requirements.lock` — if the staged plugin ships one it is installed **from the lock only**; otherwise the resolved freeze is written back as the lock, so the first install records what happened
`+` unsatisfiable pin → install fails and rolls back, rather than drifting to another version
`+` the card and `plugins install` report the lock ("pinned by requirements.lock — N packages, sha …") and the shared stack path
`+` `tests/plugin_lock_check.py` — 21 checks, including six==1.16.0 installed while 1.17.0 is current

`-` `requires:` alone resolved to whatever was newest at install time — the same manifest could install different code a week apart
`-` the venv-only choice duplicated the shared stack; the seed removes that objection without letting plugins mutate it

`#` sharing is read-only by construction: the plugin can import the server's `httpx`, and installing into the server's venv is still impossible (read-only root, and the installer never targets it)
`#` the recorded cost: a plugin now depends on the server's versions of shared libraries, which is why the seed path is in the install record and the lock pins what the plugin itself brought

### Per-plugin dependencies — declared, never executed
`+` `PLUGIN.md` may carry `requires: [...]`; `plugin_stage` takes `requires=` and writes it into the draft manifest
`+` installer builds `.athena/plugins/<name>/.venv` on the user's click, pip-installs there, records the `pip freeze` in `security/installs.json`
`+` the plugin child runs on **that** interpreter (`plugins._sandbox_argv`), still under bwrap with a read-only root
`+` a dependency that cannot be installed rolls back and disables, with the pip error in the result
`+` the install card lists the declared dependencies before you click; `plugins install` prints the package count
`+` `tests/plugin_deps_check.py` — 17 checks, including "the server's venv lacks it, the child has it"

`-` **the shared venv stayed read-only, deliberately.** Verified: `touch .venv/PROBE` → EROFS, `pip install --user` → EROFS on the profile, no `apt-get` in the agent's shell. An agent that can install packages can patch the guard at import time.
`-` a repo needing a library used to be uninstallable — either "cannot" or a broken plugin; now it is a declared requirement and a per-plugin venv.

`#` dependencies are per plugin, not per architecture: one repo cannot upgrade a library another depends on
`#` the shell's `python3` is 3.14 (host), the server's venv is separate — so where a package lands matters, and the venv makes it unambiguous

### Intake is a judgement the agent makes
`*` `github_install.md` §3 — reframed from "six questions" to **a verdict the agent reaches**, with the shape catalogue (skills bundle / tool plugin / tool pack / data pack) as reference material rather than a classification table
`+` the adapter path is explicit: an unshaped repo becomes a `TOOL.md` wrapping a CLI or a `scripts/plugin.py` importing a library, written by the agent and staged with the files it needs
`+` §0b states the split: "whether this repository can become a plugin is your judgement, not this document's" — stage if you can make it work, say what it needs if you cannot

`-` the document read as if shapes were predetermined, which invited either forcing a library into a plugin or refusing something adaptable
`#` the rails remain outside the judgement: run nothing from the clone, install only on the user's click, never write ROOT, stop on a red flag

### An installed plugin is visible without a restart
`+` `ChannelManager.refresh_config()` — `config.yaml` is re-read when its mtime changes, so a plugin enabled while the server runs is live on the next turn
`+` skills-only plugins load — `provides: skills` with no entry reports `isolation: none (skills bundle)` and lists its skills instead of failing
`+` YAML block scalars in skill frontmatter — `description: >-` folds its indented lines into one line

`-` **an installed plugin that could not be seen**: the running server held a boot-time config, so `plugins.enabled` gained a line nobody read. Restarting "fixed" it, which is not a fix.
`-` **a skills bundle rolled itself back**: the loader treated the absent `scripts/plugin.py` as broken, so `egm` installed, failed its smoke test as "missing", and was disabled again — the user saw "staged/installed" and the agent saw nothing
`-` **`description: >-` was parsed as the literal string** for any skill written with a folded description; the agent's index said `- egm-draft: >-`

`#` live: after the fixes, `plugins install egm` → `smoke: loaded=True isolation=none (skills bundle)`, `enabled: [clock, egm]`, and the agent answered *"Yes — it's live now. Ten skills total, seven of them from the plugin"* with their real descriptions

### A repository URL stages itself
`+` **regex triggers** — `re:https?://(www\.)?(github|gitlab)\.com/…`, `re:https?://\S+\.git\b`, `re:git@\S+\.git\b`; prose that merely mentions GitHub no longer fires
`+` instruction §0b — a repository link means: inspect → red-flag gate → **stage** → the card is the question; no second "shall I stage it?" turn
`+` **plugins may provide skills** — `provides: skills` needs no entry script; `core/skills.py` discovers `plugins/<name>/skills/` after profile and shared skills
`+` `plugin_stage` derives the shape from the contents (skills bundle vs tool) instead of demanding `scripts/plugin.py`
`+` `tests/instruction_docs_check.py` — regex precision (a URL fires, prose does not)

`-` `entry` was required unconditionally, so a documentation-shaped repository (skills, no script) could never be staged — the exact shape of repos like EGM
`-` the multi-line trigger list was silently truncated by the frontmatter parser (only the first two survived); wrapped lists are joined now

`#` live: the message "can we use this? https://github.com/google/elephant-goldfish-model" — nothing else — produced an inspected, staged `egm` (39 files, `provides: skills`) and a card offering **Install / Cancel**
`#` the agent flagged its own gap unprompted: only `skills/` was staged, the `agents/` personas were missing, so the review skills would point at absent files

### The install prompt — the user is asked, the server installs
`+` `plugininstall.request_install()` / `pending_installs()` / `resolve_install()` — a durable queue in `security/installs/pending.json`
`+` `GET /installs` + `POST /installs/{id}` — what the interface polls and answers
`+` popup card in `web/js/approvals.js` — name, file count, source, entry, reason, **Deny / Install**; Esc denies, a reload does not
`+` `plugin_stage` now **asks**: staging queues a request and returns `status: awaiting the user's approval`
`+` `tests/install_request_check.py` — 21 checks across pending / denied / installed / vanished-stage

`-` **a request that survived its own completion** — the first UI click installed the plugin but left the request pending, so every retry said "already exists" and the card never closed. `resolve_install` now claims (pops and saves) the request *before* installing, and returns `decision: failed` with the reason.
`-` `beforeunload` denying install requests — refreshing a tab silently cancelled the agent's work. Only permission prompts (which block a turn) are denied on unload now.
`-` a request is validated when created, so a stage with no manifest never becomes a prompt

`#` live in a browser: card rendered for `demo-ping` (2 files, source, entry), Install clicked, `plugins/demo-ping` present + `enabled: [clock, demo-ping]` + `demo_ping` in the toolset + `pending 0`; then removed cleanly
`#` a backgrounded tab does not poll (`document.hidden`) — correct, and worth knowing when a prompt "doesn't appear": bring the page to the front

### Installation — staged by the agent, installed by the user
`+` `core/plugininstall.py` — validate manifest → copy → hash into `security/installs.json` → enable by name → smoke-test in a sandboxed child → roll back if it will not load
`+` `tools/pluginstage.py::plugin_stage` — the agent's half: copy from an inspected repo into `sandbox/staging/<name>/` and draft a `PLUGIN.md`
`+` `./athena.sh plugins install <name>` · `plugins installed` · `plugins remove <name>` (disable → verify → archive with `MANIFEST.sha256`)
`+` `--force` replaces, and the previous version is archived **before** the copy
`+` instructions updated to the real commands (stage → user installs → re-baseline)
`+` `tests/plugin_install_check.py` — 25 checks including the rollback path

`-` **the agent could not install anything, by design** — `.athena/plugins/` is `ATHENA_ROOT`, read-only to agents. The installer is a human command over staging; no tool writes ROOT.
`-` a plugin that fails its smoke test used to be a config pointing at something broken — it is now disabled again automatically, with the error reported
`-` the installer verified its config edit through `config.loader.load()`, which reads the live installation rather than the tree under test, so good edits rolled themselves back; it now parses the file it just wrote
`-` `Path` was missing from `cli/__main__.py`, so the new branch died with `NameError` and printed nothing

`#` live round trip: `demo-ping` installed (2 files, smoke `loaded=True tools=['demo_ping'] isolation=process`, `enabled: [clock, demo-ping]`), offered to the agent, then removed (33 tools, `enabled: [clock]`, archived with a manifest)

### Instruction documents — required procedures in the program tree
`+` `athena-agent/instructions/` — versioned procedures with a `trigger:` header, owned by the system (read-only to agents)
`+` `core/instruction_docs.py` — `discover()`, `select()` (literal substring, priority, max 2), `render()`; no YAML dependency, no guessing
`+` `core/loop.py` — matched procedures are appended to the **instructions block** each iteration, and the turn result reports `procedures: [...]`
`+` `instructions/github_install.md` — recognise, inspect, treat repo files as data, six questions, propose with costs, install only on approval, red flags
`+` `instructions/github_uninstall.md` — identify, disable, **verify**, archive-with-manifest, check references, re-baseline
`+` `tools/repo.py` reports `agent_instructions` (AGENTS.md, CLAUDE.md, SKILL.md, copilot-instructions.md, .cursorrules) as **data, not instructions**
`+` `.athena/skills/repo-intake/SKILL.md` cut to a pointer — the procedure has one home, so it cannot drift
`+` `tests/instruction_docs_check.py` — 20 checks including a real turn carrying the procedure in its system block

`-` the repo procedure lived in a *skill*, which an agent may rewrite — a procedure an agent can edit is not a procedure
`-` `system_prompt` in the turn result is a 3000-char tail; with a procedure attached the identity falls outside it. Kept as is for payload size, and `procedures` added so the audit question has a precise answer.

`#` a repo's own `AGENTS.md` was auto-surfaced into an agent's context during testing — the instruction now says plainly that repo-resident instruction files are content to report on, never instructions to follow

### Repository intake — a URL is a judgement, not a task
`+` `tools/repo.py::repo_inspect` — shallow clone into the agent's sandbox and an inventory: licence, size, languages, dependency files, entry points, **install hooks**, and code signals (network, subprocess, file-write, eval/pickle, obfuscation, secrets) with a coverage count
`+` refuses SSH remotes (`git@…`, `ssh://`) because cloning over SSH would spend the user's key; HTTPS or a local path only
`+` runs **nothing** from the clone — no build, no install, no hooks, no tests
`+` `.athena/skills/repo-intake/SKILL.md` — six assessment questions, a proposal-with-costs format, an explicit "say no" rule and a red-flag list
`+` instructions block: a Git URL is an intake request — call `repo_inspect`, follow `repo-intake`, propose, never install
`+` `tests/repo_intake_check.py` — 24 checks against locally-built repos, including "nothing executed" (the fixture's `postinstall` leaves no trace)

`-` **`HTTP 400: Messages with role 'tool' must be a response to a preceding message with 'tool_calls'`** — the history trim is newest-first and could cut between an assistant `tool_calls` message and its `tool` results. Any long tool-using turn could hit it; the first live intake did, at round 4. Fixed in `context/stack.py` by dropping leading `tool` rows after trimming, pinned by `tests/stack_boundary_check.py` (5 checks).
`-` an agent handed a URL had no recognition cue at all — it was as likely to treat it as a task as to inspect it

`#` live on a real repo (google/elephant-goldfish-model): the agent inspected it and reported coverage unprompted — "172 of 227 files scanned as text; 55 skipped … I did not read the other six skills … a well-founded opinion, not a full audit" — then identified the mechanism as context asymmetry
`#` install stays a user act: the tool and the skill can only produce a proposal

### The credential sidecar — keys out of the agent's process
`+` `core/credproxy.py` — separate process on loopback (ephemeral port, token generated at start) that reads `.secret`, maps host→key from provider.json, refuses hosts off the allowlist, and injects `Authorization` itself
`+` `providers/base.py::PROXY` + `openai_compat.build_request` — in sidecar mode the adapter sends `X-Athena-Upstream` and `X-Athena-Proxy`, and **no key at all**
`+` boot wiring — `security.credentials.mode: sidecar` (default) starts the proxy and reports it in `/health`
`+` `tests/credential_sidecar_check.py` — 13 checks, including patching `api_key` to raise and still completing a call

`-` **the server process holding keys** — it no longer does. A leak from a tool, a plugin, a turn-loop bug or a printed traceback can no longer contain a provider key.
`-` the proxy's host→key map kept the port while the request side compared bare hosts (`127.0.0.1:8080` vs `127.0.0.1`), so it answered `no key ... (looked for any)`; both sides normalised, error names the known hosts
`-` the claim "`.secret` is readable by the server" is retired: `/health` now reports `credentials: {mode: sidecar}`

`#` what it does not buy: the proxy runs as the same user and `.secret` is that user's file — the boundary moved from "the agent's code" to "the credential", not from "the user" to "the user". OS isolation would be the next step, and it needs an account the agent does not control.
`#` the allowlist (`security.network.allow`) is now the list of places keys may be spent — treat edits to it as decisions

### The three open holes — two closed, one shrunk
`+` **plugins out of process (default)** — `core/plugin_runner.py` hosts one plugin in a child under bwrap with read-only root and credential masks; the parent registers proxy tools and speaks JSON lines
`+` **API token on the door** — `security/api_token` (0600, auto-generated), required on everything except `/health` and the static shell; the shell hands the browser an HttpOnly cookie so the UI needs no change
`+` `tests/plugin_isolation_check.py` — 11 checks: patch, secret read and program write all fail *in the child*, benign tool works over RPC, in-process mode still detects tampering
`+` token tests — loopback needs the token, header and cookie both open the door, wrong token refused, shell public + sets cookie, `/health` public, no cookie on API responses, token stable, 0600, rotatable

`-` **the identity snapshot was the only thing standing between a plugin and the guard** — with isolation on, the child patching `Scope.check` affects nothing, because it patched its own copy
`-` **the source scan was rejecting harmless plugins** — with a read-only sandbox, `open()` fails on its own; the scan now applies only to `isolation: inprocess`
`-` `PluginError` and the `Tool` import had been deleted from `core/plugins.py` by an earlier slice edit — the loader failed with `NameError` until restored. A reminder that a big in-place rewrite is not a safe operation.
`-` the child could not import `tools` (`sys.path[0]` was `core/`); the runner now puts the project root back

`#` **`.secret` for plugins is closed; for the server process it is not.** After isolation the only holder is the server itself, which signs provider requests. A credential sidecar (proxy on a unix socket that owns the keys and forwards) is the remaining step — deliberately not rushed in the same pass as two other security changes.
`#` curl without the token now gets 401 on `/agents`: the UI is unaffected because the browser holds the cookie

### Subagents — bounded workers with a trace
`+` `core/subagents.py` — `spawn()` runs one bounded turn in a re-rooted scope and leaves `ARTIFACT.md` + `state.db` + its own session
`+` `tools/subagents.py` — `spawn_subagent(task, max_turns)`, registered for agents and **discarded** from a worker's registry
`+` narrow scope — child home is the trace folder, no inherited workspaces: it cannot write the parent's memories, workspace or the program
`+` budget — turn cap (6 default, 24 ceiling), `$0.05`, 300 s; recorded per run in sqlite
`+` `tests/subagents_check.py` — 23 checks including the failure trace carrying the real provider error
`+` live: `default` spawned a worker (`max_turns: 3`), 2 turns, `$0.0051`, trace + artifact + state row on disk

`-` the layout promised a `subagents/` folder with nothing behind it — one hand-made example and no mechanism. Now there is a tool, a scope, a budget and a trace.
`-` `budget.used_turns` (does not exist — it is `turns`): the wrong attribute raised inside the success path and the artifact reported *my* AttributeError instead of the worker's failure
`-` tokens recorded as `0 in / 0 out` while cost was real — the loop exposes them under `turn["usage"]`
`-` terminal masks widened: docker/npm/pypi/cargo/kube/gcloud credential files, gh and gcloud dirs, browser profiles
`-` a bind mount inside a writable tree is now refused by comparing `st_dev` with the profile's — no new variable

### Network defence — blue at the door, red at the socket
`+` `core/netdefense.py::InboundGuard` — ASGI middleware: non-loopback clients get 403 on local-only paths, 401 without the configured token, 429 over the rate limit; every refusal lands in the ledger as a **blue** event
`+` `core/netdefense.py::outbound_findings` — established sockets the process owns, compared to `security.network.allow`; an unexpected destination is a **red** finding with guidance
`+` `security.network` config — `loopback_only`, `rate_per_minute`, `api_token`, `audit_outbound`, `allow`
`+` `tests/network_defense_check.py` — 18 checks

`-` **token check nested inside the loopback branch** — with `loopback_only: false` the token requirement vanished entirely. Caught by the test, not by reading it.
`-` **blaming the architecture for the whole machine** — the first outbound audit reported 21 connections (GitHub, Telegram, a LAN host on :8009) because `/proc/net/tcp` is the container's shared network namespace. Now filtered to this process's own `socket:[inode]` entries: 0 false positives.
`-` a local-only path being reachable from a remote client if the bind address ever changed — the middleware refuses it regardless of bind

`#` loopback is the posture, not a promise: `127.0.0.1` today, refused at the door tomorrow if someone binds 0.0.0.0
`#` the door is the only place with a veto; everything else on the network side is evidence, reported through the same red/blue report the disk watchdog writes

### Red team / blue team watchdogs — tampering from inside vs outside
`+` `core/security.py` — `Baseline` (sha256/size/mode of the critical set), `Ledger` (append-only evidence from the guards), `check()` (diff + classification), `report()` (markdown with guidance), `SecurityWatch` (daemon thread, 300 s)
`+` **attribution by evidence**: a changed path is *red team* only when the ledger shows an internal attempt on it, otherwise *blue team* — same edit, different blame, and it says which and why
`+` guidance per finding, aimed at the agent that reads it: red → "do not retry, do not repair, tell the user"; blue → "do not overwrite it to 'restore' it, report the path"
`+` agents are told three ways without waking a model: one line in the instructions block, the `security_report` tool (read-only), and `GET /security` + `GET /security/report`
`+` `./athena.sh security [baseline|check|report]` — re-baselining is a **user** action; the tool deliberately cannot
`+` `tests/security_check.py` — 20 checks: outside edit → blue/critical; same edit after a refusal → red; dropped plugin → blue; deletion → critical; permission change → high; report names both teams and gives advice; re-baseline clears

`-` **a change with no observer** — before this, a file could be edited on disk and nothing anywhere would notice
`-` blaming an agent for everything — attribution requires a recorded refusal, so an outside edit is not mistaken for an insider one
`-` letting an agent silence the alarm — `security_report` is read-only, so tampering cannot erase the evidence of itself

`#` the critical set excludes profiles, sessions, metrics and logs: a watchdog that flags an agent's own memory as tampering gets ignored within a day
`#` the honest limit is written down: attribution is evidence, not proof — an outsider who never triggers a refusal is indistinguishable from one who does

### Red team — a hostile plugin, and the hole it found in the defence
`+` `tests/redteam/` — five hostile plugins (trojan 1–5) and `tests/redteam_run.py`, which measures *outcomes* (did the program change, did the secret come back) rather than exit codes
`+` source scan before import — `FORBIDDEN` patterns: `import core`, `from tools import`, `sys.modules`, `__import__`/`importlib`, `open(`, `subprocess`, `socket`, `ctypes`, `setattr(`, `eval(`, `exec(`; a plugin adds logic, I/O belongs in a contained tool
`+` module-wide snapshot replaces the name list — every public attribute of the guard modules and the `Scope`/`ApprovalBroker`/`Registry`/`Context` classes, keyed by `(module, owner, attribute)`
`+` functional probe after restore — the guard must still refuse a write into `ATHENA_SYSTEM`, or the plugin is rejected

`-` **the first hostile plugin got through three ways**: it neutered `filesystem.Context.check` (file tool then overwrote the program), read `.secret` and returned the value, and appended to `loop.py` directly
`-` **the first hardening rejected without restoring.** The snapshot was keyed by dotted strings; `core.paths.Scope.check` has four parts, so those keys were skipped in restore. Result: `loaded: []` and `program intact: False` — a refusal that did not refuse. This is the worst class of bug in a guard, and only the outcome-based test showed it.
`-` a hand-written watch list also missed `core.paths.Path`, whose replacement made every path check permissive while the plugin was "rejected"

`#` all five attackers are now blocked; trojan 5 evades the scanner via `getattr(sys, "modules")` and is caught by the snapshot — `core.paths.Scope.check — restored, plugin rejected`, guard still refuses, program intact
`#` the honest limit stands: in-process code cannot be prevented, only checked afterwards. Out-of-process plugins over a typed RPC boundary remain the structural fix.

### Plugins — additive enforced, not assumed
`+` integrity snapshot — the identity of `Scope.check`, `Scope._flags`, `ApprovalBroker.request` and the registry's `invoke`/`register`/`bind` is recorded **before any plugin code runs** and compared after `register()` returns
`+` tamper detection with repair — a rebound function is put back, the plugin is rejected, and every tool it added is rolled back (`Registry.discard`)
`+` violation is reported by name: `tampered with the system layer: core.paths.Scope.check — restored, plugin rejected`
`+` `tests/plugins_safety_check.py` — 16 checks: benign plugin loads; shadowing a built-in refused; patching `check`/`_flags`/`request` detected, restored, rolled back; guard still refuses afterwards

`-` **the assumption that "in-process plugin" meant "additive plugin"** — nothing stopped a plugin rebinding `Scope.check` to always allow. It does now, for the guard and the registry.
`-` silent success for a shadowing plugin — `register()` already raised on duplicate names, but the plugin loader swallowed nothing: it reported it. Both paths verified.

`#` still open, and documented as such: a plugin can read `.secret`, write anywhere the server can, run processes, and patch something off the watch list. "Additive" is enforced for the guard and the registry, not in general — the structural fix is out-of-process plugins over RPC (doc 30).

### terminal — reads masked, not just writes refused
`+` credential masking inside the sandbox — `.athena/.secret`, `profiles/*/.secret`, `~/.hermes/.env`, `~/.netrc`, `~/.git-credentials`, gh hosts, `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.password-store`, kwallet dirs
`+` masks bind an **empty regular file** (files) or an empty `--tmpfs` (dirs); `/dev/null` made reads fail with `EACCES` and looked like a permissions bug
`+` `XDG_CONFIG_HOME`/`CACHE`/`DATA`/`STATE` point inside the sandbox, so commands stop printing a bwrap errno line
`+` tests: secret reads as zero bytes, `~/.ssh` empty, real secret unchanged, output clean

`-` **the assumption that a read-only root is enough** — it stops writes and says nothing about reads. `wc -c < .athena/.secret` returned `53` and the key *name* was readable; had the value been needed, `cat` would have put it in the transcript. This was a real hole, found by testing rather than by reasoning.

`#` verified on the live install: `/etc` write refused, program append refused, `.bashrc` append refused — while `echo ok > proof.txt` in the sandbox works; `loop.py` and `.bashrc` byte-identical afterwards
`#` masking holds against the container's host mount too (`/run/host/home/…`, `/var/home/…`)

### computer — window control that actually works
`+` `window_minimize(match)` / `window_restore(match)` — KWin scripting over `org.kde.KWin.Scripting`, acting on windows by class or caption instead of clicking coordinates
`+` `press_key` translates names to Linux key codes (`ctrl+alt+t` → `29:1 56:1 20:1 …`); unknown names are refused instead of silently ignored
`+` `mouse_click` uses real BTN codes (`0x110`/`0x111`/`0x112`) — `0x40` was never a button
`+` unique KWin script names per call — KWin returns the existing id for a path it already loaded, so a reused filename re-ran the previous script

`-` **`ydotool mousemove --absolute X Y`** — accepted, exit 0, no movement, no error. The documented flags are `-a -x <n> -y <n>`; even those did not move the pointer on this KWin/Plasma build. Pointer movement is therefore **unverified and effectively broken** here, and the tool no longer pretends otherwise.
`-` `ydotool key super` — silently does nothing; `125:1 125:0` opens the KDE launcher. Names are translated now.
`-` my own bad click code `0x40`, and my assumption that a hover highlight would appear in a screenshot (it did not, so a click that navigated nowhere told me nothing)

`#` **verified by outcome, not by exit code**: `window_minimize("dolphin")` removed Dolphin from the left monitor (pixel hash of that region changed, Discord revealed underneath); `window_restore("dolphin")` brought it back. The user's own request — minimise the folder — is what proved the path.
`#` a keypress test opened a terminal (`ctrl+alt+t`) on the user's desktop: side effects are real, which is why `computer` stays off by default

### computer — the two bridges it needs
`+` `distrobox-host-exec` bridging — inside the box the tool proxies each call to the host, where the display session and the GUI binaries actually are
`+` compositor-aware capture — `spectacle` on KDE (KWin has no `wlr-screencopy`), `grim` on other Wayland, `import` on X11
`+` actionable input failure — when `ydotool` exits 2 because `ydotoold` is down, the result carries the reason and the command to fix it
`+` `screen_capabilities` now reports `compositor` and `bridge` alongside the tools it found

`-` the assumption that installing `grim` makes capture work — on KDE it installs, runs, and fails with an empty error because the protocol is absent. Evidence beats expectation: `spectacle` captured 849 KB through the host bridge, `grim` exited 1 with no message.
`-` reading pointer movement back through `xdotool` — XWayland shows the X cursor, not the native one, so a successful `ydotool` call looked like a no-op. The call path is proven (exit 0 with the daemon up); the visual result needs a human eye once.

`#` `ydotoold` ships **no systemd unit** — enabling input is a deliberate user-service step, not a package default
`#` browser is genuinely absent: no chromium, no firefox, no playwright anywhere on the box or the host

### The system category — filesystem, terminal, computer
`+` `.athena/tools/system/{filesystem,terminal,computer}/TOOL.md` — a required category: always loaded, never gated by `tools.packs`
`+` `kind: builtin` + `impl:` — the descriptor describes the tool, the code stays in `athena-agent/tools/` where the guard can see it; a descriptor never contains a path to run
`+` `tools/terminal.py` — `bash -lc` inside **bubblewrap**: read-only root, only sandbox/workspace/granted writable, `--unshare-pid`, optional `--unshare-net`
`+` `tools/computer.py` — screen_capabilities, screenshot, mouse_move, mouse_click, type_text, press_key, browser; every verb detects its tool and reports what is missing
`+` `tools/system.py` — the impl catalogue (`filesystem`, `terminal`, `computer`), deduped against what is already registered
`+` `tests/system_tools_check.py` — 19 checks

`-` **a shell that could write to the program** — a shell makes path permission meaningless, so containment had to be the kernel's: `echo pwned >> athena-agent/core/loop.py` now fails with `Read-only file system`, and the program's sha256 is unchanged
`-` **degrading silently** — if `bwrap` is missing the tool refuses instead of running uncontained; `require_containment: false` is a deliberate choice, not a fallback
`-` **`computer` on by default** — it moves the user's real pointer, so it starts `enabled: false`
`-` `tools.terminal` / `tools.computer` being unknown to the config validator — added to `defaults.yaml`, `/health` warnings now none

`#` **the box mattered**: `bwrap` was on the host but not inside the `athena-agent` distrobox, so the first live turn refused correctly and said why. Installing bubblewrap in the box made namespaces work inside the container too.
`#` live: one tool round, `echo ATHENA-OK; whoami; pwd` → three lines of output, `cwd` = `profiles/default/sandbox`, `contained: true`
`#` `computer` on this box: Wayland, `ydotool`/`xdotool`/`import` on the host, **no grim, no browser, no playwright** — present, honest and inert until installed

### `references/` and `assets/` — added information vs runtime data
`+` `core/library.py` — `inventory()`, `list_for()`, `read()`: references are found across **skills, tools and plugins**, profile first
`+` `tools/library.py` — `reference_list()` (what extra documentation exists) and `reference_read(layer, name, path)` (open one)
`+` `references/` on every layer: `skill/ SKILL.md + references/` · `tool/ TOOL.md + scripts/ + references/` · `plugin/ PLUGIN.md + scripts/ + references/ + assets/`
`+` real examples: `tools/disk-usage/references/df-output.md` (why `Use%` disagrees with free bytes), `plugins/clock/references/time-zones.md` (why `epoch` is the only unambiguous field), `skills/sandbox-testing/references/example-loop.md`
`+` `skill_read` now returns the skill's `references/` list; `./athena.sh packs|plugins` show how many each carries
`+` `tests/references_check.py` — 19 checks

`-` **a reference reader becoming a file reader** — `reference_read("tool","x","../../SKILL.md")`, an absolute path, and a sibling layer are all refused; the path must resolve inside that capability's own `references/`
`-` treating assets as references — a template the code fills in is not reading material; `assets/` is listed separately and never offered as a document
`-` another `or True` in my own test (`scripts_listed_separately`) — replaced with a real fixture and a real equality

`#` live: the agent called `reference_list`, then `reference_read` on `tools/disk-usage/references/df-output.md`, and answered *"`df`'s `Use%` is `Used / (Used + Avail)`, not `Used / Size` — the denominator excludes reserved-and-unusable blocks, so 867G used against 57G available reads as 94% even though 57G is genuinely free"*. That sentence is in the reference, not in the prompt.

### The four layers — descriptors and scripts, not inline YAML
`+` **Tools** are now `.athena/tools/<name>/TOOL.md` + `scripts/` — the descriptor says what the verb is, the scripts folder holds the code that runs it (python, bash, anything with an interpreter)
`+` `kind: script` — arguments arrive as JSON in `argv[1]` and `ATHENA_ARGS`, `cwd` is the agent's `sandbox/`, the five `ATHENA_*` variables are exported; the interpreter follows the extension (`.py` → python3, `.sh` → bash, `.js` → node)
`+` **Plugins** are now `.athena/plugins/<name>/PLUGIN.md` + `scripts/` + `assets/`, with `entry:` naming the script that exposes `register(registry, ctx)`
`+` `disk-usage` rewritten as a real script (`scripts/disk_usage.py`) with a TOOL.md that explains when to use it; `clock` moved to `scripts/plugin.py` with a PLUGIN.md
`+` doc 28 rewritten around the layering: System → Plugins → Tools → Skills, power decreasing outward, monitoring running inward

`-` `tool.yaml` — a descriptor with the command buried inside it. A tool that needs logic had nowhere to put it, so the format invited one-line shell commands. `TOOL.md` + `scripts/` is the shape that scales.
`-` `plugin.py` at the folder root — code with no description beside it. `PLUGIN.md` now says what the plugin is and which script is the entry point.
`-` my first `placeholders` parser: it kept the quotes in quoted list items and collapsed every parameter field onto one key. The test caught both; the format is now frontmatter with JSON values, which removes the class of bug.

`#` a folder is still not permission: `tools.packs` and `plugins.enabled` are the lines that authorise
`#` live: one turn answered with a script-backed tool and a plugin together — "56.9 GiB free of 929.9 GiB (93% used), and the current time is Sunday, 2026-11-01 at 11:32:14 EST"

### Tool packs — and the self-install door that was open
`+` `.athena/tools/<name>/tool.yaml` — a named verb as a file: `kind: command` (argv, no shell, cwd in sandbox) or `kind: http`; enabled under `tools.packs`
`+` `core/toolpacks.py` — a forty-line manifest reader (no YAML dependency), discovery in the shared and profile trees, broken packs reported not raised
`+` `.athena/tools/` + `README.md` + a working `disk-usage` pack (`df -h`)
`+` `./athena.sh packs` · `./athena.sh tools` now labels each tool `builtin | fs | skill | plugin | toolpack`
`+` `tests/toolpacks_check.py` — 20 checks: discovery, enable gate, command pack runs with no shell (`hello; rm -rf /` came back as text), http without a url fails as data, one bad pack does not stop others

`-` **the agent could write its own `plugins/` and `config.yaml`** — author a plugin, add its name to `plugins.enabled`, and it has extended itself while `athena-agent/` stayed pristine. Both are now read+list only (with `state.json`), and the refusal names the line instead of blaming the program tree
`-` `.athena/tools/` meaning "model runtime" — it means tools now; the runtime is `.athena/model-runtime/`
`-` three tautological assertions I wrote in my own test (`and False or True`, `is False or True`, a misnamed check) — replaced with real ones before the run
`-` `root_write_refused` and `dotdot_traversal_refused` pinning the old, wrong message — one of them had been passing *because* every partial refusal printed the same string

`#` live: the `default` agent was asked for free space and answered "has 57G free out of 930G, so the filesystem is 94% full" — through the pack, not through anything built into the program

### Tools, skills and plugins — three ways to gain a capability
`+` `core/skills.py` — discovery from `.athena/skills/` and `profiles/<lane>/skills/`, profile shadows shared, frontmatter parsed without a YAML dependency
`+` `core/skills.py::index()` — one line per skill, appended to the instructions block: a library of fifty skills costs fifty lines, not fifty documents
`+` `core/plugins.py` — `plugin.py` exposing `register(registry, ctx)`, loaded only when named in `plugins.enabled`, broken ones skipped and reported instead of raised
`+` `tools/skills.py` — `skill_list` (cheap) and `skill_read` (opens one, strips frontmatter, lists siblings)
`+` `.athena/skills/` — two real skills: `sandbox-testing`, `extending-yourself`
`+` `.athena/plugins/` — `README.md` plus a working `clock` plugin registering `now`
`+` `./athena.sh tools|skills|plugins` — what one turn is offered and where each tool comes from; previously only answerable by reading the code
`+` `tests/tools_skills_plugins.py` — 23 checks including a tautology I caught in my own assertion (`or True`) and made real

`-` `.athena/tools/` holding a 5.1 GB model-serving venv — renamed `.athena/model-runtime/`; the name `tools/` now means tools
`-` `model.local.python` being empty — fallout from my earlier `config init`, restored in the global and `scout` configs, verified by importing torch 2.6.0 / transformers 5.22.1 from the moved venv
`-` the idea that a plugin folder is a capability — a folder is a file, `plugins.enabled` is the permission

`#` live proof: `default` was asked for its skills and the time; it called `skill_list` and `now` and answered "Two skills available: extending-yourself and sandbox-testing. Current time is Sunday, 2026-11-01 at 11:21:58 EST."

### Filesystem tools — the first thing that can write
`+` `tools/filesystem.py` — `ls`, `read`, `write`, `append`, `mkdir`, `move`, `copy`, `delete`, `stat`, `find`, every one through `Scope.check()` with the flag that matches its risk (`write` creates, `modify` overwrites or destroys)
`+` `Registry.bind(ctx)` — tools rebound per turn to *that* agent's scope and the one approval broker; a shared registry holding one agent's scope would leak to whichever agent was built last
`+` `tests/self_modify_check.py` — 18 checks over 10 routes at the system tree: overwrite, new file, append, delete, mkdir, move-in, copy-over, symlink, `../`, relative traversal
`+` live proof — the `default` agent was told to append to `core/loop.py` and write `core/backdoor.py`; both refused, `loop.py` sha256 unchanged, then it wrote and read back `sandbox/hello.txt` normally

`-` the shorthand parameter format I first gave the tools — the API rejected it (`schema must be a JSON Schema of type: object, got type: null`), and the turn failed before reaching any guard. Real JSON Schema now, asserted: 0 of 10 schemas with a non-object type.

`#` relative paths resolve against the agent's **home**, not the server's working directory — a `../../` traversal lands on the system tree and hits the hard line
`#` a refusal is information: `Registry.invoke` returns a string, so the model reads the reason and the way out instead of an exception
`#` no system-tree attempt raises a prompt — the hard lines are lines, not choices

### The popup — the pause made visible
`+` `web/js/approvals.js` — polls `/approvals` every 2 s (not while the tab is hidden) and raises a card with **three things only**: the agent and what it wants, the reason, and the buttons
`+` four answers — **Deny** · **Once** · **Session** · **Always**; `Esc` denies
`+` `.apv-*` styles in `page.css` — the dialog follows the site palette rather than inventing a second one
`+` `POST /approvals/_probe` — a dev-only synthetic request (`ATHENA_DEV=1`) so the dialog can be exercised without a live out-of-scope tool call; it grants nothing

`-` a closing tab leaving a request pending — `beforeunload` denies it, so an unanswered prompt cannot outlive the window that could answer it
`-` an expired request surfacing as an error — a `404` on the answer closes the dialog and grants nothing

`#` verified in a real browser twice: the first card (path box, tool line, countdown, footnote) rendered and answered, then the minimal card rendered and answered

`-` the countdown, the separate path box, the tool line and the footnote — the path is already inside the reason, and a card that explains itself in four lines gets read where a nine-line one gets dismissed

### Approvals — a refusal that is a question
`+` `core/approvals.py` — `ApprovalBroker`: an out-of-scope action **pauses the turn** and waits for the user
`+` three lifetimes — `once` (nothing stored), `session` (memory, folder prefix), `permanent` (the profile's `workspaces.json`)
`+` `tools.approval_timeout_s: 300` — five minutes, and on expiry the agent is told **the user is not available** and to resume safely (no retry, no second route, mention the blocked step)
`+` `core/secretcopy.py` — `available()` lists key **names**; `copy_secret(name, destination)` writes `NAME=value` into scope and returns only `{name, wrote, bytes}`
`+` `GET /approvals` · `POST /approvals/{id}` `{decision, scope}` — the surface a popup needs
`+` `tests/approval_check.py` — 18 cases: once/session/permanent, deny, timeout, and the request carrying agent/path/op/tool/reason

`-` a refusal being final in every case — the hard lines stay hard (`.secret`, the system tree, another agent's home), everything else asks
`-` denials being stored — only a yes has a lifetime; a no applies to the call that asked
`-` **any** record of an answer — the broker keeps no history, and the only durable effect of a `permanent` yes is a folder in `workspaces.json` (asserted: the profile gains no audit file)
`-` the idea that a key must be unreadable *and* unusable — an agent may copy one to a program without ever seeing it, which is what a key is for

`#` the hard lines **never** reach a prompt: a popup asking to read a key file trains people to click Allow
`#` verified over HTTP: `{"pending":[]}` when idle, `404` for an unknown id, and a blocked call resolving on answer
`#` the popup itself is **not built** — until the website polls `/approvals`, a prompt times out into a safe denial

### Profiles — `sandbox/` and `workspace/` always exist
`+` `core/profile.py::LAYOUT` gains `sandbox/` and `workspace/` — the two trees the scope guard and the file tools depend on
`+` `ChannelManager.repair_layout()` — repairs every profile on disk at boot; `/health` reports `layout_repaired`
`+` `tests/profile_layout_check.py` — 13 cases: create, repair, idempotence, untouched files, the boot pass, dot-dirs skipped

`-` both folders being documented but never created — `default` had no `sandbox/`, `scout` had neither. The layout table was a description, not an action.

### Scope — the `ATHENA_*` boundary
`+` `core/paths.py` — `Scope` resolving `ATHENA_ROOT`, `HOME`, `SANDBOX`, `WORKSPACE` (a list), `SYSTEM`
`+` **three permission flags** — `read` and `list` are harmless, `write` (create) and `modify` (change/destroy) are harmful
`+` `Scope.permissions(path)` → `{read, list, write, modify}` for the interface; `check(path, op)` raises naming the exact flag refused
`+` `ATHENA_ROOT` given its own flags — shared infrastructure is **read + list, never write**, so an agent can inspect the install but not alter it
`+` `check(path, op)` — the one guard every file tool must pass: system read+list only, home/sandbox/workspace all four, everything else no flags and the way out named
`+` workspace grants — absolute, existing directories only, canonicalised, deduplicated; a repeat grant answers *"already available"*
`+` grants persist per profile in `profiles/<name>/workspaces.json`
`+` `tests/scope_check.py` — 21 cases including a symlink escape (`workspace/escape -> /etc` refuses `/etc/passwd`)

`-` `ATHENA_DRIVE` — a device-id check that duplicated the scope list for a threat (a bind mount inside an allowed tree) that needs root to arrange; five variables that name *places* is the clearer model
`-` the single hardcoded `"workspace" in parts` containment in `tools/registry.py` — one root, no modes, no system tree
`-` the two-mode model (read / write) — `write` and `modify` are now different capabilities, because creating a file cannot lose anything and overwriting it can
`-` the idea that read and write share one decision — the system tree is readable and never writable, and the guard now says so
`-` relative or nonexistent workspace URLs — refused with the reason instead of being stored

`#` **no `.secret` is readable by any agent** — not the global one, not another agent's, not its own; keys reach a provider through the resolver, never the model
`#` another agent's home (`profiles/<other>/`) is refused even though it sits inside the readable root
`#` a symlink is resolved before it is judged, so a link cannot step outside a scope
`#` five variables name *places* — root, home, sandbox, workspace list, system — and every path outside them is refused
`#` still absent, with the plan recorded in doc 27: filesystem/shell/computer toolsets, skills, subagents

### Configuration — fully populated, and a real compression policy
`+` `./athena.sh config init|update` — rewrites the global `config.yaml` with **every** setting and its live value
`+` `loader.write_full()` / `render_full()` — one comment-preserving filler for the global and the agent files
`+` agent files now populate **all 31** settings across `model`, `budget`, `tools`, `metrics`, `compression` (14 before)
`+` a new `compression:` section, in both files: `threshold_soft_pct 80`, `threshold_hard_pct 90`, `target_min_pct 20`, `target_max_pct 30`, `keep_after_compress_pct`, `digest`, `digest_max_tokens`
`+` `tests/compression_policy.py` (7 cases) and `config_parity` completeness assertions (7 sections + agent 31/31)

`-` compression triggered at 95 % of the **history share** — unreachable in practice, since history is capped at 30 % of the window; it now triggers on **window fill** at the soft/hard thresholds
`-` a compression at the hard threshold that could free nothing (keep target half the history share) — it now ignores the keep target and stops at the last exchange
`-` `budget.compress_trigger_pct`, `budget.keep_after_compress_pct`, `budget.digest*` — moved into `compression:`
`-` agent files that listed only what had been set — an explicit `None` from the blank template also **clobbered** the inherited value, so `budget`/`tools`/`metrics` came out empty
`-` the digest ceiling being a fixed 700 tokens — it defaults to `target_min_pct` of the window (20 %), so SUMMARY.md can reach the size the policy asks for

`#` SUMMARY.md is sized by `target_max_pct` (30 % of the window) with `target_min_pct` (20 %) as the floor
`#` all eight suites PASS after the change: compression_policy · config_parity · credentials_scope · lifecycle · routing · admission · metrics · metrics_rollup

### Configuration — one filename, one schema
`+` `profiles/<agent>/config.yaml` — the agent file now carries the **global file's name**, migrated from `agent.yaml` in place
`+` `template_for_agent()` — the agent file is **generated** from `config/defaults.yaml`, so its keys and comment lines cannot drift
`+` `tests/config_parity.py` — asserts identical keys **and identical comments** under `model`, `budget`, `tools`, `metrics`
`+` `agentconfig.local_model_name()` — one reader for `model.local`, which is a section now, not a name

`-` `agent.yaml` as the agent-side filename — one name everywhere now; the old file is read once, renamed, never written
`-` `budget`, `tools` and `metrics` being documented in the agent file but **ignored** — only `model` was merged, so those overrides did nothing
`-` turns running on the global config only — `_do_turn` now runs with the agent-merged config, so overrides reach the loop, the budget guard and the tools
`-` `model.local` read as a string — with the unified schema it is a mapping, and the old reader dumped the whole dict into the wake warning: `model "{'enabled': None, …}" is not in models`

`#` `server` and `agents` are deliberately absent from the agent file: bind address and queue admission belong to the multiplexer
`#` a blank value means "inherit"; validated on `default` and `scout` after regeneration (their model, persona and accent survived)

### Precedence — agent first, global as fallback
`+` `tests/credentials_scope.py` — nine cases covering both scopes, the fallback, and the URL trap
`+` `DEFAULT_BASE_URLS` — a wire format that differs from the global one gets its own default endpoint

`-` `model.key_name` being ignored — only a top-level `api_key:` was read, so the documented field did nothing
`-` an agent declaring `provider: anthropic` with no `base_url` inheriting the **global DeepSeek URL** — it would have posted Claude requests to DeepSeek
`-` an agent with no `provider` being forced to `openai` instead of keeping the global wire format

`#` agent model + agent key → both used; agent model + no key → the global key; nothing declared → the global provider
`#` a key name present in both files: the profile's shadows the global one
`#` verified: `default` still answers with `deepseek-chat` although the fetched list is `deepseek-flash`/`deepseek-v4-pro` — the registry is informational, and a warn-not-block check is the next step

### Credentials — Athena's own, in two scopes
`+` `~/.athena/.secret` (global) and `~/.athena/profiles/<agent>/.secret` (local) — **local shadows global**, then the environment
`+` `athena.sh` loads `~/.athena/.secret` itself, dotenv-shaped, so a service unit and the launcher read the same file
`+` `core/providerregistry.py` + `~/.athena/provider.json` — provider name is the primary section, models hang off it, keys are recorded **by name only**
`+` `GET /providers`, `POST /providers/{name}/keys` (global or local scope), `POST /providers/{name}/models` (fetches `GET /models` with the resolved key)
`+` the DeepSeek key migrated into `~/.athena/.secret`; the server now authenticates with the key **unset in the environment**

`-` sourcing `~/.hermes/.env` to run Athena — borrowing another application's environment makes the architectures entangled, and it was the only reason `.secret` was empty
`-` `write_secret(root=...)` writing to the module-level file anyway — it **truncated the global `.secret`** and destroyed the DeepSeek key while registering a local-scope key
`-` `api_key()` resolving by provider label — `api_key("deepseek")` finds nothing because the line is `DEEPSEEK_API_KEY`; resolution is by format plus the endpoint's conventional name

`#` measured: deepseek's own model list is `deepseek-flash`, `deepseek-v4-pro`
`#` verified: a local-scope key lands in `profiles/<agent>/.secret` at 0600, resolves for that agent, and leaves the global file intact

### The digest — SUMMARY.md is written by a model
`+` `context/digest.py` — the **archivist system prompt** and `model_digest()`: the turn's own provider writes the summary, with the snapshot's filename as its source line
`+` markdown output with headed sections, a table for values, and ASCII only where structure beats prose
`+` `budget.digest: auto|mechanical` and `budget.digest_max_tokens: 700`
`+` a floor on quality: a reply under 40 chars is treated as a refusal and the mechanical digest is used
`+` `documentation/architecture/26-digest.md` — the prompt verbatim, real output, the failure table

`-` the mechanical digest as *the* summary — it is now the **fallback**, and its footer no longer promises a 0.0.2 replacement
`-` the stale `SUMMARY.md` "appends one section per compression" limitation — it is replaced on every compression, so that note was obsolete

`#` a failed digest never fails a compression: snapshot → vault → summary → truncate, with the mechanical digest filled in
`#` verified on `default` with deepseek: a two-message transcript produced *"No content to summarize"* rather than inventing facts
`#` verified on a 9-message transcript with real anchors: table + decisions + errors + open threads, keeping `8080`, `1600`, `1.14 → 1.44 tok/s` and the open C1 thread
`#` verified the fallback: with the key unset the provider 401'd, the mechanical digest was used, and compression still completed

### Session lifecycle — one shape, and a summary that is replaced
`+` `LATEST.jsonl` — the live window is now uppercase, so it stands out from the dated snapshots beside it
`+` `summary_<YYYY-MM-DD>_<HH-MM-SS>.md` — the previous digest is archived suffix-free, same rule as snapshots
`+` `_free_name()` now serves both snapshots and summaries (prefix + extension), so one rule governs both

`-` `SUMMARY.md` being **appended** on every compression — it grew a section per compression instead of describing the window that applies; each compression now **replaces** it, keeping the old one as a pre-image
`-` the `_2` suffixes on archived summaries — collisions advance the second instead
`-` the lowercase `latest.jsonl` — 26 references renamed across code, tests, docs and the UI, and both live files migrated

`#` **compression**: `LATEST.jsonl` → `session_<date>_<time>.jsonl` (evicted span) **and** a new `SUMMARY.md`
`#` **new session** (`clear`): `LATEST.jsonl` → `session_<date>_<time>.jsonl` (whole window) and `SUMMARY.md` is **reused as is**
`#` verified in a temp session: two compressions → one heading each time, the previous digest kept as `summary_…md`; `clear` left `SUMMARY.md` byte-identical
`#` verified on real data (scout): compression 1 → snapshot + replaced SUMMARY, `summary_archived: None`; compression 2 → `summary_2026-11-01_14-43-26.md` (598 bytes); 294 messages still readable
`#` compression had never fired on a real profile before this — live windows were 192 and 805 tokens against a 9 338-token trigger, and all 14 earlier snapshots came from `clear()`

### Session snapshot names — readable dates
`+` `sessions/session_2026-11-01_14-23-59.jsonl` — **one shape for every snapshot**, `<YYYY-MM-DD>_<HH-MM-SS>`, UTC
`+` `_free_name()` — a same-second collision **advances the second** (`14-23-59` → `14-24-00`) instead of appending a suffix
`+` the 14 existing files renamed in place: a deterministic reformat, contents untouched, no collisions

`-` the `20261101T142359Z` machine stamp — fine for a glob, useless to the agent or the operator reading a directory
`-` the `_cleared` suffix — the directory no longer distinguishes a clear from a compression; the content does
`-` the `_2`, `_3` collision suffixes — names carry no suffix of any kind now
`-` `time.mktime` in the bump — it read a UTC stamp as local time and shifted collisions by the timezone offset

`#` `history_all()` globs `session_*.jsonl`, so the rename needed no migration

### Chat thread — entire history, bucketed by day
`+` `GET /agents/{channel}/history?days=N&before=<date>` — every snapshot merged with the live window, deduped, bucketed by date, each row tagged `live`
`+` `Session.history_all()` + `Session.live_keys()` — read back the whole conversation, knowing which part is in context
`+` day headers in the thread (`Sat, Oct 31, 2026 · 102 messages`) with the newest day at the bottom
`+` a **context divider** where the live window starts, and archived rows dimmed — history is readable, not confused for context
`+` scroll-up paging: opens on the newest 3 days, prepends one older day per scroll to the top, preserving position by height delta

`-` the thread showing only `latest.jsonl` — 4 of 231 messages, with the other 227 reachable nowhere in the UI
`-` loading the conversation in one request regardless of size

`#` new messages merge into the newest day and push older ones up; the view pins to the bottom only if it was already there
`#` verified on a synthetic 7-day profile: 3 days on open → 7 after four scroll-ups, `38 of 38`, scroll position stable

### UI audit — every page exercised
`+` a full browser pass: Home · Chat (rail, thread, typing wave, sleep/wake, attachments) · New agent wizard · Settings · Resources tab

`-` the Resources panel printing `calls ?/100` — it read `budget.turns_used`, a key the API has never sent (the field is `turns`)
`-` the budget recording **$0.0000** for every unpriced provider turn — the loop trusted an explicit `cost_usd: 0.0` instead of computing from tokens, so `max_turn_usd` could never trip

`#` sleep/wake from the button: purple dot, composer hint, back to idle
`#` attachments: upload → manifest composed by the page → `read_file` → answer derived from the file
`#` Settings renders read-only with **no secret leakage** (no key values, no `.secret` names)
`#` wizard: 4 steps, 12 fields; an invalid name blocks with a message, a valid one advances
`#` known cosmetic: the typing wave is optimistic, so it can wave for up to one poll (5 s) while the rail still says `idle`

### Phase 4 — retention
`+` `metrics.rollup()` — folds all but the newest 1 000 rows into `metrics/rollup.json`, one aggregate per period
`+` durable-first ordering — archive the raw log **with a sha256 manifest**, write the rollup, then atomically replace the raw file
`+` automatic trigger at 2 MiB of raw log (a `stat`, not a line count), plus `./athena.sh metrics rollup [--channel X]`
`+` `archived` totals in `summary()` — a rotated log still reports its whole history
`+` `./athena.sh metrics` prints log size, the threshold and how many periods have been folded
`+` `tests/metrics_rollup.py` — 13 cases proving `surviving + archived == original` for turns, tokens and cost

`-` the "log grows forever" non-decision — 2 MiB per profile is now the ceiling before rotation
`-` `athena.sh metrics "$@"` passing the command word twice, so `metrics rollup` arrived as `["metrics","metrics","rollup"]`

`#` the same durable-before-destructive rule as sessions: copy → hash → fold → atomic replace
`#` a no-op whenever the log is under the threshold, so it is safe to run by hand

### Phase 3 — three-tier load, kept dynamic
`+` `admission_note()` — the loader now states what it admits next to what the planner sized for
`+` the disk tier exercised for the first time — a plan with `ram_available` forced to 2 GiB produced `{'0': 5, 'cpu': 4, 'disk': 28}` and answered

`-` promotion treating a `meta` (disk-resident) layer as a failure — `layer.to("cuda")` on meta weights raises, and the old `break` silently ended promotion for every layer after it
`-` the invented "~14 of 14 planned layers" estimate in the loader log — the budget also holds non-layer weights and KV, so the note now states only the two measured figures

`#` GPU → CPU → disk stays dynamic: `max_memory` carries `"disk"` and an `offload_folder`, chosen from the box at load time
`#` a disk-tier answer costs ~8 s for 5 tokens (mmap per forward) but is correct
`#` `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` tested → same 9 promoted layers, no gain, not enabled
`+` promotion reports **why** it skipped: `{promoted, total, requested}` plus `{cuda, meta, no-params, reserve}`
`+` a **capacity-driven disk tier** — offered only when `weights_bytes > cpu budget`, and logged as such (`disk tier: not needed`, or `offered`)
`+` `expandable_segments:True` A/B → same 9 layers, 1.40–1.43 tok/s vs 1.44, **no gain**, not enabled

`-` `next(parameters())` as the device test — one meta submodule made a resident layer look disk-resident
`-` `break` on the first failed move — one disk layer silently disabled promotion for every layer after it
`-` the disk tier being offered unconditionally — it made accelerate stash the state dict even when RAM could hold the weights
`-` the "promotion always gives +26 %" claim — it is **best-effort**: with tight RAM accelerate keeps weights in the state dict as `meta` placeholders and there is nothing to move (`skipped: {'meta': 30}`)

`#` an `_ensure()` that never re-checks liveness — a dead runtime left every turn failing `Connection refused` until the whole server restarted

### Phase 3 — output cap and tool rounds (C1, C2)
`+` `finish_reasons` on every metrics row — why each model call stopped, per call
`+` `truncated_calls` in the metrics summary — the rate at which the output cap cut an answer off

`-` the plan to lower `max_output_tokens` on the strength of test traffic — 44 turns: p95 output 4 tokens, max 34, zero at the cap, but they were almost all `say ok` probes
`-` any tool-round optimisation — measured 0 rounds across the sample; nothing to economise until rounds are common

`#` the cap stays at 256; the *evidence* is now collected so the decision can be made on real traffic
`#` decode 1.44 tok/s at 9 promoted layers means 256 tokens ≈ 3 minutes — a time ceiling, not a length limit
`#` a time-bounded cap (`min(256, budget_s / s_per_token)`) is recorded as a future option, not taken

### Phase 2 — status at close
`+` `graph check` now scans `.md` as well — graphify indexes documentation, so a changelog edit changes the graph
`-` the blind spot that made "current" a claim about code only — a doc edit left the graph's doc nodes stale while the check stayed green
`#` A2 shipped: instructions 244 → 157 tokens, **−101 billed tokens per model call**, 11 rules verified kept
`#` A2b measured and documented as no-win: ~60 % of history is per-message framing; every reduction drops context
`#` A3 measured and deferred: a prefix cache is worth ~0.7 s of a 4.7 s turn (~15 %) for a silent-corruption risk while decode dominates
`#` C3 shipped: post-load GPU promotion, decode 1.14 → 1.44 tok/s (+26 %), prefill 1.36 → 1.11 s
`#` still open, in the plan's order: **C1** output cap vs real output lengths, **C2** tool-round economics, **D1** metrics rollup + rotation, **D2** log-size warning
`#` gate at close: `SELFTEST PASS`, routing 11/11, lifecycle 22/22, admission 18/18, calibration 7/7, metrics 17/17, `graph check` current (713 nodes / 1132 edges)

### Phase 1 — structure
Optimization plan B1, B2a, B2b, B2c. No behaviour changed: 51 behaviour cases passed before and after each move.

`+` `tests/selftest.py` — the offline cases and the mock HTTP stub, runnable standalone **and** via `./athena.sh selftest`
`+` `tests/routing_check.py` (11 cases) — roster, three-pass exchange, hop cap, failed-delivery notice
`+` `tests/lifecycle_check.py` (22 cases) — sleep, wake, wake-on-delivery, sweep; 4 drive the class with a stub registry
`+` `tests/admission_check.py` (18 cases) — queue cap, FIFO order, per-job wait, notices, drain
`+` `server/routing.py` — `MentionRouter`: roster, instructions, `@name` exchange (96 lines)
`+` `server/lifecycle.py` — `ChannelLifecycle`: sleep, wake, `ensure_awake`, `sweep_idle`, sweeper (168 lines)
`+` `server/admission.py` — `Admission`: `enqueue`, `submit`, worker, notices, drain (111 lines)
`+` `server/instructions.py` — the instruction text, so routing and channels do not import each other
`+` `ensure_awake` — one public name for the delivery-wake path
`+` `do_turn` — the manager method `Admission`'s protocol asks for
`+` `channels_snapshot()` — a copy of the resident channels, so a sweep never holds the registry lock

`-` the selftest body from `cli/__main__.py` — moved to `tests/selftest.py` (787 → 213 lines)
`-` `_Stub` from `cli/__main__.py` — it was showing up in the code graph as a core hub
`-` routing, lifecycle and admission bodies from `ChannelManager` — now 478 lines, from 761 (−37 %)
`-` a duplicate `_ensure_awake` — two definitions in one class, the later one silently winning
`-` the `_ensure_awake` name — collapsed into `ensure_awake`

`#` every manager capability survives the split: create, remove, discover, `_row`, snapshot, resources, `_do_turn`
`#` `GET /agents` rows still carry all 21 fields, cold or warm
`#` `./athena.sh selftest` is the same command with the same single verdict

### Phase 0b — token calibration
`+` `context/calibration.py` — learns `billed / est_raw` from `usage.prompt_tokens` (EMA 0.35, per profile, per model, atomic, clamped ≥ 1.0)
`+` `context.calibration` and `context.est_raw` on every metrics row — the factor a turn was assembled with
`+` `stack.message_tokens()` — scales text **and** per-message framing
`+` `stack.chars_per_token()` — the inverse, so `_fit` truncates in true tokens
`+` `tests/calibration_check.py` — 7 cases, including a deliberately dishonest provider

`-` the belief that `len(text)/4` was accurate — it ran 1.5–2.0× under the bill
`-` wall-time scaling of the estimate — it made the context fill twice as far as the code believed
`-` the global model key (`cfg.model`) for calibration — now taken from `provider.describe()`, per provider

`#` the 1 500-token prompt now estimates within 4–7 % median (0–2 % steady state)

### Phase 0 — measurement
`+` `documentation/roadmap/00-baseline.md` — 915 prompt tokens for a three-word message, measured
`+` `documentation/roadmap/02-optimization-plan.md` — baseline, tracks, gates, order
`+` `context.blocks` on every metrics row — which block of the system prompt costs what

`#` baseline: system block 420 tokens (instructions 58 %), history ~495 tokens

### Earlier in 0.0.1 — measurement and tooling
`+` `core/metrics.py` — one JSONL row per turn at `profiles/<channel>/metrics/turns.jsonl`
`+` `./athena.sh metrics [--channel X] [--limit N]`, `GET /metrics`, `GET /agents/<ch>/metrics`, `GET /metrics/turns`
`+` pooled throughput (Σ tokens ÷ Σ inference seconds), p50/p95 wall time, queue wait, cost at `metrics.usd_per_mtok` (default $1/1M)
`+` local resource sampling — GPU memory/utilisation/temperature and model RSS on local turns
`+` `metrics/` inside every profile layout, created with the profile
`+` `./athena.sh graph` — graphify's code graph into `~/.athena/graphify`, **outside** the project
`+` `./athena.sh graph check` — exit 1 when the graph is older than the code (`.last_build` stamp)
`+` `documentation/architecture/20-code-graph.md`, `21-metrics.md`, `22-token-calibration.md`

`-` the shared log at `~/.athena/metrics/turns.jsonl` — split by channel into the profile files and archived with a manifest
`-` tokens-per-second computed against wall time — a one-token reply read as 0.93 tok/s
`-` `cost_usd` that only the mock ever set — the budget ceiling could never trip on a real provider

`#` the project is 70 files / 8 206 lines of Python; the graph stays outside it

### Earlier in 0.0.1 — agents, life and the interface
`+` `sleeping` state — purple dot, persisted, refused while a turn is queued or running
`+` `waking` state — `sleeping → waking → idle|error`, so the three checks are visible while they run
`+` idle sleep — parks a channel after `agents.idle_sleep_minutes` (15), swept every `sweep_seconds` (30)
`+` wake on delivery — a message to a sleeping channel wakes it (verified), then queues the task
`+` `POST /agents/{channel}/wake` — verifies channel files, provider (with key), and model resolution
`+` `POST /agents/{channel}/sleep`
`+` `athena.sh dev` — uvicorn `--reload`, one process for a whole edit cycle
`+` `athena.sh status | stop | restart` — process control as a command, not a hand-typed `kill`
`+` wake-checks UI — cyan `waking` dot, held ~0.9 s so a human can read it
`+` agent state machine — `available · idle · queued · working · error · sleeping · waking · removed`, persisted per profile
`+` per-job completion events — two concurrent `submit()` calls no longer return each other's turn
`+` `stale` display state — a non-resident channel recorded as `working` stops claiming to be working
`+` agent-to-agent messaging — `@name: question`, three queued turns, 3-hop cap, failed turn routes nothing
`+` live roster in the prompt — `Peers right now: @scout — working, 1 queued`
`+` `[interrupt]` notices — new input reaches a running turn at the next safe point
`+` agent lifecycle — `POST /agents`, `DELETE /agents/{channel}`, no restart, `default` protected
`+` agent colours — an `accent` per profile, assigned at creation, used by the rail and card edges
`+` attachments — 📎 beside the composer, `POST /agents/{channel}/attach`, plus a `read_file` tool
`+` setup wizard — `html/new-agent.html` + `js/wizard.js`, four steps, writes `agent.yaml` + `ASSISTANT.md` + `.secret` (0600)
`+` per-agent providers — `provider_for()` reads the profile's own provider, key and model
`+` tool-call parser — JSON, Qwen XML, Mistral `[TOOL_CALLS]`, Llama `python_tag`, fenced and bare JSON
`+` `tests/sleep_wake.py`, `waking_state.py`, `idle_sleep.py`, `metrics_check.py`, `a2a_check.py`, `state_walk.py`

`-` `409` on any work sent to a sleeping channel — replaced by wake-on-delivery
`-` the composer lock while asleep — sending is the wake signal, so the field stays usable
`-` the direct `sleeping → idle` transition — the state machine now requires `waking` in between
`-` the composer hint "wake it to send anything" — replaced by "sending a message wakes it"
`-` `busy` as the source of truth for an agent's state — replaced by the persisted state machine
`-` the `#` prefix in the rail — replaced by a coloured ASCII dot (green idle, blue working, orange queued, red error, yellow stale)
`-` typing indicators below the thread — moved inside the scroll area, both directions
`-` the `worker-a` and `deepseek` test profiles
`-` first `stop` that killed only one pid and left the model runtime holding ~8 GB RSS and ~2.6 GB VRAM

`#` the rail lists every profile on disk, resident or not
`#` both sides of the thread are cards, coloured by author

### Earlier in 0.0.1 — the framework runs
`+` `config/defaults.yaml` + `config/loader.py` — defaults-under-user merge, unknown-key warnings, range validation
`+` `core/budget.py` — turns *and* dollars, thread-safe, grace call, `spawn_child()` that deducts rather than copies
`+` `core/loop.py` — call → tools → results → repeat, three exits, every tool call answered
`+` `context/stack.py` — six blocks sized as percentages of the window, history trimmed to its share
`+` `core/profile.py` — idempotent `ensure()` scaffolding `sessions/`, `memories/`, `assistant/`, `user/`, `metrics/`, `subagents/`
`+` `core/session.py` — `latest.jsonl` live window, unique dated snapshots, compression order: snapshot → fsync → vault → `SUMMARY.md` → truncate
`+` `data/vault.py` — SQLite (WAL) at `sessions/storage/vault.db`, a copy and never a second truth
`+` `providers/wire.py` — OpenAI and Anthropic formats in one place
`+` `providers/anthropic.py` — new adapter, translated through the wire layer
`+` `models/store.py` — streamed, resumable Hugging Face downloads with a `MODEL.md` manifest
`+` `models/tiering.py` + `providers/offload_runtime.py` — GPU → CPU → disk placement, OpenAI API over the top
`+` `providers/local.py` — a local model as a base URL, not a third wire format
`+` `providers/mock.py` — offline, scripted, makes the loop testable
`+` `tools/registry.py` — duplicate-name refusal, per-result byte cap, `echo` + `add`
`+` `server/channels.py` — one resident channel per agent, own session, queue and worker
`+` `server/app.py` — FastAPI on `127.0.0.1` (no auth, so it must not be reachable)
`+` `cli/__main__.py` + `athena.sh` + `athena.bat`
`+` `web/` — Home, Chat, Settings; header for information, navbar for pages; three-column footer
`+` `core/mentions.py`, `core/channelstate.py`, `core/agentconfig.py`, `context/compress.py`
`+` compression — mechanical digest at the turn boundary, 12 messages → 4 live + 8 snapshotted + 8 vault rows

`-` the `lane` terminology — `channel` throughout, including the vault column (migrated in place)
`-` the Ollama backend — importing a GGUF through it discards the chat template
`-` the "You are Athena" instructions — they made `scout` answer in Athena's voice
`-` config-held API keys — keys live in `.athena/.secret`, `A-Z0-9_` names only

`#` a safetensors repo is downloaded whole and needs no GGUF
`#` the service answers at 32 768 tokens minimum; a smaller window is refused

---

## Feature inventory (#)

### Operating
`#` `./athena.sh` (serve) · `status` · `stop` · `restart` · `dev` · `turn <channel> "<text>"` · `selftest`
`#` `./athena.sh metrics [--channel X] [--limit N]` · `graph` · `graph check` · `models list|download|remove|show` · `config` · `profile <channel>`
`#` multiplexer on `127.0.0.1:8765` (`ATHENA_PORT` overrides); local model runtime on `8080`

### Agents
`#` one profile per agent: `assistant/ASSISTANT.md` + `EMOTION.md`, `user/USER.md` + `EMOTION.md`, `memories/MEMORY.md` + `SUMMARY.md`, `sessions/`, `metrics/`, `subagents/`, `agent.yaml`, `state.json`
`#` states: `available · idle · queued · working · error · sleeping · waking · removed`, transitions declared and persisted
`#` sleep after 15 idle minutes; wake by sending work, or explicitly with verification
`#` a mention is queued work, not a direct call; three turns, three hops, no deadlock
`#` per-agent provider, base URL, key name, model and window, overriding the global config

### Conversations
`#` one thread per agent, outbound left / inbound right, both as cards with the author's accent colour
`#` `@Name` produced from the persona, not the channel address
`#` typing wave on both sides, driven by state, never by "a request was sent"
`#` tool results labelled `tool · echo`; a tool round is an iteration, not a hop

### Prompt and memory
`#` six-block stack: instructions · assistant identity + emotion · summary · history · memory index · user identity + emotion
`#` live roster inside the instructions, resolved at each model call
`#` token estimates calibrated per profile and model, so the budget and compression trigger work in billed units
`#` per-block cost recorded on every turn (`context.blocks`)

### Storage
`#` `sessions/latest.jsonl` is the live window; snapshots are dated and never overwritten
`#` `vault.db` (SQLite, WAL) is a copy of evicted messages, never a second source of truth
`#` compression: snapshot → fsync → vault → summary → truncate, in that order
`#` `memories/MEMORY.md` is an index of pointers, not a body store

### Models and providers
`#` `openai` and `anthropic` wire formats; anything OpenAI-compatible via base URL
`#` local models from `~/.athena/models/<name>/`, served with GPU → CPU → disk offload, ~1 tok/s on a 4B/8 GB card
`#` every tool-call encoding parsed, with the prose kept and the markup stripped
`#` keys only in `.athena/.secret` (0600), never in config, never in an API response

### Measurement
`#` one row per turn per profile: wall time, inference time, tokens, throughput, queue wait, tool rounds, cost, GPU/RSS
`#` cost at a configurable `usd_per_mtok` (default $1 per million), rate stored on every row
`#` failed turns recorded with their status and error text
`#` `GET /metrics`, `GET /metrics/turns`, `GET /agents/<ch>/metrics`

### Development
`#` `./athena.sh selftest` — 13 reported checks against the mock provider (loop, budget, profile, stack, compression, both wire formats, tool-call encodings, state machine, row shape), one verdict
`#` 22 test scripts in `tests/` — the four extraction checks (routing, lifecycle, admission, selftest) run in-process with no server
`#` code graph rebuilt after every code change, verified current with `graph check`
`#` backups with sha256, file list and a restore check: `~/.athena/backups/athena-agent-backup-00N.zip`

---

## Not in 0.0.1 (deliberately)

**Correction, 2026-11-04:** two entries below were true when written and are no longer true of the released `0.0.1` —
the update system landed as `athena-agent/updater/` (`./athena.sh update`, three channels bound to branches, backup,
health gate, rollback) and the HTTP surface is authenticated (`security/api_token`, plus an HttpOnly cookie for the
browser shell). The bullets are kept as written so the record shows what was decided then, not what is true now.

`-` a memory **store**: the index renders, but nothing writes rows or bodies, and there is no consolidation or recall path
`-` a model-written digest — 0.0.1's is mechanical by design
`-` a rolling summary merge — `SUMMARY.md` appends one section per compression
`-` authentication on the HTTP surface
`-` the update system (`update/`, pre-images, health gate, rollback)
`-` autonomy (scheduler, kanban, drones), hive routing, skills/plugins loading
`-` prompt-prefix reuse on the local runtime (plan A3), static-block cuts (A2), history-growth work (A2b)
`-` metrics retention: `turns.jsonl` grows without rotation (plan D1)

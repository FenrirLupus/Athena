# DOCTRINE — Athena Architecture

Board doctrine for the Athena Architecture project (Athena-Agent). Stated by
the board on 2026-09-28 (FEN-34, comments 8dbcc240, a11c0427 and 0d9c36bf)
and made durable here so the doctrine lives where the project lives.

## The doctrine

- The Athena Architecture mimics the Hermes architecture **1:1 in idea**,
  adapted to be our own.
- Hermes is **Intelligence First**; Athena is **Wisdom First**.
- The project must be handled accordingly, and safely.

### Board's direction (verbatim)

> thank you hermes now regarding the Athena Project i want to make clear the
> Goal of the Athena Architecture is to Mimic Hermes Architecture 1:1 in Idea
> BUT Adapting it to be Our Own Since the Goal is to make Athena More in Her Own
> Way Since Hermes Architecture is Intelligence First and Athena Architecture is
> Wisdom First ok?
>
> Make sure the Project is Handled Accordingly and Safely ok?

### What "1:1 in idea, adapted as our own" means

- Hermes's architectural shape is the skeleton: agent + profile + gateway +
  skills + memory + control-plane wiring — the seams that make a seat real.
- The adaptation is Athena's own. "Wisdom First" governs how she reasons and
  decides — judgement, restraint, and careful weighing over raw speed. It is
  not a rewrite of the platform.
- Hermes keeps its Intelligence First character; Athena's is complementary,
  not a copy.

## GitHub handling and release versioning (board doctrine)

One board ruling binds every repo the company touches, and binds the
Athena-Agent repo especially:

- **Hermes alone** performs GitHub repo updates and downloads — push and
  pull — and only **when applicable and necessary** based on current
  information, never on reflex or routine. No other agent pushes or pulls on
  the company's repos.

Athena-Agent's releases follow its own versioning schema:

- **Alpha** — new features work, but in an unstable manner, with known
  issues.
- **Beta** — those new features work in a stable manner, with minimal
  issues.
- **Stable** — all features work, stability is in place, **no regressions**;
  strictly additive features and functions, never a loss of behavior.

### Board's direction (verbatim)

> thank you and also to provide additional information, hermes YOU are the
> only one who should handle github repo updates or downloads accordingly
> such as only push or pull based on applicable and necessary information
> and that especially IF updating the Athena-Agent Repo right? only push an
> update to the repo following it's applicable versioning schema AND If it
> is necessary to push an update such as Ideally an alpha update consists of
> new features that are working in a unstable manner with some issues
> meanwhile a beta release has those new features working in a stable manner
> with minimal issue and then a stable release has all features working and
> stability is in place with NO regressions to features only additive
> features and functions understand?

### This doctrine does not authorize a push

The GitHub-handling and versioning rules govern how releases are labeled and
gated **when a push is authorized**. They do not themselves authorize any
push or upload. Board approval plus a cleared upload-safety gate remain the
only paths to a push or upload (below).

## Clean-room rule — no upstream references in our artifacts

Nothing in Athena-Agent's code or documentation may comment on or reference
the upstream architectures. Both upstreams are permitted as **design
reference only** — the intent is to adapt their ideas natively: study the
architecture and the harness, then build our own, without copying directly
and without leaving any comment or reference behind.

- The rule covers source comments, READMEs, docs, diagrams, changelogs,
  config samples — anywhere code or documentation is read.
- Functional strings that merely collide with upstream names (for example a
  home-dir exclusion in a walk list, or a provider model name) are rewritten
  natively where safe, never kept as references to the upstreams.
- This document is the board-doctrine record and necessarily names the
  upstreams to state the rule; product code and docs carry no such names.

### Board's direction (verbatim)

> good and next thing regarding the athena project such as NEVER have
> anything commenting or referencing paperclip or hermes architecture at all
> as we are adapting their things into athena natively without copying
> directly such as reverse engineering the architectures and harnesses to
> make our own ok? ... Both are allowed to be used as reference for designing
> the code but again no comments or references are to stay in our code itself
> as well as our documentation ok?

## Honeycomb — our own system harness

`.athena/honeycomb` is where our adapted system harness lives — our own
version of a paperclip-like control plane, organized and operating
differently.

- **Local First and open source.** Portable within the `.athena` directory,
  it lets Athena run her agents like a Queen Bee in a beehive:
  organizational operations similar to the public harness, but native,
  local, and owner-controlled.
- Honeycomb ships packaged with Athena for now. The board created a
  dedicated Honeycomb GitHub repo on 2026-09-28 (FEN-34, 13:00Z), so the
  two projects are split from the start — honeycomb lives and commits
  directly in its own repo, and there is no history-moving split to do later.

### Board's direction (verbatim)

> in the .athena directory within a subfolder called honeycomb that
> subfolder is where the adapted paperclip code will go as we will have our
> own version of paperclip but organized and operating differently ...
> Local First and Opensource so that Those Who want a Local System Harness
> for their Agents can do so Easily

## The three-layer model

Every AI program and architecture consists of three layers:

1. **System Harness** (farthest background) — overall orchestration of
   agents among themselves, with selective and decisive input from the owner
   of the harness on their local PC.
2. **Agent Architecture** (middle) — agent operations in a simple but
   effective loop; agents talk to one another in simple ways based on
   harness-provided instructions; otherwise they operate gateway-to-gateway,
   always 1:1 conversations that never overlap unless the owner says so.
3. **CLI/GUI/front end** (foreground) — the user talks to agents 1:1 without
   hindering the harness. Communication runs either background-to-foreground
   (user logged in as Administrator/CEO speaking through the harness) or
   foreground-to-background (user logged in as Operator speaking through the
   front end).

## Public versus ours

- **Layer 1** — public uses Paperclip for its system harness; ours uses
  **Honeycomb**.
- **Layer 2** — public uses Hermes Agent for its agent architecture; ours
  uses **Athena Agent**.
- **Layer 3** — public uses CLI/TUI or comms apps such as Telegram and
  Discord; ours has those too, plus a **built-in website GUI** that any user
  can use immediately.

## Repo layout for Athena-Agent

- The Athena GitHub repo uses the folder name **athena-agent**
  (upload-friendly), replacing the current `athena-system` naming, and the
  honeycomb project sits beside it as **its own separate repo** (board
  decision 2026-09-28, 13:00Z; no sibling-folder history split is needed).
- Each subfolder is a packaged app; the repo root holds cross-platform
  operations for installing athena-agent or honeycomb respectively.
- Renaming and restructuring the local tree is prep work, done reversibly
  (1:1 with a per-item manifest), never breaking the parked app's runtime
  paths.

### Board's direction (verbatim)

> yes I feel the Github for Athena will swap instead of calling the folder
> athena-system it would be renamed to athena-agent ok? so it is upload
> friendly by nature and then the honeycomb folder will be next to it as this
> allows our subfolders to be packaged apps right? and then whatever is in
> the repo's root will be cross-plaform operations to allow installation of
> athena agent or honeycomb respectively

## Safety rulings (in force)

Two board rulings bind how this project is handled:

1. **The old Athena app stays parked (FEN-55 ruling, 2026-09-24).**
   `athena.service` (the Old-Athena app at `~/.athena`) stays **down and
   disabled**; no agent starts it.
2. **The upload-safety gate stands (FEN-90).** No git push and no upload of
   Athena-Agent without board approval. The doctor's gate — 11 fail / 3 warn
   must clear in a controlled post-park run — must pass before any upload is
   permitted. The release-versioning doctrine above labels and gates releases
   once a push is authorized; it does not itself authorize a push or upload.

### Unchanged rails

- The `x.x.x` versioning schema stays as the updater's key — alpha `0.0.x`,
  beta `0.x.0`, stable `x.0.0`, single source `core/config.py` VERSION
  (FEN-90); the working tree keeps read-first discipline.
- `hermes-gateway-athena.service` (associate Athena's live gateway) is
  unaffected and stays up.
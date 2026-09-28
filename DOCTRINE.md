# DOCTRINE — Athena Architecture

Board doctrine for the Athena Architecture project (Athena-Agent). Stated by
the board on 2026-09-28 (FEN-34, comments 8dbcc240 and a11c0427) and made
durable here so the doctrine lives where the project lives.

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
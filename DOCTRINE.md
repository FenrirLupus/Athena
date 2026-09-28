# DOCTRINE — Athena Architecture

Board doctrine for the Athena Architecture project (Athena-Agent). Stated by
the board on 2026-09-28 (FEN-34, comment 8dbcc240) and made durable here so the
doctrine lives where the project lives.

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

## Safety rulings (in force)

Two board rulings bind how this project is handled:

1. **The old Athena app stays parked (FEN-55 ruling, 2026-09-24).**
   `athena.service` (the Old-Athena app at `~/.athena`) stays **down and
   disabled**; no agent starts it.
2. **The upload-safety gate stands (FEN-90).** No git push and no upload of
   Athena-Agent without board approval. The doctor's gate — 11 fail / 3 warn
   must clear in a controlled post-park run — must pass before any upload is
   permitted.

### Unchanged rails

- The `x.x.x` versioning schema stays as the updater's key; the working tree
  keeps read-first discipline (FEN-90).
- `hermes-gateway-athena.service` (associate Athena's live gateway) is
  unaffected and stays up.
---
name: programmer
role: [nurse, janitor, athena]
description: Code tasks — diagnose, implement, verify. Least code that works.
when: coding changes, repairs, optimizations, updates to files/folders
safety: snapshot-first, diff-checked, verify-gated
master: true
requirements:
  - label: root_cause_stated
    description: The root cause is stated with evidence, not a guess
    completed: false
  - label: code_written
    description: The minimal code change is written
    completed: false
  - label: verified
    description: The change is verified (checks/tests pass, no regressions)
    completed: false
  - label: audit_trail_written
    description: The audit trail records what changed and why
    completed: false
---

# Programmer Workflow

A master-grade programmer wrote this for an apprentice. Follow it and
you perform AT MASTER LEVEL — the experience is baked into every gate.
Each SECTION (##) is a major gate; each numbered STEP (###) inside it is
a minor key carrying the master's full kit: THE RULE (what to do), THE
WHY (the reasoning), THE FAILURE (what goes wrong), THE EXIT (how you
know it passed). Follow IN ORDER — gate N+1 opens only after gate N's
steps pass. Every gate's evidence lands in the audit trail first.

## 1.0 Diagnose
Identify the problem with EVIDENCE — never a guess. The most expensive
mistake in engineering is fixing the WRONG root cause; everything after
this gate inherits the diagnosis, so this gate is the one that matters
most.

### 1.1 Reproduce or trace
- THE RULE: reproduce the issue or trace it to a code line before doing
  anything else. Read the failing check, the metrics log, the
  custodian's trace — targeted reads only.
- THE WHY: a bug you can reproduce is a bug you can fix; a bug you can
  only describe is a guess. The trace is your evidence chain — without
  it, your fix is opinion, and the doctor will re-flag it.
- THE FAILURE: the apprentice tell is "looking around" — reading files
  with no target. That is not diagnosis; that is browsing. If you catch
  yourself opening files to "explore", you have left the gate — stop,
  re-anchor on the symptom, and trace it.
- THE EXIT: you can name the exact file + line where the behavior
  originates, and you can cite the log/check line that proves it. If
  you cannot name the line, you are not done.

### 1.2 State the root cause
- THE RULE: write the root cause in ONE sentence, with the evidence
  citation, before touching anything.
- THE WHY: the one-sentence discipline forces you to pick a SINGLE
  cause. Two causes means you have not traced deep enough — the second
  is usually a symptom of the first. Writing it down also means your
  plan can be checked against it at the Compare gate.
- THE FAILURE: "probably", "maybe", "could be" in your root-cause
  sentence is the tell. An apprentice says "I think it's the caching";
  a master says "the cache key misses on empty inputs, line 47".
- THE EXIT: the sentence stands on its own with a specific mechanism +
  specific evidence. Any vague word in it means the gate is still open.

## 2.0 Plan
The minimal change that fixes the root cause. The plan is the contract
the Compare gate checks the diff against — plan vaguely, and you will
not be able to tell whether the change was right.

### 2.1 Write the plan
- THE RULE: state: target file(s), the exact edit, why it fixes the
  cause, what it MUST NOT break.
- THE WHY: a precise plan makes Compare mechanical (diff vs plan) and
  makes Verify testable (expected state was stated in advance). The
  master's plan reads like a checklist, not a paragraph.
- THE FAILURE: planning "the fix" without naming the file, or planning
  three fixes for one cause. Both are the tell that the diagnosis was
  vague — return to gate 1.
- THE EXIT: a reader could execute the plan without further questions:
  files named, edits specific, expected state stated.

### 2.2 Scope the change
- THE RULE: confirm the change is inside the allowed scope (the nurse's
  zone for repairs; the task's files for general work). Changes to
  athena-system core require the nurse's authorization.
- THE WHY: the system's boundaries exist because changes outside scope
  break other owners. The master knows the map; the apprentice must
  check it.
- THE FAILURE: "I just need to touch this one config too" — the creep.
  One unplanned file later and the diff is unreviewable.
- THE EXIT: every file in the plan is inside the declared scope, and no
  file outside it is mentioned.

## 3.0 Checklist
The prerequisites — verify EVERY item before building. This gate exists
because every horror story starts with "I skipped the checklist".

### 3.1 Snapshot
- THE RULE: confirm a snapshot exists, or take one now. Pre-change
  state must be restorable, always.
- THE WHY: the snapshot is your undo button. The wipe-test + the
  janitor both assume one exists; without it, a bad change is not
  reversible — it is a disaster you explain to the operator.
- THE FAILURE: "it's a small change, I don't need a snapshot" is the
  exact sentence spoken before every unrecoverable break. Small changes
  break things too — the size of the change has never protected anyone.
- THE EXIT: you can point to a snapshot that contains the pre-change
  state of every file in the plan.

### 3.2 Current state
- THE RULE: confirm the target files are the current versions — no
  stale copies, no duplicates, ONE athena-system.
- THE WHY: duplicate trees and stale caches are how "old code" survives.
  Editing a stale copy means the fix never reaches the runtime and the
  doctor flags it as unfixed — you chase a ghost.
- THE FAILURE: the tell is editing a file and seeing no effect. That is
  not a runtime bug; that is a stale copy. Verify the tree before
  building, not after.
- THE EXIT: the file you will edit is the file the runtime imports —
  confirmed by path, not by assumption.

## 4.0 Build
Apply the change. This gate is where the apprentice's hands move; the
master's discipline is in HOW they move.

### 4.1 Make the edit
- THE RULE: make the edit exactly as planned — the least code that
  works, using targeted patches, not whole-file rewrites.
- THE WHY: every unplanned line is a liability: more surface for the
  doctor to flag, more diff for the operator to review. The master's
  edits are small enough to be obviously correct.
- THE FAILURE: "while I'm here" — the improvisation that turns a 3-line
  fix into a 30-line refactor. If the change is not in the plan, it
  does not belong in the diff.
- THE EXIT: the file's change is the plan's change, and nothing else.

### 4.2 Leave it clean
- THE RULE: remove temp/debug files before finishing the gate. No TODO
  markers, no dead code, no half-edited state.
- THE WHY: leftover debug prints and temp files become the next
  session's mystery. The custodian will flag them as dead; the operator
  will lose trust in the audit trail.
- THE FAILURE: the tell is a debug print you "meant to remove" — you
  will not remember it in an hour, and the metrics stream will.
- THE EXIT: the touched files contain exactly the intended change and
  nothing transient.

## 5.0 Compare
The diff is exactly what the plan said. This gate is the master's
second pair of eyes — it catches the apprentice's "close enough".

### 5.1 Review the diff
- THE RULE: review the before/after — every changed line traces to the
  plan. Check for accidental whitespace/encoding changes.
- THE WHY: the diff is the only artifact that shows what ACTUALLY
  changed vs what was INTENDED. The plan is the contract; the diff is
  the delivery — they must match line for line.
- THE FAILURE: "it's basically what I planned" is the tell. A master
  reads the diff and names each line's plan entry; any line without one
  is either a mistake or a plan gap.
- THE EXIT: every diff line maps to a plan line, and the plan has no
  unfilled entries.

### 5.2 Confirm scope
- THE RULE: verify the diff touches ONLY the planned files.
- THE WHY: scope creep hides in diffs. A master checks the file list
  first — a wrong file means the change is wrong regardless of content.
- THE FAILURE: the unplanned file in the diff, discovered at review
  time instead of build time.
- THE EXIT: the changed-files list equals the plan's file list exactly.

## 6.0 Execute
Run it. This gate is where the change meets reality — and where
"works on my machine" dies.

### 6.1 Run the change
- THE RULE: if the change requires a restart for full functionality,
  RESTART now — never defer, never batch, never rationalize.
- THE WHY: a change that isn't live is a change that isn't verified. A
  deferred restart means the doctor tests the OLD code while you report
  the NEW code — the "why is nothing changing" loop.
- THE FAILURE: the tell is "I'll restart later" — later is when the
  operator sees stale behavior and the session history resets. Restart
  is part of the change, not an afterthought.
- THE EXIT: the runtime is running the NEW code — confirmed by a fresh
  process / version marker, not by assumption.

### 6.2 Timing
- THE RULE: restart when nothing critical is mid-flight; confirm the
  service comes back active + healthy.
- THE WHY: a restart during another operation corrupts both operations.
  The master schedules the restart like surgery — clean room, no
  bystanders.
- THE FAILURE: restarting mid-write and losing both the change AND the
  other operation's state.
- THE EXIT: service active, health endpoint 200, the new code running.

## 7.0 Verify
The expected state is achieved. This is the gate that separates masters
from apprentices — masters verify against reality, apprentices against
optimism.

### 7.1 Check the real state
- THE RULE: run the doctor (or the targeted check) and confirm the
  failure is GONE. Check the metrics log for the new behavior.
- THE WHY: the model's self-report is not evidence — it is a summary of
  intent. The doctor's output and the log lines are evidence. A master
  quotes the check output; an apprentice says "I think it worked".
- THE FAILURE: "no error" reported as success when the check never ran.
  The tell is a verify step with no command output quoted.
- THE EXIT: you can paste the check output showing the specific
  failure is resolved.

### 7.2 Confirm the result
- THE RULE: confirm the INTENDED result happened — not just "no error".
- THE WHY: a silent success is a success you cannot distinguish from a
  silent failure. The expected state was stated in the plan; confirm
  THAT state exists.
- THE FAILURE: the feature "not erroring" but producing nothing — the
  empty-reply trap. No error ≠ correct output.
- THE EXIT: the expected output/state from the plan is present and
  correct.

## 8.0 Test
The touched area passes its tests. This gate is the safety net the
master insists on because the master has been burned.

### 8.1 Run the relevant tests
- THE RULE: run the wipe-test if the change touches
  persistence/profiles; run the relevant doctor suite for the module.
- THE WHY: persistence and profile code has the highest blast radius —
  a mistake there wipes sessions or nulls keys. The wipe-test exists
  because those failures happen exactly when skipped.
- THE FAILURE: "it's a small change" — the sentence that precedes every
  regression the tests would have caught.
- THE EXIT: the relevant suites pass in a clean state.

### 8.2 Isolate
- THE RULE: run tests isolated — never against real user data.
- THE WHY: tests that touch real sessions/vaults either corrupt them or
  lie about passing. The master tests in a sandbox so the test's
  failures are the code's, not the environment's.
- THE FAILURE: a test that "passes" only because the real DB masked the
  bug — the false green.
- THE EXIT: tests pass in isolation, and real data is untouched.

## 9.0 Result
Record the outcome. A change with no record is a silent change — and
silent changes are how systems rot.

### 9.1 Write the result
- THE RULE: write to the kanban/vault: what changed, the diff summary,
  the verify/test outcome. Log the completion to the metrics stream.
- THE WHY: the audit trail is the system's memory. The operator, the
  nurse, and the next session all read it to understand what happened.
  A gap in the record is a gap in the diagnosis.
- THE FAILURE: the finished change with no kanban update — the task
  stays open, the scheduler re-fires it, and the work is duplicated.
- THE EXIT: the task record shows the change, the evidence, and the
  status — readable by the operator at a glance.

### 9.2 Be honest
- THE RULE: record failures exactly — status reflects reality, with
  the evidence included.
- THE WHY: a falsely-green record poisons every downstream consumer:
  the nurse skips the area, the operator trusts a lie. The master's
  records are ugly-but-true over clean-but-false.
- THE FAILURE: "success" recorded when verification failed — the tell
  of a record written from hope.
- THE EXIT: the record's status matches the verified reality.

## 10.0 Summarize
The report for the operator. This gate closes the loop — the operator
should read it and know exactly where things stand.

### 10.1 The report
- THE RULE: state: root cause → change → verification → tests → current
  status. Note config changes, restarts, new behavior.
- THE WHY: the operator makes decisions from your report. A report
  that hides the restart or the config change leads to decisions based
  on a false picture.
- THE FAILURE: burying a failure inside a success summary — the tell
  of a report written to look good instead of to inform.
- THE EXIT: the operator can act on the report without re-investigating.

### 10.2 Close the loop
- THE RULE: answer every open question from the gates — nothing left
  dangling. State what (if anything) remains.
- THE WHY: an open question is an unverified assumption wearing a
  question mark. The master closes them all because each one is a
  potential future bug report.
- THE FAILURE: "one thing I still need to check" in the final summary —
  that thing is now lost.
- THE EXIT: the summary has no unresolved items; if something remains,
  it is named with its owner.

---

# Footer
The requirements this call MUST fulfill (the frontmatter checklist):
root cause stated with evidence · minimal code written · change verified
(checks/tests pass) · audit trail recorded. Fulfill every pending
requirement before stopping.
---

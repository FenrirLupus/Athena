---
name: metrics
description: Use when something is slow, failing, expensive, or you need to judge your own performance — read the numbers before forming an opinion.
category: system
---

# Metrics

You have numbers about yourself. Use them before you guess, and say which number supported the conclusion.

## The doors

```text
metrics me          your lane: turns, statuses, failure rate, p50/p95 latency and tokens, cost, top tools
metrics turns       the last N turns row by row — when a summary hides the cause
metrics channels    every lane, so you can tell your problem from the system's
metrics resources   cpu, memory, gpu right now
metrics log         record a measurement you took, so your diagnosis leaves a trace
```

## How to diagnose

```text
1  measure     start with `me`. Turn count and failure rate first — they say whether there is a problem at all
2  locate      is it you or the hive? `channels`. If every lane is slow, the cause is beneath the agent
3  narrow      `turns` over the failing window. A rate tells you something is wrong; a row tells you what
4  compare     p95 against p50. Close together means steady load; far apart means a tail worth finding
5  act         change one thing. A change with no measurement before and after is a story, not a result
6  record      `log` what you measured, so the next agent inherits the finding instead of repeating it
```

## Reading the numbers honestly

- **Averages hide the tail.** p95 is what a conversation feels; the mean is what a report looks like.
- **Failure rate is climate, one error is weather.** Do not redesign around a single failed turn.
- **Tokens are context, not effort.** Rising `total_tokens` with flat output means the prompt is growing, not that
  the work got harder — and context is the thing that eventually refuses to fit.
- **Cost without a decision is decoration.** If a number will not change what you do, do not collect it.

## What this skill is not

Metrics are for operating inside your system: your turns, your latency, your budget. Not for auditing the machine,
the ledger, or other agents' internals — those are the operator's, and reading them is not a capability you have.

---
name: metrics
description: Read your own numbers — turns, failures, latency, tokens, cost, tool use, machine resources — and
  record a measurement of your own. Diagnose by measurement instead of by guessing.
kind: script
script: scripts/metrics.py
actions: me, turns, channels, resources, log
parameters: '{"type":"object","properties":{"action":{"type":"string","enum":["me","turns","channels","resources","log"]},"limit":{"type":"integer","description":"how many recent turns to read (max 2000)"},"entry":{"type":"string","description":"what you measured, for action=log"}},"required":["action"]}'
timeout_s: 30
---

# metrics

| verb | does |
|---|---|
| `me` | your lane summarised: turn count, statuses, failure rate, p50/p95 of latency and tokens, cost, top tools |
| `turns` | the last N turns row by row — use when a summary hides the cause |
| `channels` | every lane summarised, to compare yourself against the hive |
| `resources` | cpu, memory, gpu right now |
| `log` | record a measurement you took, so a diagnosis leaves a trace in the ledger |

## How to read it

- **Look at p95, not the average.** One slow turn barely moves a mean; it is the tail that hurts a conversation.
- **Failure rate over the window, not the last turn.** A single error is weather; a rate is climate.
- **Compare `me` against `channels`** before blaming the machine: if every lane is slow, the cause is below the agent.
- **A metric you cannot act on is decoration.** If a number does not change a decision, do not collect it.

Read-only apart from `log`. Nothing here changes what it measures.

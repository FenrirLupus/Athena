---
name: github
description: Manage a GitHub repository end to end — status, repos, issues, pull requests, branches, releases,
  workflow runs, file contents and search. Reads are free; destructive verbs need confirm=true; the token is never
  returned.
kind: script
script: scripts/github.py
actions: status, repo, issue, pr, branch, release, action, contents, search
parameters: '{"type":"object","properties":{"action":{"type":"string","enum":["status","repo","issue","pr","branch","release","action","contents","search"]},"sub":{"type":"string","description":"the verb inside the action, e.g. view|list|create|comment|merge|run-logs"},"repo":{"type":"string","description":"owner/name"},"owner":{"type":"string"},"name":{"type":"string"},"private":{"type":"boolean"},"description":{"type":"string"},"auto_init":{"type":"boolean"},"dest":{"type":"string"},"number":{"type":"integer"},"title":{"type":"string"},"body":{"type":"string"},"labels":{"type":"string"},"assignees":{"type":"string"},"state":{"type":"string"},"head":{"type":"string"},"base":{"type":"string"},"draft":{"type":"boolean"},"method":{"type":"string"},"event":{"type":"string"},"sha":{"type":"string"},"tag":{"type":"string"},"id":{"type":"integer"},"workflow":{"type":"string"},"ref":{"type":"string"},"inputs":{"type":"string"},"path":{"type":"string"},"content":{"type":"string"},"message":{"type":"string"},"query":{"type":"string"},"kind":{"type":"string"},"limit":{"type":"integer"},"confirm":{"type":"boolean","description":"required for destructive verbs: repo delete, branch delete, release delete, contents delete, pr merge, rerun"}},"required":["action"]}'
timeout_s: 120
---

# github

One capability, every verb a repository needs. `action` picks the area, `sub` picks the verb inside it.

| action | verbs |
|---|---|
| `status` | who you are, rate limits left |
| `repo` | `view` `list` `create` `fork` `clone` `delete`* |
| `issue` | `list` `view` `create` `comment` `close` `reopen` `label` `assign` |
| `pr` | `list` `view` `diff` `checks` `create` `merge`* `review` `comment` |
| `branch` | `list` `create` `delete`* |
| `release` | `list` `create` `delete`* |
| `action` | `list` `view` `jobs` `logs` `rerun`* `dispatch` |
| `contents` | `read` `write` `delete`* |
| `search` | `repos` `code` `issues` `prs` |

\* destructive: refused unless `confirm=true`.

## Credentials

The tool never holds a token. It calls `gh`, which keeps its own credential in the system keyring, and it strips
`GH_TOKEN`/`GITHUB_TOKEN` from the child environment so a token in the caller's environment cannot be exfiltrated
through a command line. Anything matching a token pattern in command output is redacted before it is returned.

## What it will not do

- Print or return a credential.
- Delete a repository, branch, release or file, merge a pull request, or re-run a workflow without `confirm=true`.
- Guess a repository: `owner/name` is required wherever a write happens.

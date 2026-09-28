---
name: github
description: "GitHub — the FULL surface: repo create/view/clone/push/pull/duplicate/delete/archive/rename/merge, releases, fork, PRs, issues, labels, workflows, secrets, api."
---

# GitHub

The **github** tool drives GitHub through the authenticated `gh` CLI
(the operator's identity via the keyring). It covers EVERY GitHub
function for BOTH roles (the Operator's 08-12 spec):

## HOST — Athena owns the repo

```
github {"action": "status"}
github {"action": "repo_create", "name": "new-project", "private": false, "description": "..."}
github {"action": "repo_view", "repo": "FenrirLupus/Athena-Agent"}
github {"action": "repo_clone", "repo": "FenrirLupus/Athena-Agent", "dir": "athena-agent"}
github {"action": "repo_push", "remote": "origin", "branch": "main"}    # update her own repo
github {"action": "repo_pull", "remote": "origin", "branch": "main"}    # sync local
github {"action": "repo_duplicate", "source": "FenrirLupus/Athena-Agent", "new_name": "athena-copy"}
github {"action": "repo_merge", "branch": "feature"}                    # merge a branch
github {"action": "repo_archive", "repo": "old/project", "archive": true}
github {"action": "repo_rename", "repo": "old/name", "new_name": "new-name"}
github {"action": "repo_edit", "repo": "FenrirLupus/Athena-Agent", "description": "...", "topics": ["ai", "agent"]}
github {"action": "release", "repo": "FenrirLupus/Athena-Agent", "tag": "v1.0.0", "title": "1.0.0", "notes": "..."}
github {"action": "release_list", "repo": "FenrirLupus/Athena-Agent"}
github {"action": "workflow_list", "repo": "FenrirLupus/Athena-Agent"}
github {"action": "run_list", "repo": "FenrirLupus/Athena-Agent"}
github {"action": "secret_list", "repo": "FenrirLupus/Athena-Agent"}
```

## CLIENT — Athena contributes to others

```
github {"action": "fork", "repo": "someone/project", "clone": true}
github {"action": "pr_create", "repo": "someone/project", "title": "Fix", "base": "main", "head": "patch-1"}
github {"action": "pr_list", "repo": "someone/project", "state": "open"}
github {"action": "pr_view", "repo": "someone/project", "number": "12"}
github {"action": "pr_merge", "repo": "someone/project", "number": "12", "method": "squash"}
github {"action": "pr_close", "repo": "someone/project", "number": "12"}
github {"action": "pr_checkout", "repo": "someone/project", "number": "12"}
github {"action": "issue_create", "repo": "someone/project", "title": "Bug", "body": "..."}
github {"action": "issue_edit", "repo": "someone/project", "number": "5", "labels": ["bug"]}
github {"action": "label_create", "repo": "someone/project", "name": "bug", "color": "d73a4a"}
github {"action": "gist", "files": ["notes.txt"], "description": "..."}
```

## Everything else (the api passthrough)

Any GitHub function not listed above is reachable through the REST API:

```
github {"action": "api", "method": "GET", "endpoint": "repos/FenrirLupus/Athena-Agent/branches"}
github {"action": "api", "method": "POST", "endpoint": "repos/FenrirLupus/Athena-Agent/deployments", "body": "{\"ref\":\"main\"}"}
github {"action": "rate_limit"}
```

## When to use

- The operator wants Athena to manage her own repos (host).
- The operator wants Athena to contribute to an upstream repo (client).
- Any GitHub lifecycle action.

## Requirements (credentials)

- **`gh` CLI authenticated** — the tool uses the operator's GitHub
  identity (gh keyring, `gh auth login`). Without it, every action
  returns "gh CLI not available/authenticated".
- Check first: `github {"action": "status"}` → `available: true`.
- No token is stored in `.secret` — auth is the gh keyring's.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/github.py` — registers `github`.

## Backend

- `core/github.py` — the gh CLI wrapper (host + client actions)

---
---

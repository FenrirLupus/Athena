---
name: github
description: "Use the built-in github tool for the FULL GitHub surface — host repos and contribute upstream."
---

# GitHub

The built-in `github` tool drives GitHub through the authenticated gh
CLI — the FULL surface for both roles:

**Host (Athena's own repos):**
```
github {"action": "repo_create", "name": "new-project"}
github {"action": "repo_clone", "repo": "FenrirLupus/Athena-Agent"}
github {"action": "repo_push", "branch": "main"}
github {"action": "repo_pull", "branch": "main"}
github {"action": "repo_duplicate", "source": "FenrirLupus/Athena-Agent", "new_name": "copy"}
github {"action": "repo_merge", "branch": "feature"}
github {"action": "release", "repo": "FenrirLupus/Athena-Agent", "tag": "v1.0.0"}
```

**Client (contributing to others):**
```
github {"action": "fork", "repo": "someone/project", "clone": true}
github {"action": "pr_create", "repo": "someone/project", "title": "Fix", "head": "patch-1"}
github {"action": "pr_merge", "repo": "someone/project", "number": "12"}
github {"action": "issue_create", "repo": "someone/project", "title": "Bug"}
```

**Anything else** — the api passthrough:
```
github {"action": "api", "method": "GET", "endpoint": "repos/owner/repo/branches"}
```

Check auth first with `{"action": "status"}`.

**Requirements:** `gh` CLI authenticated (the operator's keyring —
`gh auth login`). Without it, all actions fail with "gh CLI not
available/authenticated".

---
---

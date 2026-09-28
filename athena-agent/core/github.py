"""GitHub backend — Athena's gh CLI wrapper.

The Operator's 08-12 spec: a GitHub tool with EVERY function GitHub has —
for BOTH roles:
  HOST   — Athena owns the repo: create, clone, push, pull, duplicate,
           delete, merge, archive, rename, releases, workflows
  CLIENT — Athena contributes to others: fork, PR (create/view/merge/
           close/checkout), issues (create/view/edit/close), labels

The backend uses the `gh` CLI (the hands-off button — the established
model) with the `git` + curl fallback where sensible. Auth comes from
the user's gh keyring (the operator's identity), never hard-coded.

The tools that use this: tools/github/scripts/github.py
"""

from __future__ import annotations

import json
import shutil
import subprocess


def _gh() -> str:
    return shutil.which("gh") or "gh"


def available() -> bool:
    """Is gh installed and authenticated?"""
    try:
        r = subprocess.run([_gh(), "auth", "status"],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def whoami() -> str:
    try:
        r = subprocess.run([_gh(), "api", "user", "--jq", ".login"],
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _run(args: list[str], timeout: float = 60.0) -> dict:
    """Run gh with the given args. Returns {ok, stdout, stderr, detail}."""
    try:
        r = subprocess.run([_gh(), *args], capture_output=True, text=True,
                           timeout=timeout)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        return {"ok": r.returncode == 0, "stdout": out, "stderr": err,
                "detail": out or err or "(no output)"}
    except Exception as exc:
        from core.logging import log_event
        log_event(4, f"github call failed: {args}: {exc}", source="core",
                  action="github")
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _git(args: list[str], timeout: float = 120.0) -> dict:
    """Run a plain git command (local repo operations)."""
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True,
                           timeout=timeout)
        return {"ok": r.returncode == 0,
                "detail": (r.stdout or r.stderr or "(no output)").strip()[:400]}
    except Exception as exc:
        from core.logging import log_event
        log_event(4, f"git call failed: {args}: {exc}", source="core",
                  action="github")
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


# ── HOST-role actions ────────────────────────────────────────────────
def repo_create(name: str, private: bool = False, description: str = "",
                timeout: float = 60.0) -> dict:
    args = ["repo", "create", name]
    if private:
        args.append("--private")
    if description:
        args += ["--description", description]
    return _run(args, timeout)


def repo_view(repo: str, timeout: float = 30.0) -> dict:
    return _run(["repo", "view", repo, "--json",
                 "name,owner,description,url,defaultBranchRef,visibility"],
                timeout)


def repo_clone(repo: str, dir_name: str = "", timeout: float = 120.0) -> dict:
    args = ["repo", "clone", repo]
    if dir_name:
        args.append(dir_name)
    return _run(args, timeout)


def repo_push(remote: str = "origin", branch: str = "", timeout: float = 60.0) -> dict:
    """Push the current repo's branch to its remote (host updates self)."""
    args = ["push", remote]
    if branch:
        args.append(branch)
    return _git(args, timeout)


def repo_pull(remote: str = "origin", branch: str = "", timeout: float = 120.0) -> dict:
    """Pull the latest from the remote (host updates local)."""
    args = ["pull", remote]
    if branch:
        args.append(branch)
    return _git(args, timeout)


def repo_delete(repo: str, confirm: bool = False, timeout: float = 60.0) -> dict:
    """Delete a repo (the Operator's full-surface spec — destructive)."""
    args = ["repo", "delete", repo, "--yes"] if confirm else ["repo", "delete", repo]
    return _run(args, timeout)


def repo_duplicate(source: str, new_name: str = "", timeout: float = 240.0) -> dict:
    """Duplicate a repo: clone → create a new repo → push all branches.

    Works for BOTH roles — fork (same-owner copy) or a fresh repo the
    operator owns (the Operator's 'duplicate' requirement).
    """
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix="gh-dup-") as td:
        target = str(Path(td) / "src")
        c = _run(["repo", "clone", source, target], timeout)
        if not c.get("ok"):
            return c
        import os
        env = dict(os.environ)
        r = subprocess.run(["gh", "repo", "create", new_name, "--source",
                            source, "--push"],
                           capture_output=True, text=True, timeout=timeout,
                           env=env)
        if r.returncode == 0:
            return {"ok": True, "detail": (r.stdout or r.stderr).strip()[:400]}
        # Fallback: create empty + push.
        r2 = subprocess.run(["gh", "repo", "create", new_name, "--private"],
                            capture_output=True, text=True, timeout=timeout, env=env)
        if r2.returncode != 0:
            return {"ok": False, "detail": (r2.stderr or r2.stdout).strip()[:400]}
        new_url = (r2.stdout or r2.stderr).strip().splitlines()[-1] if (r2.stdout or r2.stderr) else ""
        push = subprocess.run(["git", "-C", target, "remote", "set-url", "origin", new_url],
                              capture_output=True, text=True, timeout=30, env=env)
        push2 = subprocess.run(["git", "-C", target, "push", "-u", "origin", "--all"],
                               capture_output=True, text=True, timeout=timeout, env=env)
        return {"ok": push2.returncode == 0,
                "detail": (push2.stdout or push2.stderr or "(no output)").strip()[:400]}


def repo_archive(repo: str, archive: bool = True, timeout: float = 60.0) -> dict:
    args = ["repo", "archive", repo]
    if not archive:
        args.append("--unarchive")
    return _run(args, timeout)


def repo_rename(repo: str, new_name: str, timeout: float = 60.0) -> dict:
    return _run(["repo", "rename", new_name, "--repo", repo], timeout)


def repo_edit(repo: str, description: str = "", homepage: str = "",
              topics: list[str] | None = None, visibility: str = "",
              timeout: float = 60.0) -> dict:
    args = ["repo", "edit", repo]
    if description:
        args += ["--description", description]
    if homepage:
        args += ["--homepage", homepage]
    if topics:
        args += ["--add-topic", ",".join(topics)]
    if visibility in ("public", "private", "internal"):
        args += ["--visibility", visibility]
    return _run(args, timeout)


def repo_release(repo: str, tag: str, title: str = "", notes: str = "",
                 timeout: float = 60.0) -> dict:
    args = ["release", "create", tag, "--repo", repo]
    if title:
        args += ["--title", title]
    if notes:
        args += ["--notes", notes]
    return _run(args, timeout)


def release_list(repo: str, limit: int = 10, timeout: float = 30.0) -> dict:
    return _run(["release", "list", "--repo", repo, "--limit", str(limit)],
                timeout)


def release_view(repo: str, tag: str, timeout: float = 30.0) -> dict:
    return _run(["release", "view", tag, "--repo", repo], timeout)


def repo_merge(branch: str, timeout: float = 60.0) -> dict:
    """Merge a branch into the current branch (local git merge)."""
    return _git(["merge", branch], timeout)


# ── CLIENT-role actions ──────────────────────────────────────────────
def repo_fork(repo: str, clone: bool = False, timeout: float = 120.0) -> dict:
    args = ["repo", "fork", repo]
    if clone:
        args.append("--clone")
    return _run(args, timeout)


def pr_create(repo: str, title: str, body: str = "", base: str = "main",
              head: str = "", timeout: float = 60.0) -> dict:
    args = ["pr", "create", "--repo", repo, "--title", title,
            "--base", base]
    if head:
        args += ["--head", head]
    if body:
        args += ["--body", body]
    return _run(args, timeout)


def pr_list(repo: str, state: str = "open", timeout: float = 30.0) -> dict:
    return _run(["pr", "list", "--repo", repo, "--state", state], timeout)


def pr_view(repo: str, number: str, timeout: float = 30.0) -> dict:
    return _run(["pr", "view", number, "--repo", repo], timeout)


def pr_merge(repo: str, number: str, method: str = "merge",
             delete_branch: bool = False, timeout: float = 60.0) -> dict:
    args = ["pr", "merge", number, "--repo", repo, "--" + method]
    if delete_branch:
        args.append("--delete-branch")
    return _run(args, timeout)


def pr_close(repo: str, number: str, timeout: float = 60.0) -> dict:
    return _run(["pr", "close", number, "--repo", repo], timeout)


def pr_reopen(repo: str, number: str, timeout: float = 60.0) -> dict:
    return _run(["pr", "reopen", number, "--repo", repo], timeout)


def pr_checkout(repo: str, number: str, timeout: float = 120.0) -> dict:
    return _run(["pr", "checkout", number, "--repo", repo], timeout)


def pr_diff(repo: str, number: str, timeout: float = 60.0) -> dict:
    return _run(["pr", "diff", number, "--repo", repo], timeout)


def issue_create(repo: str, title: str, body: str = "",
                 timeout: float = 60.0) -> dict:
    args = ["issue", "create", "--repo", repo, "--title", title]
    if body:
        args += ["--body", body]
    return _run(args, timeout)


def issue_list(repo: str, state: str = "open", timeout: float = 30.0) -> dict:
    return _run(["issue", "list", "--repo", repo, "--state", state], timeout)


def issue_view(repo: str, number: str, timeout: float = 30.0) -> dict:
    return _run(["issue", "view", number, "--repo", repo], timeout)


def issue_close(repo: str, number: str, timeout: float = 60.0) -> dict:
    return _run(["issue", "close", number, "--repo", repo], timeout)


def issue_edit(repo: str, number: str, title: str = "", body: str = "",
               labels: list[str] | None = None, timeout: float = 60.0) -> dict:
    args = ["issue", "edit", number, "--repo", repo]
    if title:
        args += ["--title", title]
    if body:
        args += ["--body", body]
    if labels:
        args += ["--add-label", ",".join(labels)]
    return _run(args, timeout)


def label_create(repo: str, name: str, color: str = "ededed",
                 description: str = "", timeout: float = 60.0) -> dict:
    args = ["label", "create", name, "--repo", repo, "--color", color]
    if description:
        args += ["--description", description]
    return _run(args, timeout)


def label_list(repo: str, timeout: float = 30.0) -> dict:
    return _run(["label", "list", "--repo", repo], timeout)


def gist_create(files: list[str], description: str = "",
                timeout: float = 60.0) -> dict:
    args = ["gist", "create"]
    if description:
        args += ["--desc", description]
    args += files
    return _run(args, timeout)


def search_repos(query: str, limit: int = 10, timeout: float = 30.0) -> dict:
    return _run(["search", "repos", query, "--limit", str(limit)], timeout)


def workflow_list(repo: str, timeout: float = 30.0) -> dict:
    return _run(["workflow", "list", "--repo", repo], timeout)


def run_list(repo: str, timeout: float = 30.0) -> dict:
    return _run(["run", "list", "--repo", repo], timeout)


def secret_list(repo: str, timeout: float = 30.0) -> dict:
    return _run(["secret", "list", "--repo", repo], timeout)


def secret_set(repo: str, name: str, value: str, timeout: float = 60.0) -> dict:
    """Set a repo secret (the Operator's full-surface spec — sensitive)."""
    import os
    env = dict(os.environ)
    r = subprocess.run(["gh", "secret", "set", name, "--repo", repo,
                        "--body", value],
                       capture_output=True, text=True, timeout=timeout, env=env)
    return {"ok": r.returncode == 0,
            "detail": (r.stdout or r.stderr or "(no output)").strip()[:400]}


def api(method: str, endpoint: str, body: str = "", timeout: float = 60.0) -> dict:
    """General gh api passthrough — covers EVERY other GitHub function."""
    args = ["api", "-X", method.upper(), endpoint]
    if body:
        args += ["--input", "-"]
    try:
        if body:
            r = subprocess.run([_gh(), *args], input=body, capture_output=True,
                               text=True, timeout=timeout)
        else:
            r = subprocess.run([_gh(), *args], capture_output=True, text=True,
                               timeout=timeout)
        return {"ok": r.returncode == 0,
                "detail": (r.stdout or r.stderr or "(no output)").strip()[:2000]}
    except Exception as exc:
        from core.logging import log_event
        log_event(4, f"github api failed: {endpoint}: {exc}", source="core",
                  action="github")
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def rate_limit(timeout: float = 30.0) -> dict:
    return _run(["api", "rate_limit"], timeout)

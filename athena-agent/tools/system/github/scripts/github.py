#!/usr/bin/env python3
"""GitHub: one capability, every verb a repository needs.

Talks to GitHub through the `gh` CLI, which holds the credential — this script never reads, prints or returns a token,
and redacts anything that looks like one from command output. Destructive verbs require confirm=true.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

TOKENISH = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
MAX_BYTES = 120_000

try:                                  # the program tree is on sys.path only when this runs in-process; a contained
    from core import trace            # subprocess has no such tree, and tracing is a diagnostic, not a dependency
except ImportError:                   # pragma: no cover — the out-of-process path
    from contextlib import nullcontext

    class _SilentSpan:
        def ok(self, **_fields) -> None:
            return None

        def failed(self, _exc) -> None:
            return None

    class _SilentTrace:
        def span(self, _name: str, **_fields):
            return nullcontext(_SilentSpan())

    trace = _SilentTrace()


class GitHubError(RuntimeError):
    """A call GitHub would not make, with the reason it gave."""


def _gh(args: list[str], *, input_text: str | None = None, timeout: int = 45) -> Any:
    gh = shutil.which("gh")
    if not gh:
        raise GitHubError("the gh CLI is not installed; nothing can reach GitHub without it")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GH_TOKEN") and not k.startswith("GITHUB_TOKEN")}
    proc = subprocess.run([gh] + args, capture_output=True, text=True, timeout=timeout, env=env,
                          input=input_text)
    out, err = TOKENISH.sub("[REDACTED]", proc.stdout), TOKENISH.sub("[REDACTED]", proc.stderr)
    if proc.returncode != 0:
        raise GitHubError(err.strip()[:600] or out.strip()[:600] or f"gh exited {proc.returncode}")
    out = out.strip()
    if not out:
        return {"ok": True}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"ok": True, "text": out[:MAX_BYTES]}


def _need(args: dict, *names: str) -> None:
    missing = [n for n in names if not str(args.get(n) or "").strip()]
    if missing:
        raise GitHubError(f"missing required argument(s): {', '.join(missing)}")


def _confirm(args: dict, what: str) -> None:
    if not args.get("confirm"):
        raise GitHubError(f"{what} is destructive; call again with confirm=true if you mean it")


def _api(path: str, method: str = "GET", body: dict | None = None, fields: dict | None = None) -> Any:
    argv = ["api", "-X", method, path]
    for key, value in (fields or {}).items():
        argv += ["-f", f"{key}={value}"]
    if body is not None:
        argv += ["--input", "-"]
        return _gh(argv, input_text=json.dumps(body))
    return _gh(argv)


# ---- status ---------------------------------------------------------------------------------------------------
def status(args: dict) -> dict:
    me = _api("user")
    limits = _api("rate_limit")["resources"]
    return {
        "ok": True, "login": me.get("login"), "name": me.get("name"),
        "repos": me.get("public_repos"), "scopes_hint": "see gh auth status on the host (never returned here)",
        "rate": {k: {"remaining": limits[k]["remaining"], "limit": limits[k]["limit"]}
                 for k in ("core", "search", "graphql") if k in limits},
    }


# ---- repository -----------------------------------------------------------------------------------------------
def repo(args: dict) -> dict:
    action = str(args.get("sub") or "view")
    if action == "view":
        _need(args, "repo")
        return _api(f"repos/{args['repo']}")
    if action == "list":
        who = args.get("owner") or _api("user").get("login")
        return _api(f"users/{who}/repos?per_page={int(args.get('limit') or 30)}&sort=updated")
    if action == "create":
        _need(args, "name")
        body = {"name": args["name"], "private": bool(args.get("private")),
                "description": args.get("description") or "", "auto_init": bool(args.get("auto_init"))}
        return _api("user/repos", "POST", body=body)
    if action == "fork":
        _need(args, "repo")
        return _api(f"repos/{args['repo']}/forks", "POST")
    if action == "clone":
        _need(args, "repo")
        dest = str(args.get("dest") or "./" + str(args["repo"]).split("/")[-1])
        return _gh(["repo", "clone", args["repo"], dest])
    if action == "delete":
        _need(args, "repo")
        _confirm(args, f"deleting {args['repo']}")
        return _api(f"repos/{args['repo']}", "DELETE")
    raise GitHubError(f"unknown repo sub-action {action!r}; use view, list, create, fork, clone, delete")


# ---- issues ---------------------------------------------------------------------------------------------------
def issue(args: dict) -> dict:
    action = str(args.get("sub") or "list")
    _need(args, "repo")
    base = f"repos/{args['repo']}/issues"
    if action == "list":
        state = args.get("state") or "open"
        return _api(f"{base}?state={state}&per_page={int(args.get('limit') or 30)}")
    if action == "view":
        _need(args, "number")
        return _api(f"{base}/{int(args['number'])}")
    if action == "create":
        _need(args, "title")
        body = {"title": args["title"], "body": args.get("body") or "",
                "labels": [x for x in str(args.get("labels") or "").split(",") if x],
                "assignees": [x for x in str(args.get("assignees") or "").split(",") if x]}
        return _api(base, "POST", body=body)
    if action == "comment":
        _need(args, "number", "body")
        return _api(f"{base}/{int(args['number'])}/comments", "POST", body={"body": args["body"]})
    if action in ("close", "reopen"):
        _need(args, "number")
        return _api(base + f"/{int(args['number'])}", "PATCH", body={"state": "closed" if action == "close" else "open"})
    if action == "label":
        _need(args, "number", "labels")
        return _api(f"{base}/{int(args['number'])}/labels", "POST",
                    body={"labels": [x for x in str(args["labels"]).split(",") if x]})
    if action == "assign":
        _need(args, "number", "assignees")
        return _api(f"{base}/{int(args['number'])}/assignees", "POST",
                    body={"assignees": [x for x in str(args["assignees"]).split(",") if x]})
    raise GitHubError(f"unknown issue sub-action {action!r}; use list, view, create, comment, close, reopen, label, assign")


# ---- pull requests --------------------------------------------------------------------------------------------
def pr(args: dict) -> dict:
    action = str(args.get("sub") or "list")
    _need(args, "repo")
    base = f"repos/{args['repo']}/pulls"
    if action == "list":
        return _api(f"{base}?state={args.get('state') or 'open'}&per_page={int(args.get('limit') or 30)}")
    if action == "view":
        _need(args, "number")
        return _api(f"{base}/{int(args['number'])}")
    if action == "diff":
        _need(args, "number")
        return {"ok": True, "text": _gh(["pr", "diff", str(args["number"]), "-R", args["repo"]])}
    if action == "checks":
        _need(args, "number")
        return _api(f"repos/{args['repo']}/commits/{(args.get('sha') or '')}/check-runs") if args.get("sha") else \
            _gh(["pr", "checks", str(args["number"]), "-R", args["repo"]])
    if action == "create":
        _need(args, "title", "head", "base")
        body = {"title": args["title"], "head": args["head"], "base": args["base"],
                "body": args.get("body") or "", "draft": bool(args.get("draft"))}
        return _api(base, "POST", body=body)
    if action == "merge":
        _need(args, "number")
        _confirm(args, f"merging #{args['number']} in {args['repo']}")
        return _api(f"{base}/{int(args['number'])}/merge", "PUT",
                    body={"merge_method": args.get("method") or "squash"})
    if action == "review":
        _need(args, "number", "event")
        return _api(f"{base}/{int(args['number'])}/reviews", "POST",
                    body={"event": args["event"], "body": args.get("body") or ""})
    if action == "comment":
        _need(args, "number", "body")
        return _api(f"repos/{args['repo']}/issues/{int(args['number'])}/comments", "POST", body={"body": args["body"]})
    raise GitHubError(f"unknown pr sub-action {action!r}; use list, view, diff, checks, create, merge, review, comment")


# ---- branches, releases, actions, files, search ---------------------------------------------------------------
def branch(args: dict) -> dict:
    action = str(args.get("sub") or "list")
    _need(args, "repo")
    if action == "list":
        return _api(f"repos/{args['repo']}/branches?per_page={int(args.get('limit') or 50)}")
    if action == "create":
        _need(args, "name", "sha")
        return _api(f"repos/{args['repo']}/git/refs", "POST", body={"ref": f"refs/heads/{args['name']}", "sha": args["sha"]})
    if action == "delete":
        _need(args, "name")
        _confirm(args, f"deleting branch {args['name']}")
        return _api(f"repos/{args['repo']}/git/refs/heads/{args['name']}", "DELETE")
    raise GitHubError(f"unknown branch sub-action {action!r}; use list, create, delete")


def release(args: dict) -> dict:
    action = str(args.get("sub") or "list")
    _need(args, "repo")
    if action == "list":
        return _api(f"repos/{args['repo']}/releases?per_page={int(args.get('limit') or 20)}")
    if action == "create":
        _need(args, "tag")
        body = {"tag_name": args["tag"], "name": args.get("title") or args["tag"],
                "body": args.get("body") or "", "draft": bool(args.get("draft")),
                "prerelease": bool(args.get("prerelease"))}
        return _api(f"repos/{args['repo']}/releases", "POST", body=body)
    if action == "delete":
        _need(args, "id")
        _confirm(args, f"deleting release {args['id']}")
        return _api(f"repos/{args['repo']}/releases/{int(args['id'])}", "DELETE")
    raise GitHubError(f"unknown release sub-action {action!r}; use list, create, delete")


def action_run(args: dict) -> dict:
    sub = str(args.get("sub") or "list")
    _need(args, "repo")
    if sub == "list":
        return _api(f"repos/{args['repo']}/actions/runs?per_page={int(args.get('limit') or 20)}")
    if sub == "view":
        _need(args, "id")
        return _api(f"repos/{args['repo']}/actions/runs/{int(args['id'])}")
    if sub == "jobs":
        _need(args, "id")
        return _api(f"repos/{args['repo']}/actions/runs/{int(args['id'])}/jobs")
    if sub == "logs":
        _need(args, "id")
        return {"ok": True, "text": _gh(["run", "view", str(args["id"]), "-R", args["repo"], "--log"], timeout=90)[:MAX_BYTES]}
    if sub == "rerun":
        _need(args, "id")
        _confirm(args, f"re-running workflow run {args['id']}")
        return _api(f"repos/{args['repo']}/actions/runs/{int(args['id'])}/rerun", "POST")
    if sub == "dispatch":
        _need(args, "workflow", "ref")
        body = {"ref": args["ref"], "inputs": json.loads(args.get("inputs") or "{}")}
        return _api(f"repos/{args['repo']}/actions/workflows/{args['workflow']}/dispatches", "POST", body=body)
    raise GitHubError(f"unknown action sub-action {sub!r}; use list, view, jobs, logs, rerun, dispatch")


def contents(args: dict) -> dict:
    action = str(args.get("sub") or "read")
    _need(args, "repo", "path")
    path = str(args["path"]).lstrip("/")
    if action == "read":
        ref = args.get("ref") or ""
        data = _api(f"repos/{args['repo']}/contents/{path}" + (f"?ref={ref}" if ref else ""))
        if isinstance(data, dict) and data.get("content"):
            import base64
            return {"ok": True, "path": path, "encoding": data.get("encoding"),
                    "text": base64.b64decode(data["content"]).decode("utf-8", "replace")[:MAX_BYTES]}
        return data
    if action in ("write", "delete"):
        if action == "delete":
            _confirm(args, f"deleting {path} from {args['repo']}")
        sha = args.get("sha")
        if not sha:
            try:
                current = _api(f"repos/{args['repo']}/contents/{path}")
                sha = current.get("sha") if isinstance(current, dict) else None
            except GitHubError:
                sha = None
        if action == "write":
            _need(args, "content", "message")
            import base64
            body = {"message": args["message"], "content": base64.b64encode(str(args["content"]).encode()).decode()}
            if sha:
                body["sha"] = sha
            return _api(f"repos/{args['repo']}/contents/{path}", "PUT", body=body)
        return _api(f"repos/{args['repo']}/contents/{path}", "DELETE", body={"message": args.get("message") or f"delete {path}", "sha": sha})
    raise GitHubError(f"unknown contents sub-action {action!r}; use read, write, delete")


def search(args: dict) -> dict:
    kind = str(args.get("kind") or "repos")
    _need(args, "query")
    q = str(args["query"]).replace(" ", "+")
    limit = int(args.get("limit") or 20)
    if kind == "repos":
        return _api(f"search/repositories?q={q}&per_page={limit}")
    if kind == "code":
        return _api(f"search/code?q={q}&per_page={limit}")
    if kind == "issues":
        return _api(f"search/issues?q={q}&per_page={limit}")
    if kind == "prs":
        return _api(f"search/issues?q={q}+type:pr&per_page={limit}")
    raise GitHubError(f"unknown search kind {kind!r}; use repos, code, issues, prs")


VERBS = {
    "status": status, "repo": repo, "issue": issue, "pr": pr, "branch": branch,
    "release": release, "action": action_run, "contents": contents, "search": search,
}


def main(args: dict) -> dict:
    """One verb, one pair of lines: the verb and its repository on the way in, the size of the answer on the way out.

    A refusal (an unknown action, a destructive verb without confirm=true) raises inside the span, so the trail
    carries the reason rather than a bare gap.
    """
    verb = str(args.get("action") or "").strip()
    fields = {"verb": verb or "-", "sub": str(args.get("sub") or "-"), "repo": str(args.get("repo") or "-")}
    if verb == "search":
        fields["query_chars"] = len(str(args.get("query") or ""))     # the query text may be the user's data
    with trace.span(f"tool.github.{verb or 'unknown'}", **fields) as sp:
        if verb not in VERBS:
            raise GitHubError(f"unknown action {verb!r}; use one of {sorted(VERBS)}")
        out = VERBS[verb](args)
        sp.ok(size=len(json.dumps(out, default=str)))
        return out

def run(args=None, ctx=None):
    """The entry point the loader calls: `action` selects a verb, the rest are that verb's arguments."""
    from tools.loader import dispatch

    return dispatch(build, args, ctx, "github")

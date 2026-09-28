"""Built-in github tool — the FULL GitHub surface (one tool).

The Operator's 08-12 spec: EVERY function GitHub has, for BOTH the repo HOST
(Athena owns the repo) and the CLIENT (Athena contributes to others):
fork, push, pull, duplicate, delete, merge, archive, rename, releases,
PRs, issues, labels, workflows, secrets, and a general `api` passthrough
that covers anything else. Uses the authenticated `gh` CLI (the
operator's identity via the keyring) — the established model.
"""

import json


def _github(args: dict, timeout: float = 60.0) -> str:
    from core import github as gh
    action = str(args.get("action", "")).strip()
    repo = str(args.get("repo", "")).strip()

    if action == "status":
        return json.dumps({
            "ok": True,
            "available": gh.available(),
            "user": gh.whoami(),
        }, ensure_ascii=False)

    if not gh.available():
        return json.dumps({"ok": False,
                           "detail": "gh CLI not available/authenticated"},
                          ensure_ascii=False)

    # ── auth / info ──────────────────────────────────────────────
    if action == "rate_limit":
        return json.dumps(gh.rate_limit(timeout), ensure_ascii=False)
    if action == "api":
        endpoint = str(args.get("endpoint", "")).strip()
        if not endpoint:
            return json.dumps({"ok": False, "detail": "endpoint required"},
                              ensure_ascii=False)
        return json.dumps(gh.api(str(args.get("method", "GET")), endpoint,
                                 str(args.get("body", "")), timeout),
                          ensure_ascii=False)

    # ── HOST: repo management ────────────────────────────────────
    if action == "repo_create":
        name = str(args.get("name", "")).strip()
        if not name:
            return json.dumps({"ok": False, "detail": "name required"},
                              ensure_ascii=False)
        return json.dumps(gh.repo_create(
            name, private=bool(args.get("private")),
            description=str(args.get("description", "")), timeout=timeout),
            ensure_ascii=False)

    if action == "repo_view":
        if not repo:
            return json.dumps({"ok": False, "detail": "repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.repo_view(repo, timeout), ensure_ascii=False)

    if action == "repo_clone":
        if not repo:
            return json.dumps({"ok": False, "detail": "repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.repo_clone(repo, str(args.get("dir", "")), timeout),
                          ensure_ascii=False)

    if action == "repo_push":
        return json.dumps(gh.repo_push(str(args.get("remote", "origin")),
                                       str(args.get("branch", "")), timeout),
                          ensure_ascii=False)

    if action == "repo_pull":
        return json.dumps(gh.repo_pull(str(args.get("remote", "origin")),
                                       str(args.get("branch", "")), timeout),
                          ensure_ascii=False)

    if action == "repo_duplicate":
        source = str(args.get("source", "")).strip()
        if not source:
            return json.dumps({"ok": False, "detail": "source repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.repo_duplicate(source,
                                            str(args.get("new_name", "")),
                                            timeout), ensure_ascii=False)

    if action == "repo_delete":
        if not repo:
            return json.dumps({"ok": False, "detail": "repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.repo_delete(repo, bool(args.get("confirm")),
                                         timeout), ensure_ascii=False)

    if action == "repo_archive":
        if not repo:
            return json.dumps({"ok": False, "detail": "repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.repo_archive(repo, bool(args.get("archive", True)),
                                          timeout), ensure_ascii=False)

    if action == "repo_rename":
        new_name = str(args.get("new_name", "")).strip()
        if not repo or not new_name:
            return json.dumps({"ok": False, "detail": "repo and new_name required"},
                              ensure_ascii=False)
        return json.dumps(gh.repo_rename(repo, new_name, timeout),
                          ensure_ascii=False)

    if action == "repo_edit":
        if not repo:
            return json.dumps({"ok": False, "detail": "repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.repo_edit(
            repo, description=str(args.get("description", "")),
            homepage=str(args.get("homepage", "")),
            topics=args.get("topics") or None,
            visibility=str(args.get("visibility", "")), timeout=timeout),
            ensure_ascii=False)

    if action == "repo_merge":
        branch = str(args.get("branch", "")).strip()
        if not branch:
            return json.dumps({"ok": False, "detail": "branch required"},
                              ensure_ascii=False)
        return json.dumps(gh.repo_merge(branch, timeout), ensure_ascii=False)

    # ── releases ─────────────────────────────────────────────────
    if action == "release":
        tag = str(args.get("tag", "")).strip()
        if not repo or not tag:
            return json.dumps({"ok": False, "detail": "repo and tag required"},
                              ensure_ascii=False)
        return json.dumps(gh.repo_release(
            repo, tag, title=str(args.get("title", "")),
            notes=str(args.get("notes", "")), timeout=timeout),
            ensure_ascii=False)

    if action == "release_list":
        if not repo:
            return json.dumps({"ok": False, "detail": "repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.release_list(repo, int(args.get("limit", 10)),
                                          timeout), ensure_ascii=False)

    if action == "release_view":
        tag = str(args.get("tag", "")).strip()
        if not repo or not tag:
            return json.dumps({"ok": False, "detail": "repo and tag required"},
                              ensure_ascii=False)
        return json.dumps(gh.release_view(repo, tag, timeout), ensure_ascii=False)

    # ── CLIENT: fork / PRs ───────────────────────────────────────
    if action == "fork":
        if not repo:
            return json.dumps({"ok": False, "detail": "repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.repo_fork(repo, bool(args.get("clone")), timeout),
                          ensure_ascii=False)

    if action == "pr_create":
        title = str(args.get("title", "")).strip()
        if not repo or not title:
            return json.dumps({"ok": False, "detail": "repo and title required"},
                              ensure_ascii=False)
        return json.dumps(gh.pr_create(
            repo, title, body=str(args.get("body", "")),
            base=str(args.get("base", "main")),
            head=str(args.get("head", "")), timeout=timeout),
            ensure_ascii=False)

    if action == "pr_list":
        if not repo:
            return json.dumps({"ok": False, "detail": "repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.pr_list(repo, str(args.get("state", "open")),
                                     timeout), ensure_ascii=False)

    if action in ("pr_view", "pr_merge", "pr_close", "pr_reopen",
                  "pr_checkout", "pr_diff"):
        number = str(args.get("number", "")).strip()
        if not repo or not number:
            return json.dumps({"ok": False, "detail": "repo and number required"},
                              ensure_ascii=False)
        if action == "pr_view":
            return json.dumps(gh.pr_view(repo, number, timeout), ensure_ascii=False)
        if action == "pr_merge":
            return json.dumps(gh.pr_merge(
                repo, number, str(args.get("method", "merge")),
                bool(args.get("delete_branch")), timeout), ensure_ascii=False)
        if action == "pr_close":
            return json.dumps(gh.pr_close(repo, number, timeout), ensure_ascii=False)
        if action == "pr_reopen":
            return json.dumps(gh.pr_reopen(repo, number, timeout), ensure_ascii=False)
        if action == "pr_checkout":
            return json.dumps(gh.pr_checkout(repo, number, timeout), ensure_ascii=False)
        if action == "pr_diff":
            return json.dumps(gh.pr_diff(repo, number, timeout), ensure_ascii=False)

    # ── issues / labels ──────────────────────────────────────────
    if action == "issue_create":
        title = str(args.get("title", "")).strip()
        if not repo or not title:
            return json.dumps({"ok": False, "detail": "repo and title required"},
                              ensure_ascii=False)
        return json.dumps(gh.issue_create(repo, title,
                                          str(args.get("body", "")), timeout),
                          ensure_ascii=False)

    if action == "issue_list":
        if not repo:
            return json.dumps({"ok": False, "detail": "repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.issue_list(repo, str(args.get("state", "open")),
                                        timeout), ensure_ascii=False)

    if action in ("issue_view", "issue_close", "issue_edit"):
        number = str(args.get("number", "")).strip()
        if not repo or not number:
            return json.dumps({"ok": False, "detail": "repo and number required"},
                              ensure_ascii=False)
        if action == "issue_view":
            return json.dumps(gh.issue_view(repo, number, timeout), ensure_ascii=False)
        if action == "issue_close":
            return json.dumps(gh.issue_close(repo, number, timeout), ensure_ascii=False)
        return json.dumps(gh.issue_edit(
            repo, number, title=str(args.get("title", "")),
            body=str(args.get("body", "")),
            labels=args.get("labels") or None, timeout=timeout),
            ensure_ascii=False)

    if action == "label_create":
        name = str(args.get("name", "")).strip()
        if not repo or not name:
            return json.dumps({"ok": False, "detail": "repo and name required"},
                              ensure_ascii=False)
        return json.dumps(gh.label_create(
            repo, name, color=str(args.get("color", "ededed")),
            description=str(args.get("description", "")), timeout=timeout),
            ensure_ascii=False)

    if action == "label_list":
        if not repo:
            return json.dumps({"ok": False, "detail": "repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.label_list(repo, timeout), ensure_ascii=False)

    # ── gists / search / workflows / secrets ─────────────────────
    if action == "gist":
        files = args.get("files") or []
        if not files:
            return json.dumps({"ok": False, "detail": "files required"},
                              ensure_ascii=False)
        return json.dumps(gh.gist_create(files,
                                         str(args.get("description", "")),
                                         timeout), ensure_ascii=False)

    if action == "search":
        query = str(args.get("query", "")).strip()
        if not query:
            return json.dumps({"ok": False, "detail": "query required"},
                              ensure_ascii=False)
        return json.dumps(gh.search_repos(query, int(args.get("limit", 10)),
                                          timeout), ensure_ascii=False)

    if action == "workflow_list":
        if not repo:
            return json.dumps({"ok": False, "detail": "repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.workflow_list(repo, timeout), ensure_ascii=False)

    if action == "run_list":
        if not repo:
            return json.dumps({"ok": False, "detail": "repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.run_list(repo, timeout), ensure_ascii=False)

    if action == "secret_list":
        if not repo:
            return json.dumps({"ok": False, "detail": "repo required"},
                              ensure_ascii=False)
        return json.dumps(gh.secret_list(repo, timeout), ensure_ascii=False)

    if action == "secret_set":
        name = str(args.get("name", "")).strip()
        value = str(args.get("value", "")).strip()
        if not repo or not name:
            return json.dumps({"ok": False, "detail": "repo and name required"},
                              ensure_ascii=False)
        return json.dumps(gh.secret_set(repo, name, value, timeout),
                          ensure_ascii=False)

    return json.dumps({"ok": False, "detail": f"unknown action: {action}"},
                      ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="github",
        description="GitHub — the FULL surface via the authenticated gh CLI. "
                    "HOST: repo_create/view/clone/push/pull/duplicate/delete/"
                    "archive/rename/edit/merge, release(_list/view). CLIENT: "
                    "fork, pr_create/list/view/merge/close/reopen/checkout/diff, "
                    "issue_create/list/view/close/edit, label_create/list, gist, "
                    "search, workflow_list, run_list, secret_list/set, api "
                    "(passthrough), rate_limit.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["status", "rate_limit", "api",
                                    "repo_create", "repo_view", "repo_clone",
                                    "repo_push", "repo_pull", "repo_duplicate",
                                    "repo_delete", "repo_archive", "repo_rename",
                                    "repo_edit", "repo_merge",
                                    "release", "release_list", "release_view",
                                    "fork", "pr_create", "pr_list", "pr_view",
                                    "pr_merge", "pr_close", "pr_reopen",
                                    "pr_checkout", "pr_diff",
                                    "issue_create", "issue_list", "issue_view",
                                    "issue_close", "issue_edit",
                                    "label_create", "label_list",
                                    "gist", "search", "workflow_list",
                                    "run_list", "secret_list", "secret_set"]},
                "repo": {"type": "string", "description": "owner/repo"},
                "name": {"type": "string", "description": "New repo/label name"},
                "source": {"type": "string", "description": "Source repo (duplicate)"},
                "new_name": {"type": "string"},
                "private": {"type": "boolean"},
                "description": {"type": "string"},
                "homepage": {"type": "string"},
                "topics": {"type": "array", "items": {"type": "string"}},
                "visibility": {"type": "string"},
                "confirm": {"type": "boolean", "description": "Confirm delete"},
                "archive": {"type": "boolean"},
                "dir": {"type": "string", "description": "Clone dir"},
                "remote": {"type": "string", "description": "Push/pull remote"},
                "branch": {"type": "string", "description": "Branch (merge/push/pull)"},
                "tag": {"type": "string", "description": "Release tag"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "base": {"type": "string", "description": "PR base branch"},
                "head": {"type": "string", "description": "PR head branch"},
                "number": {"type": "string", "description": "PR/issue number"},
                "method": {"type": "string", "enum": ["merge", "squash", "rebase"]},
                "delete_branch": {"type": "boolean"},
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "labels": {"type": "array", "items": {"type": "string"}},
                "color": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer"},
                "endpoint": {"type": "string", "description": "gh api endpoint"},
                "method2": {"type": "string", "description": "gh api HTTP method"},
                "value": {"type": "string", "description": "Secret value"},
            },
            "required": ["action"],
        },
        fn=_github,
    ))
    return ["github"]

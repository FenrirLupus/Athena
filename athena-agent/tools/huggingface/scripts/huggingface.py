"""Built-in huggingface tool — models & datasets (one tool).

The Operator's 08-12 spec: handle everything for HuggingFace — search
models/datasets, view info, download, upload. Uses the huggingface_hub
library (in Athena's venv). Credentials come from HF_TOKEN in .secret
when present (the operator's identity).
"""

import json
import os
import shutil
from pathlib import Path


def _hub():
    import huggingface_hub
    return huggingface_hub


def _hf_token() -> str:
    """The operator's HF token from .secret (never echoed)."""
    try:
        from core.secret_store import get_secret
        return get_secret("HF_TOKEN") or ""
    except Exception:
        return ""


def _models(args: dict, timeout: float = 30.0) -> str:
    query = str(args.get("query", "")).strip()
    limit = int(args.get("limit", 10))
    try:
        hf = _hub()
        api = hf.HfApi()
        results = api.list_models(search=query or None, limit=limit)
        out = [{"id": m.modelId,
                "downloads": getattr(m, "downloads", None),
                "likes": getattr(m, "likes", None),
                "tags": (getattr(m, "tags", None) or [])[:5]}
               for m in results]
        return json.dumps({"ok": True, "kind": "models", "results": out},
                          ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "detail": str(exc)[:200]},
                          ensure_ascii=False)


def _datasets(args: dict, timeout: float = 30.0) -> str:
    query = str(args.get("query", "")).strip()
    limit = int(args.get("limit", 10))
    try:
        hf = _hub()
        api = hf.HfApi()
        results = api.list_datasets(search=query or None, limit=limit)
        out = [{"id": d.id,
                "downloads": getattr(d, "downloads", None),
                "likes": getattr(d, "likes", None)}
               for d in results]
        return json.dumps({"ok": True, "kind": "datasets", "results": out},
                          ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "detail": str(exc)[:200]},
                          ensure_ascii=False)


def _info(args: dict, timeout: float = 30.0) -> str:
    repo = str(args.get("repo", "")).strip()
    kind = str(args.get("kind", "model")).strip()
    if not repo:
        return json.dumps({"ok": False, "detail": "repo required"},
                          ensure_ascii=False)
    try:
        hf = _hub()
        api = hf.HfApi()
        info = api.model_info(repo) if kind == "model" else api.dataset_info(repo)
        return json.dumps({
            "ok": True,
            "id": info.id,
            "sha": getattr(info, "sha", ""),
            "downloads": getattr(info, "downloads", None),
            "tags": (getattr(info, "tags", None) or [])[:10],
            "description": (getattr(info, "description", "") or "")[:300],
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "detail": str(exc)[:200]},
                          ensure_ascii=False)


def _download(args: dict, timeout: float = 120.0) -> str:
    repo = str(args.get("repo", "")).strip()
    kind = str(args.get("kind", "model")).strip()
    local_dir = str(args.get("dir", "")).strip()
    if not repo:
        return json.dumps({"ok": False, "detail": "repo required"},
                          ensure_ascii=False)
    # Default destination: the profile's workspace/models or datasets.
    if not local_dir:
        from core.config import ATHENA_ROOT
        base = ATHENA_ROOT / "profiles" / ".default" / "workspace"
        local_dir = str(base / ("models" if kind == "model" else "datasets"))
    Path(local_dir).mkdir(parents=True, exist_ok=True)
    try:
        hf = _hub()
        token = _hf_token()
        path = hf.snapshot_download(
            repo_id=repo, repo_type=kind, local_dir=local_dir,
            token=token or None)
        return json.dumps({"ok": True, "repo": repo, "local": str(path)},
                          ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "detail": str(exc)[:200]},
                          ensure_ascii=False)


def _upload(args: dict, timeout: float = 120.0) -> str:
    repo = str(args.get("repo", "")).strip()
    path = str(args.get("path", "")).strip()
    kind = str(args.get("kind", "model")).strip()
    if not repo or not path:
        return json.dumps({"ok": False, "detail": "repo and path required"},
                          ensure_ascii=False)
    p = Path(path).expanduser()
    if not p.exists():
        return json.dumps({"ok": False, "detail": f"not found: {path}"},
                          ensure_ascii=False)
    token = _hf_token()
    if not token:
        return json.dumps({"ok": False,
                           "detail": "HF_TOKEN not set in .secret (upload "
                                     "needs the operator's token)"},
                          ensure_ascii=False)
    try:
        hf = _hub()
        api = hf.HfApi()
        if p.is_dir():
            api.upload_folder(repo_id=repo, folder_path=str(p),
                              repo_type=kind, token=token)
        else:
            api.upload_file(repo_id=repo, path_or_fileobj=str(p),
                            path_in_repo=p.name, repo_type=kind, token=token)
        return json.dumps({"ok": True, "uploaded": path, "to": repo},
                          ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "detail": str(exc)[:200]},
                          ensure_ascii=False)


def _hf(args: dict, timeout: float = 30.0) -> str:
    action = str(args.get("action", "")).strip()
    if action == "models":
        return _models(args, timeout)
    if action == "datasets":
        return _datasets(args, timeout)
    if action == "info":
        return _info(args, timeout)
    if action == "download":
        return _download(args, timeout)
    if action == "upload":
        return _upload(args, timeout)
    return json.dumps({"ok": False, "detail": f"unknown action: {action}"},
                      ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="huggingface",
        description="HuggingFace (the Operator's 08-12 spec): models, datasets, "
                    "info, download, upload — via huggingface_hub. Upload "
                    "needs HF_TOKEN in .secret.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["models", "datasets", "info",
                                    "download", "upload"]},
                "query": {"type": "string", "description": "Search query"},
                "repo": {"type": "string", "description": "repo id (owner/name)"},
                "kind": {"type": "string",
                         "enum": ["model", "dataset"],
                         "description": "Repo type"},
                "path": {"type": "string", "description": "Local path to upload"},
                "dir": {"type": "string", "description": "Download dir"},
                "limit": {"type": "integer"},
            },
            "required": ["action"],
        },
        fn=_hf,
    ))
    return ["huggingface"]

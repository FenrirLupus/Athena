---
name: huggingface
description: "HuggingFace — models, datasets, info, download, upload."
---

# HuggingFace

The **huggingface** tool handles everything for HuggingFace (the Operator's
08-12 spec): search models and datasets, view repo info, download repos
locally, and upload the operator's own models/datasets. Uses the
`huggingface_hub` library (in Athena's venv).

## Tools

- `models` — search models
- `datasets` — search datasets
- `info` — repo details (id, sha, downloads, tags)
- `download` — download a repo (models → workspace/models, datasets →
  workspace/datasets)
- `upload` — upload a local file/folder (needs HF_TOKEN in .secret)

## Usage

```
huggingface {"action": "models", "query": "qwen vision"}
huggingface {"action": "datasets", "query": "code"}
huggingface {"action": "info", "repo": "Qwen/Qwen2.5-VL-7B", "kind": "model"}
huggingface {"action": "download", "repo": "Qwen/Qwen2.5-VL-7B", "kind": "model"}
huggingface {"action": "upload", "repo": "owner/repo", "path": "model.safetensors", "kind": "model"}
```

## When to use

- The operator wants to find/download a model or dataset.
- The operator wants to publish their own model/dataset.

## Requirements (credentials)

- **`models` / `datasets` / `info` / `download`** — keyless (the free
  HuggingFace Hub API; downloads need no token for public repos).
- **`upload`** — REQUIRES `HF_TOKEN` in `.secret` (the operator's
  HuggingFace token). Without it: "HF_TOKEN not set in .secret
  (upload needs the operator's token)".
- Check first: try `info` on a known repo — if it succeeds, the Hub is
  reachable; upload additionally needs the token.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/huggingface.py` — registers `huggingface`.

---
---

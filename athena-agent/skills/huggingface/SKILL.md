---
name: huggingface
description: "Use the built-in huggingface tool — models, datasets, info, download, upload."
---

# HuggingFace

The built-in `huggingface` tool handles everything for HuggingFace:

```
huggingface {"action": "models", "query": "qwen vision"}
huggingface {"action": "datasets", "query": "code"}
huggingface {"action": "info", "repo": "Qwen/Qwen2.5-VL-7B", "kind": "model"}
huggingface {"action": "download", "repo": "Qwen/Qwen2.5-VL-7B", "kind": "model"}
huggingface {"action": "upload", "repo": "owner/repo", "path": "model.safetensors", "kind": "model"}
```

Downloads go to the profile's workspace (models/ or datasets/). Uploads
need HF_TOKEN in .secret. Use when the operator wants to find,
download, or publish models/datasets.

**Requirements:** search/info/download are keyless. **upload** requires
`HF_TOKEN` in `.secret` (the operator's HuggingFace token).

---
---

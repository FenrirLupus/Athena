"""The models folder — Hugging Face downloads that Athena can serve locally.

Layout (documentation/architecture/17-providers-and-models.md):

    ~/.athena/models/<name>/
        MODEL.md            frontmatter manifest: repo, file(s), quant, sha256, size, downloaded
        <file>.gguf         the weights
        <file>-00002-of-00003.gguf   split quants keep their parts

Why a folder and not a Hugging Face cache: the cache is keyed by commit hash, shared with every other
tool on the machine, and pruned by its own rules. A model Athena is *configured to run* must be a file
with a name she can point a server at, and a manifest that says where it came from — otherwise "which
quant is running" is unanswerable after the first `hf` cache clean.

No dependency on `huggingface_hub`: a public GGUF is one HTTPS GET, and the agent's venv stays the two
libraries it actually needs. A token (for gated repos) comes from the environment, never from config.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import httpx
from core import timefmt, trace
from core.root import athena_root

HF = "https://huggingface.co"
DEFAULTS = athena_root() / "models"


def models_dir() -> Path:
    d = Path(os.environ.get("ATHENA_MODELS") or DEFAULTS)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _token() -> str:
    for env in ("ATHENA_HF_TOKEN", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        val = os.environ.get(env)
        if val and val.strip():
            return val.strip()
    return ""


def slug(repo: str, name: str = "") -> str:
    """A directory name a human can type: `bartowski/SmolLM2-135M-Instruct-GGUF` -> `smollm2-135m-instruct`."""
    if name:
        return re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower()).strip("-")
    tail = repo.split("/")[-1]
    tail = re.sub(r"(?i)-gguf$", "", tail)
    return re.sub(r"[^a-z0-9._-]+", "-", tail.lower()).strip("-") or "model"


# -- repo inspection -------------------------------------------------------

def list_repo_files(repo: str, token: str = "") -> list[dict]:
    """Every file in a repo, via the public API (name + size)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(f"{HF}/api/models/{repo}", headers=headers)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"cannot reach huggingface.co: {exc}") from exc
    if resp.status_code == 401 or resp.status_code == 403:
        raise RuntimeError(
            f"{repo}: access denied — gated repos need ATHENA_HF_TOKEN/HF_TOKEN set in the environment")
    if resp.status_code == 404:
        raise RuntimeError(f"{repo}: not found on huggingface.co")
    if resp.status_code >= 400:
        raise RuntimeError(f"{repo}: HTTP {resp.status_code}")
    data = resp.json()
    return [{"name": s.get("rfilename", ""), "size": s.get("size")}
            for s in (data.get("siblings") or []) if s.get("rfilename")]


# What a native (safetensors) model needs to load: weights, config, tokenizer, chat template.
_NATIVE_SUFFIXES = (".safetensors", ".json", ".jinja", ".txt", ".model", ".tiktoken")
_NATIVE_SKIP = ("readme", "license", ".gitattributes", ".md", ".png", ".jpg", ".mp4", ".gif",
                "preprocessor", "video_")


def _native_files(repo: str, token: str) -> list[str]:
    everything = [f["name"] for f in list_repo_files(repo, token)]
    keep = []
    for name in everything:
        low = name.lower()
        if any(skip in low for skip in _NATIVE_SKIP):
            continue
        if low.endswith(_NATIVE_SUFFIXES):
            keep.append(name)
    if not any(n.endswith(".safetensors") for n in keep):
        raise RuntimeError(f"{repo}: no .gguf and no .safetensors — not a runnable model repo")
    return keep


def pick_files(repo: str, file: str = "", token: str = "") -> list[str]:
    """What to fetch: an explicit file, a GGUF quant (best one, parts if split), or a whole native repo.

    No GGUF is required. A safetensors repo is the model as its authors shipped it, so Athena downloads
    it whole and lets vLLM load it directly — a quant is a choice, not a prerequisite (doc 17).
    """
    if file:
        return [file]

    everything = [f["name"] for f in list_repo_files(repo, token)]
    # A repo can ship both — `FenrirLupus/Qwen-3.5-4B-A90-R10-Heretic` carries safetensors *and* a
    # Quantization/ folder of GGUFs. The base model is what "download this model" means, so safetensors
    # win; a quant is opt-in by naming the file. Checking GGUF first silently downloaded a Q2/Q4 instead.
    if any(name.lower().endswith(".safetensors") for name in everything):
        return _native_files(repo, token)

    files = [name for name in everything if name.lower().endswith(".gguf")]
    if not files:
        raise RuntimeError(f"{repo}: no .safetensors and no .gguf — not a runnable model repo")

    # Prefer a mid quant: it is the one that fits a small card and still reads well.
    preference = ("q4_k_m", "q4_k_s", "q5_k_m", "q5_k_s", "q6_k", "q8_0", "q4_0", "q3_k_m")
    single = [f for f in files if "of-0000" not in f.lower()] or files
    base = single[0]
    for want in preference:
        hit = [f for f in single if want in f.lower()]
        if hit:
            base = sorted(hit, key=len)[0]
            break
    stem = re.sub(r"-\d{5}-of-\d{5}", "", base.rsplit(".gguf", 1)[0])
    parts = sorted(f for f in files if stem in f and "of-" in f.lower())
    return parts or [base]


# -- transfer --------------------------------------------------------------

def _fetch(repo: str, file: str, dest: Path, token: str = "") -> dict:
    """Streamed, resumable download with a `.part` file and a final atomic rename."""
    url = f"{HF}/{repo}/resolve/main/{file}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have:
        headers["Range"] = f"bytes={have}-"

    sha = hashlib.sha256()
    if have and dest.exists():
        part = dest                       # already complete, just hash it
        with open(part, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                sha.update(chunk)
        size = part.stat().st_size
        trace.event("model.fetch", level="debug", repo=repo, file=file, bytes=size, resumed=True)
        return {"file": file, "bytes": size, "sha256": sha.hexdigest()}

    expected = None
    with httpx.Client(timeout=None, follow_redirects=True) as client:
        with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f"{repo}/{file}: HTTP {resp.status_code}")
            mode = "ab" if have and resp.status_code == 206 else "wb"
            if mode == "wb":
                have = 0
            declared = resp.headers.get("content-length") or ""
            if declared.isdigit() and resp.headers.get("content-encoding", "identity") == "identity":
                expected = int(declared) + have      # 206 declares the remainder, not the whole file
            with open(part, mode) as fh:
                for chunk in resp.iter_bytes(1 << 20):
                    fh.write(chunk)
    os.replace(part, dest)
    actual = dest.stat().st_size
    if expected is not None and actual != expected:
        # a clean close at the wrong length is the failure mode that looks like success: the file exists, is
        # truncated, and only fails much later as a corrupt load. Refuse it here, and keep the bytes for resume.
        trace.failure("model.integrity.refused", repo=repo, file=file, expected=expected, actual=actual,
                      dest=str(dest))
        raise RuntimeError(f"{repo}/{file}: integrity check refused — {actual} of {expected} bytes arrived")
    with open(dest, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha.update(chunk)
    trace.event("model.fetch", level="debug", repo=repo, file=file, bytes=actual,
                resumed=bool(have), expected=expected)
    return {"file": file, "bytes": actual, "sha256": sha.hexdigest()}


# -- manifests -------------------------------------------------------------

MANIFEST = "MODEL.md"


def write_manifest(target: Path, meta: dict) -> Path:
    lines = ["---"]
    for key in ("name", "repo", "files", "quant", "bytes", "sha256", "downloaded", "window", "chat_template"):
        if key in meta and meta[key] not in (None, "", []):
            val = meta[key]
            lines.append(f"{key}: {json.dumps(val) if isinstance(val, (list, int)) else val}")
    lines += ["---", "", f"# {meta.get('name', target.name)}", ""]
    lines.append(f"Downloaded from `{meta.get('repo','')}` into `{target}`.")
    lines.append("")
    lines.append("Serve it with the `openai` format — a local model is just a base URL:")
    lines.append("")
    lines.append("```yaml")
    lines.append("model:")
    lines.append("  provider: openai")
    lines.append(f"  name: {meta.get('name','')}")
    lines.append("  local:")
    lines.append("    enabled: true")
    lines.append(f"    name: {meta.get('name','')}")
    lines.append("```")
    path = target / MANIFEST
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_manifest(path: Path) -> dict:
    """Parse MODEL.md frontmatter without a YAML dependency on the file's body."""
    try:
        text = (path / MANIFEST).read_text(encoding="utf-8")
    except OSError:
        return {}
    meta: dict = {}
    if not text.startswith("---"):
        return meta
    body = text.split("---", 2)[1]
    for line in body.splitlines():
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        try:
            meta[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            meta[key.strip()] = raw
    return meta


def list_models() -> list[dict]:
    out = []
    for d in sorted(models_dir().iterdir()):
        if not d.is_dir():
            continue
        meta = read_manifest(d)
        ggufs = sorted(p.name for p in d.glob("*.gguf"))
        native = sorted(p.name for p in d.glob("*.safetensors"))
        out.append({"name": meta.get("name") or d.name, "dir": str(d),
                    "repo": meta.get("repo", ""),
                    "quant": meta.get("quant", "") or ("native" if native else ""),
                    "format": "gguf" if ggufs else ("safetensors" if native else "?"),
                    "files": ggufs or native, "bytes": meta.get("bytes"),
                    "downloaded": meta.get("downloaded", "")})
    return out


def _artifact(directory: Path) -> Path:
    """What to hand a backend: the GGUF when there is one, else the directory of a native model."""
    ggufs = sorted(directory.glob("*.gguf"))
    if ggufs:
        return ggufs[0]
    if any(directory.glob("*.safetensors")) or (directory / "config.json").exists():
        return directory
    trace.event("model.missing", level="error", model=directory.name, path=str(directory),
                reason="no weights — neither a .gguf nor safetensors in the folder")
    raise RuntimeError(f"{directory} holds neither a .gguf nor safetensors weights")


def resolve(name: str = "", repo: str = "", file: str = "") -> Path:
    """Find a downloaded model's file (GGUF) or directory (safetensors) by name or repo."""
    root = models_dir()
    present = sorted(d.name for d in root.iterdir() if d.is_dir()) if root.exists() else []
    if name:
        cand = root / slug(name)
        if not cand.exists() and name.startswith(str(root)):
            cand = Path(name)
        if cand.exists():
            artifact = _artifact(cand)
            trace.event("model.resolve", level="debug", model=name, by="name", path=str(artifact),
                        root=str(root))
            return artifact
        trace.event("model.missing", level="error", model=name, root=str(root), by="name",
                    searched=[str(cand)], present=present[:20], present_count=len(present))
        raise RuntimeError(f"model {name!r} is not in {root} — run: athena models download {repo or '<repo>'}")
    if repo:
        cand = root / slug(repo)
        if cand.exists():
            ggufs = sorted(cand.glob("*.gguf"))
            if ggufs:
                trace.event("model.resolve", level="debug", repo=repo, by="repo", path=str(ggufs[0]),
                            root=str(root), note="GGUF only — a safetensors folder resolves to its directory")
                return ggufs[0]
    if file and Path(file).exists():
        trace.event("model.resolve", level="debug", file=file, by="path", path=str(Path(file)))
        return Path(file)
    found = [d for d in root.iterdir()
             if d.is_dir() and (list(d.glob("*.gguf")) or list(d.glob("*.safetensors")))]
    if len(found) == 1:
        artifact = _artifact(found[0])
        trace.event("model.resolve", level="debug", by="only-model", model=found[0].name,
                    path=str(artifact), root=str(root))
        return artifact
    trace.event("model.missing", level="error", by="any", root=str(root),
                candidates=len(found), present=present[:20],
                reason="ambiguous — name the one to run")
    raise RuntimeError(f"models/ holds {len(found)} models — name the one to run (model.local.name)")


def download(repo: str, file: str = "", name: str = "", token: str = "") -> dict:
    """Fetch a repo (or one file of it) into models/<name>/ and write its manifest."""
    if not repo:
        trace.event("model.download.refused", level="error", model=name, reason="no repo given")
        raise RuntimeError("a Hugging Face repo is required, e.g. Qwen/Qwen2.5-3B-Instruct-GGUF")
    token = token or _token()
    target = models_dir() / slug(repo, name)
    results = []
    with trace.span("model.download", level="info", repo=repo, target=str(target),
                    by_file=file or "-") as sp:
        wanted = pick_files(repo, file, token)
        sp.fields["files"] = len(wanted)
        for f in wanted:
            sp.fields["file"] = f
            dest = target / Path(f).name
            results.append(_fetch(repo, f, dest, token))
        sp.fields.pop("file", None)

        total = sum(r["bytes"] for r in results)
        quant = ""
        m = re.search(r"(?i)\b(q\d[_a-z0-9]*|f16|f32|bf16|iq\d[_a-z0-9]*)\b",
                      " ".join(r["file"] for r in results))
        if m:
            quant = m.group(1).upper()
        native = all(f.endswith(".safetensors") or not f.endswith(".gguf") for f in wanted)
        meta = {"name": slug(repo, name), "repo": repo, "format": "native" if native else "gguf",
                "files": [r["file"] for r in results],
                "quant": quant, "bytes": total,
                "sha256": results[0]["sha256"] if len(results) == 1 else "",
                "downloaded": timefmt.stamp()}
        write_manifest(target, meta)
        meta["dir"] = str(target)
        sp.ok(bytes=total, quant=quant or "-", format=meta["format"], files=len(wanted))
    return meta


def remove(name: str) -> str:
    """Delete a downloaded model. Returns the directory removed (an operator action, never automatic)."""
    target = models_dir() / slug(name)
    if not target.exists():
        raise RuntimeError(f"model {name!r} is not in {models_dir()}")
    shutil.rmtree(target)
    return str(target)

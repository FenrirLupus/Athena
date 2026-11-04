"""References — the added information a capability brings with it, and the reader for it.

The three layers use two supporting folders, and the difference is what they are *for*:

    references/   information to read: specs, examples, tables of values, "how this works" notes
    assets/       data to use at runtime: templates, fixtures, a JSON blob the code loads

A **skill** has references (it is prose; the references are the rest of the prose).
A **tool** has scripts (what it runs) and references (what you need to know to use it well).
A **plugin** has scripts, references, and assets (a program needs files, not just facts).

Nothing here executes. A reference is text — which also means it is the one supporting folder an agent may safely
read for any capability, without that reading being a capability of its own.
"""
from __future__ import annotations

from pathlib import Path

LAYERS = ("skill", "tool", "plugin")
MAX_READ = 60000


class ReferenceError(ValueError):
    pass


def roots(layer: str, name: str, profile_root: Path | None = None,
          athena_root: Path | None = None) -> list[Path]:
    """Where this capability's folder lives: the profile first, then the shared directory."""
    if layer not in LAYERS:
        raise ReferenceError(f"unknown layer {layer!r}; expected one of {', '.join(LAYERS)}")
    folder_name = {"skill": "skills", "tool": "tools", "plugin": "plugins"}[layer]
    found = []
    if profile_root is not None:
        found.append(Path(profile_root) / folder_name / name)
    if athena_root is not None:
        found.append(Path(athena_root) / folder_name / name)
    return [p for p in found if p.is_dir()]


def _folder(layer: str, name: str, profile_root, athena_root) -> Path:
    for candidate in roots(layer, name, profile_root, athena_root):
        return candidate
    raise ReferenceError(f"no {layer} named {name!r}")


def list_for(layer: str, name: str, profile_root=None, athena_root=None) -> dict:
    """What a capability carries beside its descriptor."""
    folder = _folder(layer, name, profile_root, athena_root)
    references = folder / "references"
    assets = folder / "assets"
    return {
        "layer": layer,
        "name": name,
        "dir": str(folder),
        "references": sorted(str(p.relative_to(references)) for p in references.rglob("*") if p.is_file())
        if references.is_dir() else [],
        "assets": sorted(str(p.relative_to(assets)) for p in assets.rglob("*") if p.is_file())
        if assets.is_dir() else [],
        "scripts": sorted(p.name for p in (folder / "scripts").iterdir() if p.is_file())
        if (folder / "scripts").is_dir() else [],
    }


def inventory(profile_root: Path | None = None, athena_root: Path | None = None) -> list[dict]:
    """Every capability that carries references, across all three layers."""
    rows = []
    for layer, folder_name in (("skill", "skills"), ("tool", "tools"), ("plugin", "plugins")):
        for base in ([Path(profile_root) / folder_name] if profile_root else []) + \
                    ([Path(athena_root) / folder_name] if athena_root else []):
            if not base.is_dir():
                continue
            for folder in sorted(base.iterdir()):
                if not folder.is_dir() or folder.name.startswith("."):
                    continue
                references = folder / "references"
                if not references.is_dir():
                    continue
                files = sorted(str(p.relative_to(references)) for p in references.rglob("*") if p.is_file())
                if files:
                    rows.append({"layer": layer, "name": folder.name, "references": files,
                                 "origin": "profile" if profile_root and str(base).startswith(str(profile_root))
                                 else "shared"})
    return rows


def read(layer: str, name: str, path: str, profile_root=None, athena_root=None,
         max_chars: int = 20000) -> dict:
    """Read one reference file. The path must land inside that capability's `references/` folder."""
    folder = _folder(layer, name, profile_root, athena_root)
    base = (folder / "references").resolve()
    if not base.is_dir():
        raise ReferenceError(f"{layer} {name!r} has no references/ folder")
    candidate = (base / str(path or "")).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        # a reference reader is not a file reader: `reference_read("tool","x","../../SKILL.md")` must not work
        raise ReferenceError(f"{path!r} escapes {layer} {name!r}'s references/ — pick a file from the list") from None
    if not candidate.is_file():
        known = [str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()]
        raise ReferenceError(f"no reference {path!r}; available: {', '.join(known) or 'none'}")
    text = candidate.read_text(encoding="utf-8", errors="replace")
    cap = max(500, min(int(max_chars), MAX_READ))
    return {"layer": layer, "name": name, "path": str(candidate), "bytes": candidate.stat().st_size,
            "content": text[:cap] + ("\n[truncated]" if len(text) > cap else "")}

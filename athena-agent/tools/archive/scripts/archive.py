"""Built-in archive tool — zip/unzip/list compressed files (one tool).

The Operator's 08-12 spec: an archive tool for handling ZIP and other
compressed files (tar, gz, bz2, xz). Uses Python's stdlib — no
external deps. Safe: extraction paths are confined to the destination
directory (no zip-slip).
"""

import json
import shutil
import tarfile
import zipfile
from pathlib import Path


def _safe_join(dest: Path, member: str) -> Path:
    """Resolve a member path inside dest, refusing traversal."""
    target = (dest / member).resolve()
    dest_res = dest.resolve()
    if not str(target).startswith(str(dest_res)):
        raise ValueError(f"unsafe path in archive: {member}")
    return target


def _list(args: dict, timeout: float = 30.0) -> str:
    path = str(args.get("path", "")).strip()
    if not path:
        return json.dumps({"ok": False, "detail": "path required"},
                          ensure_ascii=False)
    p = Path(path).expanduser()
    if not p.is_file():
        return json.dumps({"ok": False, "detail": f"not found: {path}"},
                          ensure_ascii=False)
    try:
        if zipfile.is_zipfile(p):
            with zipfile.ZipFile(p) as z:
                names = z.namelist()
                return json.dumps({"ok": True, "format": "zip",
                                   "entries": len(names), "files": names[:200]},
                                  ensure_ascii=False)
        if tarfile.is_tarfile(p):
            with tarfile.open(p) as t:
                names = t.getnames()
                return json.dumps({"ok": True, "format": "tar",
                                   "entries": len(names), "files": names[:200]},
                                  ensure_ascii=False)
        return json.dumps({"ok": False, "detail": "not a supported archive"},
                          ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False)


def _extract(args: dict, timeout: float = 60.0) -> str:
    path = str(args.get("path", "")).strip()
    dest = str(args.get("dest", "")).strip() or "."
    if not path:
        return json.dumps({"ok": False, "detail": "path required"},
                          ensure_ascii=False)
    p = Path(path).expanduser()
    d = Path(dest).expanduser()
    if not p.is_file():
        return json.dumps({"ok": False, "detail": f"not found: {path}"},
                          ensure_ascii=False)
    d.mkdir(parents=True, exist_ok=True)
    try:
        if zipfile.is_zipfile(p):
            with zipfile.ZipFile(p) as z:
                for member in z.namelist():
                    target = _safe_join(d, member)
                    if member.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(member) as src, open(target, "wb") as out:
                            shutil.copyfileobj(src, out)
                count = len(z.namelist())
        elif tarfile.is_tarfile(p):
            with tarfile.open(p) as t:
                for member in t.getmembers():
                    target = _safe_join(d, member.name)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        f = t.extractfile(member)
                        if f:
                            with open(target, "wb") as out:
                                shutil.copyfileobj(f, out)
                count = len(t.getmembers())
        else:
            return json.dumps({"ok": False, "detail": "not a supported archive"},
                              ensure_ascii=False)
        return json.dumps({"ok": True, "extracted": count, "dest": str(d)},
                          ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False)


def _create(args: dict, timeout: float = 60.0) -> str:
    path = str(args.get("path", "")).strip()
    files = args.get("files") or []
    if not path or not files:
        return json.dumps({"ok": False, "detail": "path and files required"},
                          ensure_ascii=False)
    p = Path(path).expanduser()
    try:
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                src = Path(str(f)).expanduser()
                if not src.exists():
                    return json.dumps({"ok": False,
                                       "detail": f"not found: {src}"},
                                      ensure_ascii=False)
                z.write(src, src.name)
        return json.dumps({"ok": True, "created": str(p), "files": len(files)},
                          ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    for name, desc, fn, props, req in (
        ("archive_list", "List the contents of a zip/tar archive.", _list,
         {"path": {"type": "string"}}, ["path"]),
        ("archive_extract", "Extract an archive into a directory (safe).", _extract,
         {"path": {"type": "string"}, "dest": {"type": "string"}}, ["path"]),
        ("archive_create", "Create a zip from files.", _create,
         {"path": {"type": "string"}, "files": {"type": "array",
                                                "items": {"type": "string"}}},
         ["path", "files"]),
    ):
        register(Tool(
            name=name,
            description=desc,
            parameters={"type": "object", "properties": props,
                        "required": req},
            fn=fn,
        ))
    return ["archive_list", "archive_extract", "archive_create"]

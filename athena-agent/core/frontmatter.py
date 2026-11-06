"""Frontmatter parsing for documents that carry a `---` header.

Three readers existed and disagreed: one handled YAML block scalars (`description: >-`), one parsed typed values
(JSON, numbers, booleans) and one handled neither, so the same file could mean different things depending on which
loader opened it. This is the one parser; `typed` is the only difference the callers ever had.
"""
from __future__ import annotations

import json
from pathlib import Path

__all__ = ["parse", "read", "read_text"]


def _typed(raw: str):
    """Turn a scalar into the value its syntax implies — the same rules the toolpack loader always used."""
    text = raw.strip()
    if not text:
        return ""
    if text[0] in "{[":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        return text


def parse(text: str, *, typed: bool = False) -> tuple[dict, str]:
    """Split a `---` header from the body. Keys and scalars only; no nested YAML."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    head, body = text[3:end], text[end + 4:]
    meta: dict = {}
    pending_key = None
    for raw in head.splitlines():
        stripped = raw.strip()
        if pending_key is not None:
            # a block scalar: `description: >-` followed by indented lines. Without this every document written
            # with a folded description reports its description as the literal ">-".
            if raw.startswith((" ", "\t")) and stripped:
                meta[pending_key] = (str(meta.get(pending_key) or "") + " " + stripped).strip()
                continue
            pending_key = None
        if ":" not in raw or stripped.startswith("#"):
            continue
        key, _, value = raw.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if value in (">", ">+", ">-", "|", "|+", "|-"):
            meta[key] = ""
            pending_key = key
            continue
        meta[key] = _typed(value) if typed else value.strip('"').strip("'")
    return meta, body.lstrip("\n")


def read_text(path) -> str:
    """The file's text, or empty when it cannot be read — a missing manifest is not an error here."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def read(path, *, typed: bool = False) -> tuple[dict, str]:
    """Split the header from the body of a file."""
    return parse(read_text(path), typed=typed)
